# Which season an event belongs to, and what the app can say about it.
#
# - current: the season the game config is for (live ratings, predictions, forecasts).
# - rated: the season has a rating baseline (ratings/constants.py SEASON_BASELINES).
#   Earlier seasons have results but no FRCMOB ratings or predictions.
# - frozen: finished and read-only (scripts/freeze_season.py). Its stored ratings are
#   never recomputed and its pages read archived predictions, so nothing rebuilds a
#   past season in memory.
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db import models

# The game each season was played with; add the new one at kickoff (SEASON_TRANSITION.md).
SEASON_GAMES: dict[int, str] = {
    2022: "RAPID REACT",
    2023: "CHARGED UP",
    2024: "CRESCENDO",
    2025: "REEFSCAPE",
    2026: "REBUILT",
}


def season_of(event_key: str) -> int | None:
    head = str(event_key or "").strip()[:4]
    return int(head) if head.isdigit() else None


def current_season() -> int:
    from app.services.scouting_rooms.scope import active_game_season_year

    return int(active_game_season_year())


def is_rated(season: int | None) -> bool:
    from app.services.ratings.constants import SEASON_BASELINES

    return season is not None and int(season) in SEASON_BASELINES


def is_frozen(db: Session, season: int | None) -> bool:
    return season is not None and db.get(models.FrozenSeason, int(season)) is not None


def season_info(db: Session, season: int | None) -> dict[str, Any]:
    current = current_season()
    return {
        "season": season,
        "game": SEASON_GAMES.get(int(season)) if season is not None else None,
        "current": season == current,
        "rated": is_rated(season),
        "frozen": is_frozen(db, season),
    }


def live_model_allowed(db: Session, season: int | None) -> bool:
    # Pages may run the team model for this season: it is rated and not frozen.
    return is_rated(season) and not is_frozen(db, season)


def freeze_season(db: Session, season: int, *, allow_current: bool = False, note: str | None = None) -> dict[str, Any]:
    # Archive every match prediction of `season` as the schedule serves it now, then mark
    # the season frozen. Run once at the next season's kickoff (scripts/freeze_season.py).
    from app.api.routes_events import get_event_schedule_with_synergy

    season = int(season)
    if is_frozen(db, season):
        return {"ok": True, "season": season, "status": "already_frozen"}
    if not is_rated(season):
        raise ValueError(f"Season {season} has no ratings to freeze.")
    if season == current_season() and not allow_current:
        raise ValueError(f"Season {season} is the current season; freeze it once the next one starts.")

    event_keys = [
        key
        for (key,) in db.query(models.Match.event_key)
        .join(models.Event, models.Event.event_key == models.Match.event_key)
        .filter(models.Event.year == season)
        .distinct()
    ]
    archived = 0
    for event_key in sorted(event_keys):
        payload = get_event_schedule_with_synergy(event_key=event_key, db=db)
        for match in payload.get("matches") or []:
            prediction = match.get("prediction") or {}
            if not prediction.get("available"):
                continue
            db.merge(models.ArchivedMatchPrediction(
                match_key=str(match["match_key"]).lower(), event_key=event_key, season=season, prediction=prediction,
            ))
            archived += 1
        db.commit()
    db.merge(models.FrozenSeason(season=season, note=note))
    db.commit()
    return {"ok": True, "season": season, "status": "frozen", "events": len(event_keys), "archived_predictions": archived}
