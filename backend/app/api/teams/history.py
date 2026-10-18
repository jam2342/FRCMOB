# A team's FRCMOB ratings season by season (Team Center → Event history). Each season is
# its own game and scale, so seasons are listed apart and never compared.
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.db.session import get_db
from app.services.intel.helpers import _normalize_team_key
from app.services.ratings.constants import SEASON_BASELINES
from app.services.seasons import SEASON_GAMES, current_season, is_frozen
from app.services.simulation.forecast import finals_outcome

router = APIRouter(tags=["teams"])


@router.get("/{team_key}/rating-history")
def get_team_rating_history(team_key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    # routes_teams includes this router, so its helpers are imported when called.
    from app.api.routes_teams import _event_rating_rank_context

    team_key = _normalize_team_key(team_key)
    first_match = (
        db.query(models.Match.event_key, func.min(func.coalesce(models.Match.actual_time, models.Match.time)).label("start"))
        .group_by(models.Match.event_key)
        .subquery()
    )
    rows = (
        db.query(models.EventTeamRating, models.Event, first_match.c.start)
        .join(models.Event, models.Event.event_key == models.EventTeamRating.event_key)
        .outerjoin(first_match, first_match.c.event_key == models.EventTeamRating.event_key)
        .filter(models.EventTeamRating.team_key == team_key)
        .all()
    )
    by_season: dict[int, list[dict[str, Any]]] = {}
    for rating, event, start in rows:
        details = rating.details_json or {}
        # Events the team played at; a listing it never played (Einstein lists every
        # Championship team) or an event still to come isn't history.
        played = int(details.get("matches_here") or 0)
        if played <= 0:
            continue
        rank = _event_rating_rank_context(db, event_key=event.event_key, team_key=team_key) or {}
        outcome = finals_outcome(db, event.event_key)
        by_season.setdefault(int(event.year), []).append({
            "event_key": event.event_key,
            "event_name": event.name,
            "start": int(start) if start is not None else None,
            "rating_0_100": round(float(rating.rating_0_100), 2),
            "rating_range_80": details.get("rating_range_80"),
            "points_per_match": (details.get("expected_points") or {}).get("total"),
            "matches_here": played,
            "rank": rank.get("rank"),
            "field_size": rank.get("field_size"),
            "won_event": bool(outcome and team_key in outcome[0]),
        })
    current = current_season()
    seasons = [
        {
            "season": season,
            "game": SEASON_GAMES.get(season),
            "current": season == current,
            "frozen": is_frozen(db, season),
            "events": sorted(events, key=lambda e: (e["start"] is None, e["start"] or 0, e["event_key"])),
        }
        for season, events in sorted(by_season.items(), reverse=True)
    ]
    return {
        "ok": True,
        "team_key": team_key,
        "seasons": seasons,
        # Seasons the app has no rating algorithm for (no SEASON_BASELINES row).
        "first_rated_season": min(SEASON_BASELINES) if SEASON_BASELINES else None,
    }
