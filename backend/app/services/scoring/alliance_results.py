# Each alliance's official result per match, from a TBA match payload.
#
# TBA's 2026 breakdown, checked against real payloads (2026cmptx, 2026txhou quals): an
# alliance's minorFoulCount/majorFoulCount are fouls it committed, and its foulPoints
# are points it received from the other alliance's fouls (5 per minor, 15 per major
# from the opponent's counts: 120 of 120 Houston alliance-sides). ENERGIZED is hub
# fuel (hubScore.totalCount) >= 100 at regionals; TRAVERSAL is tower points >= 50.
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db import models
from app.services.scoring.truth import _tower_points_from_status, _truth_context
from app.services.utils import _as_float

ALLIANCES = ("red", "blue")
_SCORING = _truth_context()["scoring_points"]


def _int(value: object) -> int | None:
    number = _as_float(value)
    return int(round(number)) if number is not None else None


def _keys(value: object) -> list[str]:
    return [str(key).lower() for key in value or [] if isinstance(key, str) and key]


def _components_2026(breakdown: dict) -> dict[str, Any]:
    hub = breakdown.get("hubScore") if isinstance(breakdown.get("hubScore"), dict) else {}
    return {
        "auto_hub_points": _int(hub.get("autoPoints")),
        "teleop_hub_points": _int(hub.get("teleopPoints")),
        "transition_hub_points": _int(hub.get("transitionPoints")),
        "shift1_hub_points": _int(hub.get("shift1Points")),
        "shift2_hub_points": _int(hub.get("shift2Points")),
        "shift3_hub_points": _int(hub.get("shift3Points")),
        "shift4_hub_points": _int(hub.get("shift4Points")),
        "endgame_hub_points": _int(hub.get("endgamePoints")),
        # Fuel counted toward ENERGIZED / SUPERCHARGED (auto + teleop, active hub).
        "hub_fuel_count": _int(hub.get("totalCount")),
        # Tower levels are recorded per robot (Robot1..3 = the alliance's team_keys order),
        # so a robot's climbs need no splitting.
        "robot_auto_tower_points": [
            _tower_points_from_status(breakdown.get(f"autoTowerRobot{i}"), auto_phase=True, scoring_points=_SCORING)
            for i in (1, 2, 3)
        ],
        "robot_endgame_tower_points": [
            _tower_points_from_status(breakdown.get(f"endGameTowerRobot{i}"), auto_phase=False, scoring_points=_SCORING)
            for i in (1, 2, 3)
        ],
        "auto_tower_points": _int(breakdown.get("autoTowerPoints")),
        "endgame_tower_points": _int(breakdown.get("endGameTowerPoints")),
        "tower_points": _int(breakdown.get("totalTowerPoints")),
        "adjust_points": _int(breakdown.get("adjustPoints")),
        "energized_rp": bool(breakdown.get("energizedAchieved")),
        "supercharged_rp": bool(breakdown.get("superchargedAchieved")),
        "traversal_rp": bool(breakdown.get("traversalAchieved")),
    }


COMPONENT_PARSERS = {2026: _components_2026}


def parse_alliance_results(match: dict, season_year: int) -> list[dict[str, Any]]:
    # Rows for both alliances of a played match; [] until TBA has a score.
    alliances = match.get("alliances") if isinstance(match.get("alliances"), dict) else {}
    breakdowns = match.get("score_breakdown") if isinstance(match.get("score_breakdown"), dict) else {}
    winner = str(match.get("winning_alliance") or "").lower()
    rows = []
    for color in ALLIANCES:
        payload = alliances.get(color) if isinstance(alliances.get(color), dict) else {}
        score = _int(payload.get("score"))
        if score is None or score < 0:  # TBA uses -1 for unplayed
            return []
        breakdown = breakdowns.get(color) if isinstance(breakdowns.get(color), dict) else {}
        foul_points = _int(breakdown.get("foulPoints"))
        parser = COMPONENT_PARSERS.get(int(season_year))
        rows.append({
            "match_key": str(match.get("key") or "").lower(),
            "alliance": color,
            "team_keys": _keys(payload.get("team_keys")),
            "surrogate_team_keys": _keys(payload.get("surrogate_team_keys")),
            "dq_team_keys": _keys(payload.get("dq_team_keys")),
            "total_score": score,
            "score_without_fouls": score - foul_points if foul_points is not None else None,
            # TBA leaves winning_alliance empty for a tie.
            "result": "tie" if not winner else ("win" if winner == color else "loss"),
            "minor_fouls_committed": _int(breakdown.get("minorFoulCount")),
            "major_fouls_committed": _int(breakdown.get("majorFoulCount")),
            "foul_points_received": foul_points,
            "ranking_points": _int(breakdown.get("rp")),
            "components": parser(breakdown) if parser and breakdown else {},
            "raw_breakdown": breakdown,
        })
    return rows


def upsert_alliance_results(db: Session, *, event_key: str, season_year: int, matches: list[dict]) -> int:
    # Writes (or rewrites) every played match's two rows. The caller commits.
    known = {
        key.lower(): key
        for (key,) in db.query(models.Match.match_key).filter(models.Match.event_key == event_key)
    }
    written = 0
    for match in matches:
        if not isinstance(match, dict):
            continue
        for row in parse_alliance_results(match, season_year):
            match_key = known.get(row["match_key"])
            if match_key is None:  # match row not stored yet
                continue
            db.merge(models.MatchAllianceResult(**{**row, "match_key": match_key}, event_key=event_key, season_year=int(season_year)))
            written += 1
    return written
