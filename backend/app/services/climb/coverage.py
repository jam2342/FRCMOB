from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models



def _is_official_source_token(source: object) -> bool:
    return "score_breakdown" in str(source or "").strip().lower()


def _summary_status(summary: dict[str, Any]) -> dict[str, Any]:
    status = summary.get("status") if isinstance(summary.get("status"), dict) else {}
    return dict(status) if isinstance(status, dict) else {}


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
