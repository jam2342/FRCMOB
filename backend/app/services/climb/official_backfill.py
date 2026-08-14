from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import sanitize_external_error
from app.db import models
from app.services.utils import _as_float
from app.tba.client import TBAClient

BACKFILL_RUN_VERSION = "tba_scorebreakdown_climb_backfill_v1"
BACKFILL_SOURCE = "tba_score_breakdown_backfill"


def _is_official_source_token(source: object) -> bool:
    return "score_breakdown" in str(source or "").strip().lower()


def _summary_status(summary: dict[str, Any]) -> dict[str, Any]:
    status = summary.get("status") if isinstance(summary.get("status"), dict) else {}
    return dict(status) if isinstance(status, dict) else {}


def _normalize_event_year(event_key: str, fallback_year: int) -> int:
    token = str(event_key or "").strip()[:4]
    if token.isdigit():
        value = int(token)
        if 1992 <= value <= 2100:
            return value
    return int(fallback_year)


def _official_prob(climb_success: bool, climb_points: float) -> float:
    if climb_success:
        return 1.0
    if climb_points > 0.0:
        return 0.35
    return 0.0


def _upsert_backfill_run(db: Session, *, event_key: str, match_key: str) -> models.AnalysisRun:
    run = (
        db.query(models.AnalysisRun)
        .filter(
            models.AnalysisRun.match_key == match_key,
            models.AnalysisRun.version == BACKFILL_RUN_VERSION,
        )
        .order_by(models.AnalysisRun.id.desc())
        .first()
    )
    if run is not None:
        return run

    run = models.AnalysisRun(match_key=match_key, version=BACKFILL_RUN_VERSION, status="completed")
    db.add(run)
    db.flush()
    db.add(
        models.AnalysisRunContext(
            run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            analysis_version=BACKFILL_RUN_VERSION,
            params_hash=BACKFILL_RUN_VERSION,
            calibration_id=None,
        )
    )
    return run


def backfill_official_climb_for_event(
    db: Session,
    *,
    event_key: str,
    tba: TBAClient,
) -> dict[str, Any]:
    from app.services.scoring.truth import (
        _extract_score_breakdown_truth_rows,
        _rebuilt_active_hub_duration_sec,
        _truth_context,
    )

    normalized_event_key = str(event_key or "").strip().lower()
    if not normalized_event_key:
        return {"ok": False, "event_key": normalized_event_key, "error": "missing_event_key"}

    season_year = _normalize_event_year(normalized_event_key, datetime.now(timezone.utc).year)
    matches = tba.event_matches(normalized_event_key)
    if not isinstance(matches, list):
        return {"ok": False, "event_key": normalized_event_key, "error": "invalid_matches_payload"}

    context = _truth_context()
    phases = context.get("phases") if isinstance(context.get("phases"), dict) else {}
    teleop_sec = max(1.0, float(_as_float(phases.get("teleop_sec")) or 120.0))
    if season_year >= 2026:
        teleop_sec = max(
            1.0,
            float(
                _as_float(phases.get("teleop_active_hub_sec"))
                or _rebuilt_active_hub_duration_sec(
                    phases,
                    include_post_deactivate_grace=True,
                )
            ),
        )

    official_rows_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for match_payload in matches:
        if not isinstance(match_payload, dict):
            continue
        match_key = str(match_payload.get("key") or "").strip()
        if not match_key:
            continue
        rows = _extract_score_breakdown_truth_rows(
            match=match_payload,
            season_year=season_year,
            context=context,
            team_weight_by_key=None,
        )
        for row in rows:
            team_key = str(row.get("team_key") or "").strip().lower()
            if not team_key:
                continue
            status_payload = row.get("status") if isinstance(row.get("status"), dict) else {}
            climb_points = max(0.0, float(_as_float(row.get("climb_points")) or 0.0))
            climb_success = bool(row.get("climb_success"))
            if climb_points <= 0.0 and not climb_success and not status_payload:
                continue
            official_rows_by_pair[(match_key, team_key)] = {
                "event_key": normalized_event_key,
                "match_key": match_key,
                "team_key": team_key,
                "alliance": str(row.get("alliance") or "").strip().lower() or None,
                "station": str(row.get("station") or "").strip().lower() or None,
                "auto_points": max(0.0, float(_as_float(row.get("auto_points")) or 0.0)),
                "teleop_points": max(0.0, float(_as_float(row.get("teleop_points")) or 0.0)),
                "teleop_score_count": _as_float(row.get("teleop_score_count")),
                "climb_points": climb_points,
                "climb_success": climb_success,
                "status": status_payload,
            }

    if not official_rows_by_pair:
        return {
            "ok": True,
            "event_key": normalized_event_key,
            "official_rows": 0,
            "updated_findings": 0,
            "inserted_findings": 0,
            "inserted_runs": 0,
        }

    existing_rows = (
        db.query(models.TeamMatchFinding)
        .filter(models.TeamMatchFinding.event_key == normalized_event_key)
        .filter(models.TeamMatchFinding.match_key.in_([key[0] for key in official_rows_by_pair.keys()]))
        .filter(models.TeamMatchFinding.team_key.in_([key[1] for key in official_rows_by_pair.keys()]))
        .order_by(models.TeamMatchFinding.id.desc())
        .all()
    )
    existing_by_pair: dict[tuple[str, str], models.TeamMatchFinding] = {}
    for finding in existing_rows:
        pair = (str(finding.match_key), str(finding.team_key).strip().lower())
        if pair not in existing_by_pair:
            existing_by_pair[pair] = finding

    inserted_runs = 0
    inserted_findings = 0
    updated_findings = 0
    run_by_match: dict[str, models.AnalysisRun] = {}

    for pair, official in official_rows_by_pair.items():
        finding = existing_by_pair.get(pair)
        climb_points = float(official["climb_points"])
        climb_prob = _official_prob(bool(official["climb_success"]), climb_points)
        status_payload = official.get("status") if isinstance(official.get("status"), dict) else {}
        if finding is not None:
            summary = dict(finding.summary) if isinstance(finding.summary, dict) else {}
            merged_status = _summary_status(summary)
            for key, value in status_payload.items():
                if key not in merged_status or not merged_status.get(key):
                    merged_status[key] = value
            if merged_status:
                summary["status"] = merged_status
            summary["official_score_breakdown"] = True
            summary["official_climb_source"] = BACKFILL_SOURCE
            summary["official_climb_points"] = round(climb_points, 4)
            summary["official_climb_success"] = bool(official["climb_success"])
            finding.summary = summary
            if finding.climb_success_prob is None or float(climb_prob) > float(finding.climb_success_prob):
                finding.climb_success_prob = round(float(climb_prob), 4)
            updated_findings += 1
            continue

        match_key, team_key = pair
        run = run_by_match.get(match_key)
        if run is None:
            run = _upsert_backfill_run(db, event_key=normalized_event_key, match_key=match_key)
            run_by_match[match_key] = run
            inserted_runs += 1

        scoring_proxy = (
            official["teleop_score_count"]
            if isinstance(official["teleop_score_count"], (int, float)) and official["teleop_score_count"] > 0.0
            else official["teleop_points"]
        )
        fuel_scoring_rate = (
            ((float(scoring_proxy) / teleop_sec) * 60.0)
            if isinstance(scoring_proxy, (int, float)) and float(scoring_proxy) > 0.0
            else None
        )
        cycle_time_sec = (
            (teleop_sec / max(1e-6, float(scoring_proxy)))
            if isinstance(scoring_proxy, (int, float)) and float(scoring_proxy) > 0.0
            else None
        )
        db.add(
            models.TeamMatchFinding(
                analysis_run_id=run.id,
                match_key=match_key,
                event_key=normalized_event_key,
                team_key=team_key,
                alliance=official.get("alliance") or "red",
                station=official.get("station"),
                source=BACKFILL_SOURCE,
                fuel_scoring_rate=round(float(fuel_scoring_rate), 4)
                if isinstance(fuel_scoring_rate, (int, float))
                else None,
                cycle_time_sec=round(float(cycle_time_sec), 4) if isinstance(cycle_time_sec, (int, float)) else None,
                auto_contribution=round(float(official["auto_points"]), 4),
                climb_success_prob=round(float(climb_prob), 4),
                defensive_engagement_sec=None,
                reliability_score=None,
                summary={
                    "source": BACKFILL_SOURCE,
                    "official_score_breakdown": True,
                    "official_climb_source": BACKFILL_SOURCE,
                    "official_climb_points": round(float(climb_points), 4),
                    "official_climb_success": bool(official["climb_success"]),
                    "status": status_payload,
                    "season_year": season_year,
                },
            )
        )
        inserted_findings += 1

    db.flush()
    return {
        "ok": True,
        "event_key": normalized_event_key,
        "official_rows": int(len(official_rows_by_pair)),
        "updated_findings": int(updated_findings),
        "inserted_findings": int(inserted_findings),
        "inserted_runs": int(inserted_runs),
    }


def run_official_climb_backfill(
    db: Session,
    *,
    event_key: str | None = None,
    max_events: int | None = None,
) -> dict[str, Any]:
    if not bool(settings.tba_auth_key.strip()):
        return {"ok": False, "error": "tba_auth_key_missing", "events": []}

    cap = max(1, min(int(max_events or settings.climb_official_backfill_max_events_per_run), 40))
    selected_events: list[str] = []
    if isinstance(event_key, str) and event_key.strip():
        selected_events = [event_key.strip().lower()]
    else:
        rows = (
            db.query(models.Event.event_key)
            .outerjoin(models.Match, models.Match.event_key == models.Event.event_key)
            .group_by(models.Event.event_key)
            .order_by(
                func.coalesce(func.max(models.Match.time), 0).desc(),
                models.Event.event_key.asc(),
            )
            .limit(cap)
            .all()
        )
        selected_events = [str(row[0]).strip().lower() for row in rows if isinstance(row[0], str) and row[0].strip()]

    tba = TBAClient()
    per_event: list[dict[str, Any]] = []
    for key in selected_events[:cap]:
        try:
            per_event.append(backfill_official_climb_for_event(db, event_key=key, tba=tba))
            db.commit()
        except Exception as exc:
            db.rollback()
            per_event.append(
                {
                    "ok": False,
                    "event_key": key,
                    "error": sanitize_external_error(exc, default="climb_backfill_failed"),
                }
            )

    return {
        "ok": all(bool(item.get("ok")) for item in per_event) if per_event else True,
        "event_count": len(per_event),
        "events": per_event,
        "totals": {
            "official_rows": int(sum(int(item.get("official_rows") or 0) for item in per_event)),
            "updated_findings": int(sum(int(item.get("updated_findings") or 0) for item in per_event)),
            "inserted_findings": int(sum(int(item.get("inserted_findings") or 0) for item in per_event)),
            "inserted_runs": int(sum(int(item.get("inserted_runs") or 0) for item in per_event)),
            "errored_events": int(sum(1 for item in per_event if not bool(item.get("ok")))),
        },
    }


def climb_signal_coverage(
    db: Session,
    *,
    event_key: str | None = None,
    season_year: int | None = None,
    max_events: int = 10,
) -> dict[str, Any]:
    max_events = max(1, min(int(max_events), 40))
    normalized_event = event_key.strip().lower() if isinstance(event_key, str) and event_key.strip() else None
    if normalized_event:
        event_keys = [normalized_event]
    else:
        query = db.query(models.Event.event_key)
        if isinstance(season_year, int):
            query = query.filter(models.Event.year == int(season_year))
        rows = (
            query
            .outerjoin(models.Match, models.Match.event_key == models.Event.event_key)
            .group_by(models.Event.event_key)
            .order_by(func.coalesce(func.max(models.Match.time), 0).desc(), models.Event.event_key.asc())
            .limit(max_events)
            .all()
        )
        event_keys = [str(row[0]).strip().lower() for row in rows if isinstance(row[0], str) and row[0].strip()]

    payload_rows: list[dict[str, Any]] = []
    for key in event_keys:
        teams = (
            db.query(models.EventTeam.team_key)
            .filter(models.EventTeam.event_key == key)
            .all()
        )
        team_keys = {str(row[0]).strip().lower() for row in teams if isinstance(row[0], str) and row[0].strip()}
        findings_query = db.query(models.TeamMatchFinding).filter(models.TeamMatchFinding.event_key == key)
        if team_keys:
            findings = findings_query.filter(models.TeamMatchFinding.team_key.in_(team_keys)).all()
        else:
            findings = []

        signal_by_team: dict[str, dict[str, bool]] = defaultdict(
            lambda: {"any": False, "official": False, "video": False}
        )
        for finding in findings:
            team_key = str(finding.team_key or "").strip().lower()
            if not team_key:
                continue
            summary = finding.summary if isinstance(finding.summary, dict) else {}
            status = _summary_status(summary)
            has_status_signal = any(
                isinstance(status.get(token), (str, int, float, bool))
                and str(status.get(token)).strip()
                and str(status.get(token)).strip().lower() not in {"none", "no", "null", "unknown"}
                for token in ("endgame_tower", "endgameTower", "endgame")
            )
            has_prob_signal = finding.climb_success_prob is not None
            has_signal = bool(has_status_signal or has_prob_signal)
            if not has_signal:
                continue

            bucket = signal_by_team[team_key]
            bucket["any"] = True
            is_official = _is_official_source_token(finding.source) or bool(summary.get("official_score_breakdown"))
            if is_official:
                bucket["official"] = True
            else:
                bucket["video"] = True

        total_teams = len(team_keys)
        any_count = sum(1 for team_key in team_keys if signal_by_team[team_key]["any"])
        official_count = sum(1 for team_key in team_keys if signal_by_team[team_key]["official"])
        video_count = sum(1 for team_key in team_keys if signal_by_team[team_key]["video"])
        payload_rows.append(
            {
                "event_key": key,
                "teams_total": total_teams,
                "teams_with_any_signal": any_count,
                "teams_with_official_signal": official_count,
                "teams_with_video_signal": video_count,
                "coverage_any_pct": round((100.0 * any_count / max(1, total_teams)), 2),
                "coverage_official_pct": round((100.0 * official_count / max(1, total_teams)), 2),
                "coverage_video_pct": round((100.0 * video_count / max(1, total_teams)), 2),
            }
        )

    overall_total = sum(int(row["teams_total"]) for row in payload_rows)
    overall_any = sum(int(row["teams_with_any_signal"]) for row in payload_rows)
    overall_official = sum(int(row["teams_with_official_signal"]) for row in payload_rows)
    overall_video = sum(int(row["teams_with_video_signal"]) for row in payload_rows)

    return {
        "ok": True,
        "scope": "event" if normalized_event else "multi_event",
        "events": payload_rows,
        "summary": {
            "events": len(payload_rows),
            "teams_total": int(overall_total),
            "teams_with_any_signal": int(overall_any),
            "teams_with_official_signal": int(overall_official),
            "teams_with_video_signal": int(overall_video),
            "coverage_any_pct": round((100.0 * overall_any / max(1, overall_total)), 2),
            "coverage_official_pct": round((100.0 * overall_official / max(1, overall_total)), 2),
            "coverage_video_pct": round((100.0 * overall_video / max(1, overall_total)), 2),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
