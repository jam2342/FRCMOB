# Team intel endpoints (search, team intel, event teams intel).
#
# Split from routes_teams.py — no business logic was changed.
# Endpoint handler functions only; all helpers remain in routes_teams.py.

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.season_config import CURRENT_SEASON_YEAR, PREVIOUS_SEASON_YEAR
from app.core.security import require_admin_access
from app.db import models
from app.db.session import get_db
from app.api.schemas import TeamSearchResponse
from app.services.clients import statbotics as statbotics_client
from app.services.intel.helpers import (
    EVENT_INTEL_CACHE_TTL_SEC,
    TEAM_INTEL_CACHE_TTL_SEC,
    _cache_key,
    _event_intel_cache_token,
    _normalize_team_key,
    _team_intel_cache_token,
)
from app.services.intel.snapshots import read_snapshot, write_snapshot
from app.tba.client import TBAClient

# Import helpers from the main routes_teams module.
# These MUST come from routes_teams (not a sub-module) so that
# unittest.mock.patch("app.api.routes_teams.<name>", ...) intercepts
# calls originating from _build_team_intel_payload and friends.
import app.api.routes_teams as _rt

router = APIRouter(tags=["teams"])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint: search teams
# ---------------------------------------------------------------------------

@router.get("/search", response_model=TeamSearchResponse)
async def search_teams(
    q: str,
    limit: int = 25,
    include_global: bool = True,
    include_event_registrations: bool = True,
    preferred_year: int = CURRENT_SEASON_YEAR,
    fallback_year: int = PREVIOUS_SEASON_YEAR,
    db: Session = Depends(get_db),
):
    query = q.strip().lower()
    if not query:
        return {"ok": True, "query": q, "count": 0, "teams": []}

    capped_limit = max(1, min(limit, 80))
    pattern = f"%{query}%"

    findings_counts_subquery = (
        db.query(
            models.TeamMatchFinding.team_key.label("team_key"),
            func.count(models.TeamMatchFinding.id).label("analyzed_matches"),
        )
        .group_by(models.TeamMatchFinding.team_key)
        .subquery()
    )

    # For numeric queries use prefix match on team_number ("118" -> 118, 1180 but NOT 10118)
    if query.isdigit():
        number_filter = or_(
            models.Team.team_number == int(query),
            cast(models.Team.team_number, String).like(f"{query}%"),
        )
    else:
        number_filter = cast(models.Team.team_number, String).like(pattern)

    rows = (
        db.query(models.Team, findings_counts_subquery.c.analyzed_matches, models.TeamProfile)
        .outerjoin(
            findings_counts_subquery,
            findings_counts_subquery.c.team_key == models.Team.team_key,
        )
        .outerjoin(models.TeamProfile, models.TeamProfile.team_key == models.Team.team_key)
        .filter(
            or_(
                func.lower(models.Team.team_key).like(pattern),
                func.lower(func.coalesce(models.Team.nickname, "")).like(pattern),
                number_filter,
            )
        )
        .order_by(models.Team.team_number.asc())
        .limit(capped_limit)
        .all()
    )

    teams_by_key: dict[str, dict] = {}
    for team, analyzed_matches, team_profile in rows:
        teams_by_key[team.team_key] = {
            "team_key": team.team_key,
            "team_number": team.team_number,
            "nickname": team.nickname,
            "analyzed_matches": int(analyzed_matches or 0),
            "source": "local",
            "country": team_profile.country if team_profile else None,
            "state": team_profile.state_prov if team_profile else None,
        }

    inserted_global = 0
    if include_global and len(teams_by_key) < capped_limit:
        try:
            global_teams = await statbotics_client.get_all_teams(include_inactive=True)
        except RuntimeError:
            global_teams = []

        for item in global_teams:
            team_number_raw = item.get("team")
            if not isinstance(team_number_raw, int):
                continue
            team_number = int(team_number_raw)
            team_key = f"frc{team_number}"
            nickname = item.get("name")

            if not _rt._matches_team_query(query, team_key, team_number, nickname):
                continue

            if team_key not in teams_by_key:
                teams_by_key[team_key] = {
                    "team_key": team_key,
                    "team_number": team_number,
                    "nickname": nickname,
                    "analyzed_matches": 0,
                    "source": "global",
                    "country": item.get("country"),
                    "state": item.get("state"),
                }

                # Upsert matched global teams so "Open Team" can load breakdown without 404.
                if not settings.public_readonly_mode:
                    db.merge(
                        models.Team(
                            team_key=team_key,
                            team_number=team_number,
                            nickname=nickname,
                        )
                    )
                    inserted_global += 1

            if len(teams_by_key) >= capped_limit:
                break

    if inserted_global > 0:
        db.commit()

    def _rank(item: dict) -> tuple[int, int]:
        team_key = str(item["team_key"]).lower()
        number_str = str(item["team_number"])
        nickname = str(item.get("nickname") or "").lower()
        if query == team_key or query == number_str:
            return (0, item["team_number"])
        if team_key.startswith(query) or number_str.startswith(query):
            return (1, item["team_number"])
        if nickname.startswith(query):
            return (2, item["team_number"])
        return (3, item["team_number"])

    teams = sorted(teams_by_key.values(), key=_rank)[:capped_limit]

    tba: TBAClient | None = None
    if include_event_registrations and settings.tba_auth_key.strip():
        tba = TBAClient()

    if include_event_registrations:
        for team_item in teams:
            selected_year, registered_events, source = _rt._team_registered_events(
                db=db,
                team_key=str(team_item["team_key"]),
                preferred_year=preferred_year,
                fallback_year=fallback_year,
                tba=tba,
            )
            team_item["registration_year"] = selected_year
            team_item["registered_events"] = registered_events
            team_item["registered_events_count"] = len(registered_events)
            team_item["registered_events_source"] = source
    else:
        for team_item in teams:
            team_item["registration_year"] = None
            team_item["registered_events"] = []
            team_item["registered_events_count"] = 0
            team_item["registered_events_source"] = "disabled"

    return {
        "ok": True,
        "query": q,
        "count": len(teams),
        "teams": teams,
    }

# ---------------------------------------------------------------------------
# Endpoint: team intel
# ---------------------------------------------------------------------------

@router.get("/{team_key}/intel")
async def get_team_intel(
    team_key: str,
    request: Request,
    event_key: str | None = None,
    preferred_year: int = CURRENT_SEASON_YEAR,
    fallback_year: int = PREVIOUS_SEASON_YEAR,
    include_tba: bool = True,
    include_statbotics: bool = True,
    allow_season_fallback: bool = True,
    auto_heal_ratings: bool = False,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    normalized_team_key = _normalize_team_key(team_key)
    cache_token = _team_intel_cache_token(
        team_key=normalized_team_key,
        event_key=event_key,
        preferred_year=preferred_year,
        fallback_year=fallback_year,
        include_tba=include_tba,
        include_statbotics=include_statbotics,
        allow_season_fallback=allow_season_fallback,
        auto_heal_ratings=auto_heal_ratings,
    )
    cache_key = _cache_key("team", cache_token)
    if refresh or auto_heal_ratings:
        require_admin_access(request, "Team intel refresh/auto-heal")
    if not refresh:
        snapshot_payload, snapshot_age = read_snapshot(db, snapshot_key=cache_key)
        if snapshot_payload is not None:
            return _rt._with_cache_metadata(
                snapshot_payload,
                cache_key=cache_key,
                cache_hit=True,
                age_sec=snapshot_age,
                ttl_sec=TEAM_INTEL_CACHE_TTL_SEC,
            )
        cached_payload, age_sec = await _rt._aread_cached_payload(cache_key)
        if cached_payload is not None:
            return _rt._with_cache_metadata(
                cached_payload,
                cache_key=cache_key,
                cache_hit=True,
                age_sec=age_sec,
                ttl_sec=TEAM_INTEL_CACHE_TTL_SEC,
            )

    payload = await _rt._build_team_intel_payload(
        db=db,
        team_key=normalized_team_key,
        event_key=event_key,
        preferred_year=preferred_year,
        fallback_year=fallback_year,
        include_tba=include_tba,
        include_statbotics=include_statbotics,
        allow_season_fallback=allow_season_fallback,
        auto_heal_ratings=auto_heal_ratings,
    )
    await _rt._awrite_cached_payload(cache_key, payload, TEAM_INTEL_CACHE_TTL_SEC)
    try:
        write_snapshot(
            db,
            snapshot_key=cache_key,
            scope="team",
            payload=payload,
            ttl_sec=TEAM_INTEL_CACHE_TTL_SEC,
            event_key=(event_key.strip().lower() if isinstance(event_key, str) and event_key.strip() else None),
            team_key=normalized_team_key,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("teams.team_intel snapshot write failed key=%s error=%s", cache_key, exc)
    return _rt._with_cache_metadata(
        payload,
        cache_key=cache_key,
        cache_hit=False,
        age_sec=0,
        ttl_sec=TEAM_INTEL_CACHE_TTL_SEC,
    )

# ---------------------------------------------------------------------------
# Endpoint: event teams intel
# ---------------------------------------------------------------------------

@router.get("/event/{event_key}/intel")
async def get_event_teams_intel(
    event_key: str,
    request: Request,
    include_tba: bool = True,
    include_statbotics: bool = True,
    auto_heal_ratings: bool = False,
    include_season_fallback: bool = True,
    include_rating_details: bool = False,
    include_rating_signals: bool = False,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    normalized_event_key = event_key.strip().lower()
    cache_token = _event_intel_cache_token(
        event_key=normalized_event_key,
        include_tba=include_tba,
        include_statbotics=include_statbotics,
        auto_heal_ratings=auto_heal_ratings,
        include_season_fallback=include_season_fallback,
        include_rating_details=include_rating_details,
        include_rating_signals=include_rating_signals,
    )
    cache_key = _cache_key("event", cache_token)
    if refresh or auto_heal_ratings:
        require_admin_access(request, "Event intel refresh/auto-heal")
    if not refresh:
        snapshot_payload, snapshot_age = read_snapshot(db, snapshot_key=cache_key)
        if snapshot_payload is not None:
            return _rt._with_cache_metadata(
                snapshot_payload,
                cache_key=cache_key,
                cache_hit=True,
                age_sec=snapshot_age,
                ttl_sec=EVENT_INTEL_CACHE_TTL_SEC,
            )
        cached_payload, age_sec = await _rt._aread_cached_payload(cache_key)
        if cached_payload is not None:
            return _rt._with_cache_metadata(
                cached_payload,
                cache_key=cache_key,
                cache_hit=True,
                age_sec=age_sec,
                ttl_sec=EVENT_INTEL_CACHE_TTL_SEC,
            )

    payload = await _rt._build_event_teams_intel_payload(
        db=db,
        event_key=normalized_event_key,
        include_tba=include_tba,
        include_statbotics=include_statbotics,
        auto_heal_ratings=auto_heal_ratings,
        include_season_fallback=include_season_fallback,
        include_rating_details=include_rating_details,
        include_rating_signals=include_rating_signals,
    )
    await _rt._awrite_cached_payload(cache_key, payload, EVENT_INTEL_CACHE_TTL_SEC)
    try:
        write_snapshot(
            db,
            snapshot_key=cache_key,
            scope="event",
            payload=payload,
            ttl_sec=EVENT_INTEL_CACHE_TTL_SEC,
            event_key=normalized_event_key,
            team_key=None,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("teams.event_intel snapshot write failed key=%s error=%s", cache_key, exc)
    return _rt._with_cache_metadata(
        payload,
        cache_key=cache_key,
        cache_hit=False,
        age_sec=0,
        ttl_sec=EVENT_INTEL_CACHE_TTL_SEC,
    )
