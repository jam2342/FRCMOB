from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any

import redis
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.services.events.pipeline import run_event_pipeline, get_queue
from app.core.config import settings
from app.db import models
from app.services.ml.synergy import QUALITY_THRESHOLD_DEFAULT, SYNERGY_MODEL_VERSION

logger = logging.getLogger(__name__)

FRESHNESS_RECOVERY_LAST_RESULT_KEY = "maintenance:freshness_recovery:last_result"
FRESHNESS_RECOVERY_LAST_RUN_TS_KEY = "maintenance:freshness_recovery:last_run_ts"


def _safe_age_hours(now_utc: datetime, unix_ts: int | None) -> float | None:
    if not isinstance(unix_ts, int) or unix_ts <= 0:
        return None
    age = max(0.0, now_utc.timestamp() - float(unix_ts))
    return round(age / 3600.0, 3)


def _normalize_event_key(event_key: str) -> str:
    return str(event_key or "").strip().lower()


def _redis_conn() -> redis.Redis:
    return redis.from_url(settings.redis_url)


def _persist_last_result(result_payload: dict[str, Any]) -> None:
    try:
        conn = _redis_conn()
        now_ts = datetime.now(timezone.utc).timestamp()
        conn.set(FRESHNESS_RECOVERY_LAST_RUN_TS_KEY, str(now_ts))
        conn.set(FRESHNESS_RECOVERY_LAST_RESULT_KEY, json.dumps(result_payload, default=str))
    except Exception as exc:
        logger.warning("Failed to persist freshness recovery summary: %s", exc)


def load_last_result() -> dict[str, Any] | None:
    try:
        conn = _redis_conn()
        raw = conn.get(FRESHNESS_RECOVERY_LAST_RESULT_KEY)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        if not isinstance(raw, str) or not raw.strip():
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"raw": raw}
    except Exception:
        return None


def build_event_team_freshness_rows(
    db: Session,
    event_key: str,
    *,
    stale_hours_threshold: int | None = None,
    now_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    normalized_event_key = _normalize_event_key(event_key)
    threshold_hours = int(stale_hours_threshold or settings.freshness_sla_stale_hours)
    current_now = now_utc or datetime.now(timezone.utc)

    team_rows = (
        db.query(models.EventTeam.team_key, models.Team.team_number, models.Team.nickname)
        .join(models.Team, models.Team.team_key == models.EventTeam.team_key)
        .filter(models.EventTeam.event_key == normalized_event_key)
        .order_by(models.Team.team_number.asc())
        .all()
    )
    finding_rows = (
        db.query(
            models.TeamMatchFinding.team_key,
            func.count(models.TeamMatchFinding.id).label("finding_count"),
            func.max(models.Match.time).label("latest_match_time"),
        )
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .filter(models.TeamMatchFinding.event_key == normalized_event_key)
        .group_by(models.TeamMatchFinding.team_key)
        .all()
    )
    by_team = {
        str(team_key): (
            int(finding_count or 0),
            int(latest_match_time) if isinstance(latest_match_time, int) else None,
        )
        for team_key, finding_count, latest_match_time in finding_rows
    }

    payload: list[dict[str, Any]] = []
    for team_key, team_number, nickname in team_rows:
        finding_count, latest_match_time = by_team.get(str(team_key), (0, None))
        age_hours = _safe_age_hours(current_now, latest_match_time)
        if finding_count <= 0:
            status = "missing"
        elif isinstance(age_hours, float) and age_hours > threshold_hours:
            status = "stale"
        else:
            status = "fresh"
        payload.append(
            {
                "team_key": str(team_key),
                "team_number": int(team_number or 0),
                "nickname": nickname,
                "finding_count": int(finding_count),
                "latest_match_time": latest_match_time,
                "age_hours": age_hours,
                "status": status,
            }
        )
    return payload


def _target_team_keys(
    team_rows: list[dict[str, Any]],
    *,
    max_target_teams: int | None = None,
) -> set[str]:
    candidates = [
        row
        for row in team_rows
        if str(row.get("status") or "").lower() in {"missing", "stale"} and isinstance(row.get("team_key"), str)
    ]
    candidates.sort(
        key=lambda row: (
            0 if str(row.get("status")).lower() == "missing" else 1,
            int(row.get("team_number") or 0),
            str(row.get("team_key") or ""),
        )
    )
    if isinstance(max_target_teams, int) and max_target_teams > 0:
        candidates = candidates[:max_target_teams]
    return {str(row["team_key"]).strip().lower() for row in candidates if str(row.get("team_key") or "").strip()}


def recover_event_freshness(
    db: Session,
    *,
    event_key: str,
    stale_hours_threshold: int | None = None,
    force_analysis: bool = False,
    require_video: bool = True,
    require_calibration: bool = True,
    run_post_compute: bool = True,
    model_version: str = SYNERGY_MODEL_VERSION,
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    max_target_teams: int | None = None,
) -> dict[str, Any]:
    normalized_event_key = _normalize_event_key(event_key)
    threshold_hours = int(stale_hours_threshold or settings.freshness_sla_stale_hours)

    event = db.get(models.Event, normalized_event_key)
    if event is None:
        return {
            "ok": False,
            "status": "event_not_found",
            "event_key": normalized_event_key,
            "stale_hours_threshold": threshold_hours,
        }

    freshness_rows = build_event_team_freshness_rows(
        db,
        normalized_event_key,
        stale_hours_threshold=threshold_hours,
    )
    target_team_keys = _target_team_keys(freshness_rows, max_target_teams=max_target_teams)

    freshness_summary = {
        "teams": len(freshness_rows),
        "fresh": sum(1 for row in freshness_rows if row["status"] == "fresh"),
        "stale": sum(1 for row in freshness_rows if row["status"] == "stale"),
        "missing": sum(1 for row in freshness_rows if row["status"] == "missing"),
        "targeted": len(target_team_keys),
    }

    if not target_team_keys:
        return {
            "ok": True,
            "status": "no_target_teams",
            "event_key": normalized_event_key,
            "event_name": event.name,
            "stale_hours_threshold": threshold_hours,
            "freshness_summary": freshness_summary,
            "analysis": {
                "scheduled": 0,
                "skipped": 0,
                "blocked": 0,
                "scheduled_matches": [],
                "skipped_matches": [],
                "blocked_matches": [],
            },
        }

    queue = get_queue()
    pipeline_result = run_event_pipeline(
        db,
        queue,
        event_key=normalized_event_key,
        force_analysis=bool(force_analysis),
        allowed_team_keys=target_team_keys,
        clone_event_calibration=False,
        require_video=bool(require_video),
        require_calibration=bool(require_calibration),
        run_post_compute=bool(run_post_compute),
        synergy_model_version=model_version,
        quality_threshold=float(quality_threshold),
        auto_calibrate_missing=False,
        auto_calibration_overwrite_existing=False,
        auto_calibration_refresh_video=False,
        auto_calibration_sample_count=18,
        auto_calibration_min_inliers=4,
        auto_calibration_ransac_reproj_threshold_px=3.0,
        auto_calibration_focus_time_sec=None,
    )
    analysis_block = pipeline_result.get("analysis") if isinstance(pipeline_result, dict) else {}
    return {
        "ok": True,
        "status": str(pipeline_result.get("status") or "ran"),
        "event_key": normalized_event_key,
        "event_name": event.name,
        "stale_hours_threshold": threshold_hours,
        "freshness_summary": freshness_summary,
        "target_team_keys": sorted(target_team_keys),
        "analysis": {
            "scheduled": len(analysis_block.get("scheduled") or []),
            "skipped": len(analysis_block.get("skipped") or []),
            "blocked": len(analysis_block.get("blocked") or []),
            "scheduled_matches": analysis_block.get("scheduled") or [],
            "skipped_matches": analysis_block.get("skipped") or [],
            "blocked_matches": analysis_block.get("blocked") or [],
        },
        "pipeline": pipeline_result,
    }


def select_recovery_candidate_events(
    db: Session,
    *,
    stale_hours_threshold: int | None = None,
    max_events: int = 3,
) -> list[dict[str, Any]]:
    threshold_hours = int(stale_hours_threshold or settings.freshness_sla_stale_hours)
    capped_max_events = max(1, min(int(max_events), 50))
    year_floor = datetime.now(timezone.utc).year - 1

    event_rows = (
        db.query(
            models.Event.event_key,
            models.Event.name,
            models.Event.year,
            func.count(models.EventTeam.team_key).label("team_count"),
        )
        .join(models.EventTeam, models.EventTeam.event_key == models.Event.event_key)
        .filter(models.Event.year >= year_floor)
        .group_by(models.Event.event_key, models.Event.name, models.Event.year)
        .all()
    )

    candidates: list[dict[str, Any]] = []
    for event_key, event_name, event_year, team_count in event_rows:
        rows = build_event_team_freshness_rows(
            db,
            str(event_key),
            stale_hours_threshold=threshold_hours,
        )
        stale_count = sum(1 for row in rows if row["status"] == "stale")
        missing_count = sum(1 for row in rows if row["status"] == "missing")
        target_count = stale_count + missing_count
        if target_count <= 0:
            continue
        total_teams = int(team_count or 0)
        ratio = (target_count / total_teams) if total_teams > 0 else 0.0
        candidates.append(
            {
                "event_key": str(event_key),
                "event_name": str(event_name or event_key),
                "year": int(event_year or 0),
                "team_count": total_teams,
                "stale_count": stale_count,
                "missing_count": missing_count,
                "target_count": target_count,
                "target_ratio": round(ratio, 4),
            }
        )

    candidates.sort(
        key=lambda row: (
            int(row.get("target_count") or 0),
            float(row.get("target_ratio") or 0.0),
            int(row.get("year") or 0),
            str(row.get("event_key") or ""),
        ),
        reverse=True,
    )
    return candidates[:capped_max_events]


def recover_stale_events(
    db: Session,
    *,
    stale_hours_threshold: int | None = None,
    max_events: int = 3,
    max_target_teams_per_event: int | None = None,
    force_analysis: bool = False,
    require_video: bool = True,
    require_calibration: bool = True,
    run_post_compute: bool = True,
    model_version: str = SYNERGY_MODEL_VERSION,
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
) -> dict[str, Any]:
    threshold_hours = int(stale_hours_threshold or settings.freshness_sla_stale_hours)
    candidates = select_recovery_candidate_events(
        db,
        stale_hours_threshold=threshold_hours,
        max_events=max_events,
    )

    event_results: list[dict[str, Any]] = []
    for candidate in candidates:
        event_key = str(candidate.get("event_key") or "").strip().lower()
        if not event_key:
            continue
        result = recover_event_freshness(
            db,
            event_key=event_key,
            stale_hours_threshold=threshold_hours,
            force_analysis=force_analysis,
            require_video=require_video,
            require_calibration=require_calibration,
            run_post_compute=run_post_compute,
            model_version=model_version,
            quality_threshold=quality_threshold,
            max_target_teams=max_target_teams_per_event,
        )
        result["candidate"] = candidate
        event_results.append(result)

    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stale_hours_threshold": threshold_hours,
        "max_events": int(max_events),
        "candidate_event_count": len(candidates),
        "processed_event_count": len(event_results),
        "events": event_results,
        "totals": {
            "scheduled_matches": int(
                sum(int((row.get("analysis") or {}).get("scheduled") or 0) for row in event_results)
            ),
            "skipped_matches": int(
                sum(int((row.get("analysis") or {}).get("skipped") or 0) for row in event_results)
            ),
            "blocked_matches": int(
                sum(int((row.get("analysis") or {}).get("blocked") or 0) for row in event_results)
            ),
            "targeted_teams": int(
                sum(int((row.get("freshness_summary") or {}).get("targeted") or 0) for row in event_results)
            ),
        },
    }
    _persist_last_result(summary)
    return summary

