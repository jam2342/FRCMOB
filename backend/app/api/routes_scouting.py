from collections import defaultdict
from contextlib import suppress
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin_access, require_write_access, sanitize_external_error
from app.db import models
from app.db.session import get_db
from app.api.schemas import RatingsResponse, TeamsHistoryResponse
from app.services.analysis.runs import RUN_KIND_OFFICIAL_TRUTH, best_on_device_run
from app.services.scouting_rooms.helpers import (
    RECENT_STATS_MATCH_WINDOW,
    compute_averages as _compute_averages,
    compute_climb_sources as _compute_climb_sources,
    dedupe_finding_rows_by_match as _dedupe_finding_rows_by_match,
    infer_climb_from_match_events as _infer_climb_from_match_events,
    serialize_finding as _serialize_finding,
    serialize_rating_row as _serialize_rating_row,
)

from app.services.ratings.model import (
    MODEL_VERSION,
    recompute_event_ratings,
)
from app.services.ratings.snapshots import rating_trend
from app.services.scouting_rooms.scope import (
    active_game_season_year as _active_game_season_year,
    event_year_from_key as _event_year_from_key,
)
from app.services.team_utils import (
    region_label as _region_label,
    team_number_from_team_key as _team_number_from_team_key,
)
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
    # "summary" leaves out details and evidence: 2/3 of the payload (864 KB for a
    # 75-team event), and no list screen reads them.
    view: str = Query(default="full", pattern="^(full|summary)$"),
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
    if include_trend:
        for item, (row, _team) in zip(payload, rows):
            item["rating_trend"] = rating_trend((row.details_json or {}).get("rating_history"))
    if view == "summary":
        for item in payload:
            item["details"] = {}
            item["evidence"] = []

    last_updated_at: str | None = None
    if rows:
        latest = max((r.updated_at for r, _ in rows if r.updated_at), default=None)
        last_updated_at = latest.isoformat() if latest else None

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

    def in_scope(query, column):
        if event_key:
            return query.filter(column == event_key)
        return query.join(models.Event, models.Event.event_key == column).filter(
            models.Event.year == season_scope["season_year"]
        )

    # One row per played match from the official score breakdown (the only per-match
    # source since the broadcast pipeline was retired).
    findings_query = in_scope(
        db.query(models.TeamMatchFinding, models.Match.time, models.AnalysisRun.version)
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .join(models.AnalysisRun, models.AnalysisRun.id == models.TeamMatchFinding.analysis_run_id)
        .filter(models.TeamMatchFinding.team_key == team_key)
        .filter(models.AnalysisRun.run_kind == RUN_KIND_OFFICIAL_TRUTH),
        models.TeamMatchFinding.event_key,
    )
    metric_rows = _dedupe_finding_rows_by_match(
        findings_query
        .order_by(models.Match.time.desc().nullslast(), models.TeamMatchFinding.id.desc())
        .limit(max(capped_match_limit, min(capped_match_limit * 3, 300)))
        .all()
    )[:capped_match_limit]
    findings = [row[0] for row in metric_rows]

    match_events = (
        in_scope(db.query(models.MatchEvent).filter(models.MatchEvent.team_key == team_key), models.MatchEvent.event_key)
        .order_by(models.MatchEvent.time_sec.desc(), models.MatchEvent.id.desc())
        .limit(capped_event_limit)
        .all()
    )
    event_type_counts_rows = (
        in_scope(
            db.query(models.MatchEvent.event_type, func.count(models.MatchEvent.id)).filter(
                models.MatchEvent.team_key == team_key
            ),
            models.MatchEvent.event_key,
        )
        .group_by(models.MatchEvent.event_type)
        .order_by(func.count(models.MatchEvent.id).desc(), models.MatchEvent.event_type.asc())
        .all()
    )

    # Positions come only from phone recordings an operator accepted: the best one per
    # match, the same pick the heat map and Attack vs Defense use (Auto Paths).
    recorded_matches = [
        key
        for (key,) in in_scope(
            db.query(models.OnDeviceSession.match_key)
            .filter(models.OnDeviceSession.status == "accepted"),
            models.OnDeviceSession.event_key,
        ).distinct()
    ]
    chosen_runs = []
    for match_key in recorded_matches:
        best = best_on_device_run(db, match_key=match_key, team_key=team_key)
        if best is not None:
            chosen_runs.append(best[0].id)
    robot_tracks = (
        db.query(models.RobotTrack)
        .filter(models.RobotTrack.team_key == team_key, models.RobotTrack.analysis_run_id.in_(chosen_runs))
        .order_by(models.RobotTrack.time_sec.desc(), models.RobotTrack.id.desc())
        .limit(capped_track_limit)
        .all()
    ) if chosen_runs else []

    climb_fallback_by_match = _infer_climb_from_match_events(match_events)
    return {
        "ok": True,
        "team": {
            "team_key": team.team_key,
            "team_number": team.team_number,
            "nickname": team.nickname,
        },
        "event_key": event_key,
        "season_scope": season_scope,
        "matches_analyzed": len(findings),
        "averages": _compute_averages(
            findings,
            climb_fallback_by_match=climb_fallback_by_match,
        ),
        "climb_sources": _compute_climb_sources(
            findings,
            climb_fallback_by_match=climb_fallback_by_match,
        ),
        "event_type_counts": [
            {"event_type": event_type, "count": count}
            for event_type, count in event_type_counts_rows
        ],
        "recent_matches": [
            {
                **_serialize_finding(finding, match_time),
                "analysis_run_id": finding.analysis_run_id,
                "source_version": version,
            }
            for finding, match_time, version in metric_rows
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
        history_rows_for_metrics = _dedupe_finding_rows_by_match(rows_by_team.get(event_team.team_key, []))[:capped_history]

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
                "history_count": len(history_rows_for_metrics),
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
        "teams_count": len(teams_payload),
        "teams": teams_payload,
    }
