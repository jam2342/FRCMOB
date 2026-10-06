from collections import defaultdict
from contextlib import suppress
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin_access, require_write_access, sanitize_external_error
from app.db import models
from app.db.session import get_db
from app.api.schemas import RatingsResponse, TeamsHistoryResponse
from app.services.analysis.runs import RUN_KIND_VIDEO
from app.services.scouting_rooms.helpers import (
    RECENT_STATS_MATCH_WINDOW,
    apply_quality_gate_to_finding_rows as _apply_quality_gate_to_finding_rows,
    compute_averages as _compute_averages,
    compute_climb_sources as _compute_climb_sources,
    dedupe_finding_rows_by_match as _dedupe_finding_rows_by_match,
    extract_perimeter_context as _extract_perimeter_context,
    infer_climb_from_match_events as _infer_climb_from_match_events,
    metric_coverage_payload as _metric_coverage_payload,
    rows_for_metric_coverage as _rows_for_metric_coverage,
    safe_int as _safe_int,
    serialize_finding as _serialize_finding,
    serialize_rating_row as _serialize_rating_row,
)

from app.services.quality_gate import quality_gate_config_payload
from app.services.ml.shadow import (
    TEAM_STRENGTH_MODEL_KEY,
    build_team_strength_shadow_input_from_rating_row,
    infer_team_strength_shadow_from_rows,
)
from app.services.ratings.model import (
    MODEL_VERSION,
    recompute_event_ratings,
)
from app.services.ratings.snapshots import compute_event_rating_trends
from app.services.scouting_rooms.scope import (
    active_game_season_year as _active_game_season_year,
    build_data_freshness_payload as _build_data_freshness_payload,
    event_year_from_key as _event_year_from_key,
)
from app.services.team_utils import (
    region_label as _region_label,
    team_number_from_team_key as _team_number_from_team_key,
)
from app.services.utils import _as_float
from app.tba.client import TBAClient

router = APIRouter(prefix="/scouting", tags=["scouting"])
logger = logging.getLogger(__name__)


def _resolve_team_season_scope(
    db: Session,
    team_key: str,
    event_key: str | None = None,
) -> dict:
    active_season_year = _active_game_season_year()
    if event_key:
        event = db.get(models.Event, event_key)
        scoped_year = (
            int(event.year)
            if event and isinstance(event.year, int)
            else (_event_year_from_key(event_key) or active_season_year)
        )
        return {
            "season_year": scoped_year,
            "active_season_year": active_season_year,
            "source": "event",
            "uses_active_season": scoped_year == active_season_year,
        }

    has_active_season_data = (
        db.query(models.TeamMatchFinding.id)
        .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
        .filter(
            models.TeamMatchFinding.team_key == team_key,
            models.Event.year == active_season_year,
        )
        .first()
        is not None
    )
    if has_active_season_data:
        return {
            "season_year": active_season_year,
            "active_season_year": active_season_year,
            "source": "active_season",
            "uses_active_season": True,
        }

    latest_available_year = (
        db.query(func.max(models.Event.year))
        .join(models.TeamMatchFinding, models.TeamMatchFinding.event_key == models.Event.event_key)
        .filter(models.TeamMatchFinding.team_key == team_key)
        .scalar()
    )
    if isinstance(latest_available_year, int):
        return {
            "season_year": int(latest_available_year),
            "active_season_year": active_season_year,
            "source": "latest_available",
            "uses_active_season": int(latest_available_year) == active_season_year,
        }

    return {
        "season_year": active_season_year,
        "active_season_year": active_season_year,
        "source": "active_season_default",
        "uses_active_season": True,
    }


def _team_breakdown_rating_row(
    db: Session,
    *,
    team_key: str,
    event_key: str | None,
    season_year: int,
) -> tuple[models.EventTeamRating | None, str]:
    if event_key:
        event_row = (
            db.query(models.EventTeamRating)
            .filter(
                models.EventTeamRating.event_key == event_key,
                models.EventTeamRating.team_key == team_key,
            )
            .one_or_none()
        )
        if event_row is not None:
            return event_row, "event_rating"

    season_row = (
        db.query(models.EventTeamRating, models.Event)
        .join(models.Event, models.Event.event_key == models.EventTeamRating.event_key)
        .filter(
            models.EventTeamRating.team_key == team_key,
            models.Event.year == int(season_year),
        )
        .order_by(models.EventTeamRating.updated_at.desc(), models.EventTeamRating.event_key.asc())
        .first()
    )
    if season_row is not None:
        return season_row[0], "season_latest_rating"
    return None, "rating_context_unavailable"


def _team_breakdown_ml_projection(
    db: Session,
    *,
    team_key: str,
    event_key: str | None,
    season_year: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": False,
        "model_key": TEAM_STRENGTH_MODEL_KEY,
        "model_version": None,
        "source": None,
        "source_event_key": None,
        "predicted_active_bps": None,
        "predicted_scores_per_min": None,
        "reason": "disabled",
        "detail": None,
        "input": None,
    }
    if not bool(getattr(settings, "ml_shadow_enabled", False)):
        return payload

    rating_row, rating_source = _team_breakdown_rating_row(
        db,
        team_key=team_key,
        event_key=event_key,
        season_year=season_year,
    )
    payload["source"] = rating_source
    if rating_row is None:
        payload["reason"] = "rating_context_unavailable"
        return payload

    ml_input = build_team_strength_shadow_input_from_rating_row(rating_row)
    payload["source_event_key"] = rating_row.event_key
    payload["input"] = {
        "rating_0_100": _as_float(ml_input.get("rating_0_100")),
        "throughput_0_100": _as_float(ml_input.get("throughput_0_100")),
        "confidence_0_1": _as_float(ml_input.get("confidence_0_1")),
        "match_count": int(_safe_int(ml_input.get("match_count")) or 0),
        "coverage_factor_0_1": _as_float(ml_input.get("coverage_factor_0_1")),
    }

    try:
        inference = infer_team_strength_shadow_from_rows(db, rows=[ml_input])
    except Exception as exc:
        payload["reason"] = "inference_failed"
        payload["detail"] = str(exc)
        return payload

    payload["model_version"] = inference.get("model_version")
    if not bool(inference.get("ok")):
        payload["reason"] = str(inference.get("reason") or "prediction_failed")
        payload["detail"] = inference.get("detail")
        return payload

    predictions = inference.get("predictions") if isinstance(inference.get("predictions"), list) else []
    prediction = predictions[0] if predictions and isinstance(predictions[0], dict) else {}
    predicted_bps = _as_float(prediction.get("strength_active_bps_pred"))
    if predicted_bps is None:
        payload["reason"] = "prediction_missing"
        return payload

    payload["available"] = True
    payload["reason"] = "ok"
    payload["predicted_active_bps"] = round(float(predicted_bps), 6)
    payload["predicted_scores_per_min"] = round(float(predicted_bps) * 60.0, 3)
    return payload


def _upsert_event_roster_if_missing(
    db: Session,
    event_key: str,
    event: models.Event | None,
) -> tuple[models.Event | None, bool, str | None]:
    if settings.public_readonly_mode:
        return event, False, "public_mode"
    if settings.tba_auth_key.strip() == "":
        return event, False, "tba_not_configured"

    try:
        tba = TBAClient()
        event_payload = tba.event(event_key)
        teams_payload = tba.event_teams(event_key)
    except Exception as exc:
        return event, False, sanitize_external_error(exc, default="TBA roster fetch failed.")

    if not isinstance(event_payload, dict):
        return event, False, "invalid_event_payload"
    if not isinstance(teams_payload, list):
        return event, False, "invalid_team_payload"

    event_key_payload = str(event_payload.get("key") or event_key).strip().lower()
    event_name = str(event_payload.get("name") or event_key).strip() or event_key
    year_raw = event_payload.get("year")
    year = _event_year_from_key(event_key_payload) or _active_game_season_year()
    if isinstance(year_raw, int):
        year = year_raw
    elif isinstance(year_raw, float):
        year = int(year_raw)
    elif isinstance(year_raw, str):
        with suppress(ValueError):
            year = int(year_raw.strip())

    db.merge(
        models.Event(
            event_key=event_key_payload,
            name=event_name,
            year=year,
        )
    )
    db.merge(
        models.EventProfile(
            event_key=event_key_payload,
            city=event_payload.get("city"),
            state_prov=event_payload.get("state_prov"),
            country=event_payload.get("country"),
        )
    )

    normalized_team_keys: list[str] = []
    for team_payload in teams_payload:
        if not isinstance(team_payload, dict):
            continue
        team_key_raw = team_payload.get("key")
        if not isinstance(team_key_raw, str) or not team_key_raw.strip():
            continue
        team_key = team_key_raw.strip().lower()
        normalized_team_keys.append(team_key)
        team_number = (
            int(team_payload.get("team_number"))
            if isinstance(team_payload.get("team_number"), int)
            else _team_number_from_team_key(team_key)
        )
        db.merge(
            models.Team(
                team_key=team_key,
                team_number=team_number,
                nickname=team_payload.get("nickname"),
            )
        )
        db.merge(
            models.TeamProfile(
                team_key=team_key,
                city=team_payload.get("city"),
                state_prov=team_payload.get("state_prov"),
                country=team_payload.get("country"),
            )
        )

    try:
        db.flush()
    except Exception as exc:
        db.rollback()
        return event, False, sanitize_external_error(exc, default="Roster team upsert failed.")

    for team_key in normalized_team_keys:
        db.merge(
            models.EventTeam(
                event_key=event_key_payload,
                team_key=team_key,
            )
        )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        return event, False, sanitize_external_error(exc, default="Roster upsert failed.")

    refreshed_event = db.get(models.Event, event_key_payload) or db.get(models.Event, event_key)
    return refreshed_event, True, None


@router.get("/event/{event_key}/ratings", response_model=RatingsResponse)
def get_event_ratings(
    event_key: str,
    request: Request,
    refresh: bool = False,
    include_trend: bool = False,
    db: Session = Depends(get_db),
):
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")

    source = "local"
    if refresh:
        require_admin_access(request, "Ratings refresh")
        require_write_access("Ratings refresh")
        recompute_event_ratings(db, event_key)
        source = "recomputed"

    rows = (
        db.query(models.EventTeamRating, models.Team)
        .outerjoin(models.Team, models.Team.team_key == models.EventTeamRating.team_key)
        .filter(models.EventTeamRating.event_key == event_key)
        .order_by(models.EventTeamRating.rating_0_100.desc(), models.EventTeamRating.team_key.asc())
        .all()
    )

    if not rows:
        source = "local_missing"

    payload = [_serialize_rating_row(row, team) for row, team in rows]

    # The live view requests include_trend=true to get sparkline/momentum data
    # without a second round trip; the default response stays unchanged.
    last_updated_at: str | None = None
    if rows:
        latest = max((r.updated_at for r, _ in rows if r.updated_at), default=None)
        last_updated_at = latest.isoformat() if latest else None
    if include_trend:
        trends = compute_event_rating_trends(db, event_key)
        for item in payload:
            item["rating_trend"] = trends.get(item.get("team_key"))

    return {
        "ok": True,
        "event_key": event_key,
        "event_name": event.name,
        "model_version": MODEL_VERSION,
        "source": source,
        "count": len(payload),
        "ratings": payload,
        "last_updated_at": last_updated_at,
    }


@router.get("/team/{team_key}/breakdown")
def get_team_breakdown(
    team_key: str,
    event_key: str | None = None,
    match_limit: int = RECENT_STATS_MATCH_WINDOW,
    event_limit: int = 400,
    track_limit: int = 800,
    db: Session = Depends(get_db),
):
    team = db.get(models.Team, team_key)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    season_scope = _resolve_team_season_scope(db, team_key, event_key=event_key)

    capped_match_limit = max(RECENT_STATS_MATCH_WINDOW, min(match_limit, 100))
    capped_event_limit = max(50, min(event_limit, 2000))
    capped_track_limit = max(50, min(track_limit, 4000))

    findings_query = (
        db.query(models.TeamMatchFinding, models.Match.time, models.AnalysisRun.version)
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
        .join(models.AnalysisRun, models.AnalysisRun.id == models.TeamMatchFinding.analysis_run_id)
        .filter(models.TeamMatchFinding.team_key == team_key)
        .filter(models.AnalysisRun.run_kind == RUN_KIND_VIDEO)
        .filter(models.Event.year == season_scope["season_year"])
    )
    if event_key:
        findings_query = findings_query.filter(models.TeamMatchFinding.event_key == event_key)

    raw_finding_rows = (
        findings_query
        .order_by(models.Match.time.desc().nullslast(), models.TeamMatchFinding.id.desc())
        .limit(max(capped_match_limit, min(capped_match_limit * 3, 300)))
        .all()
    )
    finding_rows, quality_gate = _apply_quality_gate_to_finding_rows(raw_finding_rows)
    finding_rows = _dedupe_finding_rows_by_match(finding_rows)
    metric_rows, analysis_coverage = _rows_for_metric_coverage(raw_finding_rows, finding_rows, quality_gate)
    metric_rows = _dedupe_finding_rows_by_match(metric_rows)[:capped_match_limit]
    data_freshness = _build_data_freshness_payload(
        season_scope=season_scope,
        match_times=[match_time for _, match_time, _ in metric_rows],
        analyzed_matches=len(finding_rows),
        raw_analyzed_matches=len(raw_finding_rows),
        quality_gate_enabled=bool(quality_gate.get("enabled")),
        quality_gate_fallback_used=bool(analysis_coverage.get("fallback_used")),
    )

    findings = [row[0] for row in metric_rows]
    run_ids = [finding.analysis_run_id for finding in findings]
    run_id_set = set(run_ids)
    versions = sorted({row[2] for row in metric_rows if row[2]})
    perimeter_types_in_order: list[str] = []
    perimeter_sources_in_order: list[str] = []
    for finding in findings:
        perimeter_type, perimeter_source = _extract_perimeter_context(finding)
        if perimeter_type and perimeter_type not in perimeter_types_in_order:
            perimeter_types_in_order.append(perimeter_type)
        if perimeter_source and perimeter_source not in perimeter_sources_in_order:
            perimeter_sources_in_order.append(perimeter_source)

    match_events: list[models.MatchEvent] = []
    event_type_counts_rows: list[tuple[str, int]] = []
    robot_tracks: list[models.RobotTrack] = []
    if (not quality_gate["enabled"]) or run_id_set:
        match_events_query = db.query(models.MatchEvent).filter(models.MatchEvent.team_key == team_key)
        if event_key:
            match_events_query = match_events_query.filter(models.MatchEvent.event_key == event_key)
        else:
            match_events_query = (
                match_events_query
                .join(models.Event, models.Event.event_key == models.MatchEvent.event_key)
                .filter(models.Event.year == season_scope["season_year"])
            )
        if quality_gate["enabled"]:
            match_events_query = match_events_query.filter(models.MatchEvent.analysis_run_id.in_(run_id_set))
        match_events = (
            match_events_query
            .order_by(models.MatchEvent.time_sec.desc(), models.MatchEvent.id.desc())
            .limit(capped_event_limit)
            .all()
        )

        event_type_counts_query = (
            db.query(models.MatchEvent.event_type, func.count(models.MatchEvent.id))
            .filter(models.MatchEvent.team_key == team_key)
        )
        if event_key:
            event_type_counts_query = event_type_counts_query.filter(models.MatchEvent.event_key == event_key)
        else:
            event_type_counts_query = (
                event_type_counts_query
                .join(models.Event, models.Event.event_key == models.MatchEvent.event_key)
                .filter(models.Event.year == season_scope["season_year"])
            )
        if quality_gate["enabled"]:
            event_type_counts_query = event_type_counts_query.filter(models.MatchEvent.analysis_run_id.in_(run_id_set))
        event_type_counts_rows = (
            event_type_counts_query
            .group_by(models.MatchEvent.event_type)
            .order_by(func.count(models.MatchEvent.id).desc(), models.MatchEvent.event_type.asc())
            .all()
        )

        robot_tracks_query = db.query(models.RobotTrack).filter(models.RobotTrack.team_key == team_key)
        if event_key:
            robot_tracks_query = robot_tracks_query.filter(models.RobotTrack.event_key == event_key)
        else:
            robot_tracks_query = (
                robot_tracks_query
                .join(models.Event, models.Event.event_key == models.RobotTrack.event_key)
                .filter(models.Event.year == season_scope["season_year"])
            )
        if quality_gate["enabled"]:
            robot_tracks_query = robot_tracks_query.filter(models.RobotTrack.analysis_run_id.in_(run_id_set))
        robot_tracks = (
            robot_tracks_query
            .order_by(models.RobotTrack.time_sec.desc(), models.RobotTrack.id.desc())
            .limit(capped_track_limit)
            .all()
        )

    zone_time_sec: dict[str, float] = defaultdict(float)
    for event in match_events:
        if event.event_type != "zone_dwell":
            continue
        zone_key = str((event.meta or {}).get("zone_key") or "unknown")
        duration = float((event.meta or {}).get("duration_sec") or 0.0)
        zone_time_sec[zone_key] += duration

    climb_fallback_by_match = _infer_climb_from_match_events(match_events)
    metric_coverage = _metric_coverage_payload(
        findings,
        climb_fallback_by_match=climb_fallback_by_match,
        quality_gate_fallback_used=bool(analysis_coverage.get("fallback_used")),
    )
    ml_projection = _team_breakdown_ml_projection(
        db,
        team_key=team_key,
        event_key=event_key,
        season_year=int(season_scope["season_year"]),
    )

    return {
        "ok": True,
        "team": {
            "team_key": team.team_key,
            "team_number": team.team_number,
            "nickname": team.nickname,
        },
        "event_key": event_key,
        "season_scope": season_scope,
        "data_freshness": data_freshness,
        "quality_gate": quality_gate,
        "analysis_coverage": analysis_coverage,
        "ml_projection": ml_projection,
        "matches_analyzed": len(finding_rows),
        "matches_used_for_metrics": len(findings),
        "matches_before_quality_gate": quality_gate["raw_count"],
        "matches_excluded_by_quality_gate": quality_gate["excluded_count"],
        "averages": _compute_averages(
            findings,
            climb_fallback_by_match=climb_fallback_by_match,
        ),
        "metric_units": {
            "fuel_scoring_rate": "scores_per_min",
            "cycle_time_sec": "sec",
            "auto_contribution": "points",
            "climb_success_prob": "probability_0_1",
            "defensive_engagement_sec": "sec",
            "reliability_score": "probability_0_1",
        },
        "climb_sources": _compute_climb_sources(
            findings,
            climb_fallback_by_match=climb_fallback_by_match,
        ),
        "metric_coverage": metric_coverage,
        "active_perimeter_type": perimeter_types_in_order[0] if perimeter_types_in_order else None,
        "perimeter_types": perimeter_types_in_order,
        "perimeter_sources": perimeter_sources_in_order,
        "analysis_versions": versions,
        "event_type_counts": [
            {"event_type": event_type, "count": count}
            for event_type, count in event_type_counts_rows
        ],
        "zone_time_sec": {key: round(value, 3) for key, value in sorted(zone_time_sec.items())},
        "recent_matches": [
            {
                **_serialize_finding(finding, match_time),
                "analysis_run_id": finding.analysis_run_id,
                "source_version": version,
            }
            for finding, match_time, version in metric_rows
        ],
        "recent_events": [
            {
                "id": event.id,
                "analysis_run_id": event.analysis_run_id,
                "match_key": event.match_key,
                "event_key": event.event_key,
                "track_id": event.track_id,
                "time_sec": event.time_sec,
                "frame_index": event.frame_index,
                "event_type": event.event_type,
                "confidence": event.confidence,
                "field_x": event.field_x,
                "field_y": event.field_y,
                "meta": event.meta,
            }
            for event in match_events[:120]
        ],
        "recent_track_points": [
            {
                "id": track.id,
                "analysis_run_id": track.analysis_run_id,
                "match_key": track.match_key,
                "track_id": track.track_id,
                "frame_index": track.frame_index,
                "time_sec": track.time_sec,
                "field_x": track.field_x,
                "field_y": track.field_y,
                "speed_mps": track.speed_mps,
                "zone_key": track.zone_key,
                "bbox": [track.bbox_x1, track.bbox_y1, track.bbox_x2, track.bbox_y2],
            }
            for track in robot_tracks[:300]
        ],
        "run_ids": sorted(set(run_ids), reverse=True),
    }


@router.get("/event/{event_key}/teams/history", response_model=TeamsHistoryResponse)
def get_event_team_histories(
    event_key: str,
    history_limit: int = RECENT_STATS_MATCH_WINDOW,
    exclude_current_event: bool = True,
    db: Session = Depends(get_db),
):
    normalized_event_key = event_key.strip().lower()
    event = db.get(models.Event, normalized_event_key)
    active_season_year = _active_game_season_year()
    event_season_year = (
        int(event.year)
        if event is not None and isinstance(event.year, int)
        else (_event_year_from_key(normalized_event_key) or active_season_year)
    )
    season_scope = {
        "season_year": event_season_year,
        "active_season_year": active_season_year,
        "source": "event",
        "uses_active_season": event_season_year == active_season_year,
    }

    def _load_team_rows() -> list[tuple[models.EventTeam, models.Team, models.TeamProfile | None]]:
        return (
            db.query(models.EventTeam, models.Team, models.TeamProfile)
            .join(models.Team, models.Team.team_key == models.EventTeam.team_key)
            .outerjoin(models.TeamProfile, models.TeamProfile.team_key == models.Team.team_key)
            .filter(models.EventTeam.event_key == normalized_event_key)
            .order_by(models.Team.team_number.asc())
            .all()
        )

    source = "local"
    refresh_error_detail: str | None = None
    team_rows = _load_team_rows()
    if not team_rows:
        refreshed_event, refreshed, refresh_error = _upsert_event_roster_if_missing(
            db=db,
            event_key=normalized_event_key,
            event=event,
        )
        if refreshed:
            event = refreshed_event
            event_season_year = (
                int(event.year)
                if event is not None and isinstance(event.year, int)
                else (_event_year_from_key(normalized_event_key) or active_season_year)
            )
            season_scope = {
                "season_year": event_season_year,
                "active_season_year": active_season_year,
                "source": "event",
                "uses_active_season": event_season_year == active_season_year,
            }
            source = "remote_refreshed"
            team_rows = _load_team_rows()
        elif refresh_error:
            source = "remote_refresh_failed"
            refresh_error_detail = refresh_error

    team_keys = [event_team.team_key for event_team, *_ in team_rows]
    climb_events_by_team_match: dict[tuple[str, str], list[models.MatchEvent]] = defaultdict(list)
    if team_keys:
        climb_events = (
            db.query(models.MatchEvent)
            .filter(
                models.MatchEvent.event_key == normalized_event_key,
                models.MatchEvent.team_key.in_(team_keys),
                func.lower(models.MatchEvent.event_type).contains("climb"),
            )
            .all()
        )
        for event_row in climb_events:
            if not event_row.team_key:
                continue
            climb_events_by_team_match[(event_row.team_key, event_row.match_key)].append(event_row)

    capped_history = max(RECENT_STATS_MATCH_WINDOW, min(history_limit, 30))
    fetch_limit = max(capped_history, min(capped_history * 3, 180))
    # One query for the whole roster: this ran one per team (40-75 per call, ~3 s
    # on the Events page).
    rows_by_team: dict[str, list[tuple[models.TeamMatchFinding, int | None]]] = defaultdict(list)
    if team_keys:
        history_query = (
            db.query(models.TeamMatchFinding, models.Match.time)
            .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
            .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
            .filter(models.TeamMatchFinding.team_key.in_(team_keys))
            .filter(models.Event.year == event_season_year)
        )
        if exclude_current_event:
            history_query = history_query.filter(models.TeamMatchFinding.event_key != normalized_event_key)
        for finding, match_time in history_query.order_by(
            models.TeamMatchFinding.team_key.asc(),
            models.Match.time.desc().nullslast(),
            models.TeamMatchFinding.id.desc(),
        ):
            bucket = rows_by_team[finding.team_key]
            if len(bucket) < fetch_limit:
                bucket.append((finding, match_time))
    teams_payload = []

    for event_team, team, team_profile in team_rows:
        state_prov = team_profile.state_prov if team_profile else None
        country = team_profile.country if team_profile else None
        raw_history_rows = rows_by_team.get(event_team.team_key, [])
        history_rows, history_quality_gate = _apply_quality_gate_to_finding_rows(raw_history_rows)
        history_rows = _dedupe_finding_rows_by_match(history_rows)
        history_rows_for_metrics, history_analysis_coverage = _rows_for_metric_coverage(
            raw_history_rows,
            history_rows,
            history_quality_gate,
        )
        history_rows_for_metrics = _dedupe_finding_rows_by_match(history_rows_for_metrics)[:capped_history]
        data_freshness = _build_data_freshness_payload(
            season_scope=season_scope,
            match_times=[match_time for _, match_time in history_rows_for_metrics],
            analyzed_matches=len(history_rows),
            raw_analyzed_matches=len(raw_history_rows),
            quality_gate_enabled=bool(history_quality_gate.get("enabled")),
            quality_gate_fallback_used=bool(history_analysis_coverage.get("fallback_used")),
        )

        findings = [row[0] for row in history_rows_for_metrics]
        climb_fallback_by_match: dict[str, float] = {}
        for finding in findings:
            key = (event_team.team_key, finding.match_key)
            climb_events = climb_events_by_team_match.get(key) or []
            if not climb_events:
                continue
            inferred = _infer_climb_from_match_events(climb_events)
            if finding.match_key in inferred:
                climb_fallback_by_match[finding.match_key] = inferred[finding.match_key]
        averages_payload = _compute_averages(
            findings,
            climb_fallback_by_match=climb_fallback_by_match,
        )
        teams_payload.append(
            {
                "team_key": team.team_key,
                "team_number": team.team_number,
                "nickname": team.nickname,
                "region": _region_label(state_prov, country),
                "state_prov": state_prov,
                "country": country,
                "analysis_coverage": history_analysis_coverage,
                "history_count": len(history_rows_for_metrics),
                "history_count_quality_gate_accepted": len(history_rows),
                "history_count_before_quality_gate": history_quality_gate["raw_count"],
                "history_excluded_by_quality_gate": history_quality_gate["excluded_count"],
                "data_freshness": data_freshness,
                "averages": averages_payload if isinstance(averages_payload, dict) else {},
                "previous_games": [
                    _serialize_finding(finding, match_time)
                    for finding, match_time in history_rows_for_metrics
                ],
            }
        )

    return {
        "ok": True,
        "event_key": normalized_event_key,
        "event_name": event.name if event else None,
        "source": source,
        "refresh_error": refresh_error_detail,
        "season_scope": season_scope,
        "quality_gate": quality_gate_config_payload(),
        "teams_count": len(teams_payload),
        "teams": teams_payload,
    }
