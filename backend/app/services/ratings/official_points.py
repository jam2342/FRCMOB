# One official TBA finding -> the points that robot added in the match.
#
# Teleop: the official fuel rate (per active-hub minute) times the minutes a hub is
# active. The breakdown counts fuel per alliance, so a robot's share is split by its
# event COPR at ingest: an estimate, but the alliance total is real.
# Auto: hub fuel (split the same way) plus its own auto tower points. Rows from before
# that split (no "auto_allocation", the 2026 backfill) divided the alliance's auto
# evenly, which says nothing about one robot: unknown.
# Climb: its own endgame tower level, which the breakdown records per robot.
from __future__ import annotations

from functools import lru_cache

from app.services.ratings.strength import MatchPoints
from app.services.scoring.truth import _tower_points_from_status, _truth_context
from app.services.utils import _as_float

OFFICIAL_SOURCES = ("tba_score_breakdown", "tba_score_breakdown_backfill")


@lru_cache(maxsize=1)
def active_hub_minutes() -> float:
    return float(_truth_context()["phases"]["teleop_active_hub_sec"]) / 60.0


@lru_cache(maxsize=1)
def _scoring_points() -> dict[str, float]:
    return dict(_truth_context()["scoring_points"])


def climb_points(endgame_tower: object) -> float:
    return _tower_points_from_status(endgame_tower, auto_phase=False, scoring_points=_scoring_points())


# Auto allocations that describe one robot.
ROBOT_AUTO_ALLOCATIONS = {"event_auto_fuel_copr", "no_hub_fuel"}


def _status(summary: object) -> dict:
    status = summary.get("status") if isinstance(summary, dict) else None
    return status if isinstance(status, dict) else {}


def endgame_tower(summary: object) -> object:
    return _status(summary).get("endgame_tower")


def match_points(*, match_key: str, time: int, fuel_rate: object, auto: object, summary: object) -> MatchPoints:
    fuel = _as_float(fuel_rate)
    tower = endgame_tower(summary)
    return MatchPoints(
        match_key=str(match_key).lower(),
        time=int(time or 0),
        teleop=fuel * active_hub_minutes() if fuel is not None else None,
        auto=_as_float(auto) if _status(summary).get("auto_allocation") in ROBOT_AUTO_ALLOCATIONS else None,
        # No tower field means an old row that predates per-robot climbs: unknown, not 0.
        climb=climb_points(tower) if tower is not None else None,
    )
