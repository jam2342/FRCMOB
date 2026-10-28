# Two teams' shared history (Compare → Head to head): every match they played on the same
# alliance or against each other, any season, and how those went for the first team.
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, aliased

from app.db import models
from app.db.session import get_db
from app.services.intel.helpers import _normalize_team_key
from app.services.utils import COMP_LEVEL_ORDER

router = APIRouter(tags=["teams"])

_MATCH_LIMIT = 60


def _tally() -> dict[str, int]:
    return {"played": 0, "wins": 0, "losses": 0, "ties": 0}


@router.get("/{team_key}/head-to-head")
def get_team_head_to_head(
    team_key: str,
    other: str = Query(..., max_length=12, pattern=r"^(?i:frc)?\d{1,5}$"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    team_key, other_key = _normalize_team_key(team_key), _normalize_team_key(other)
    if team_key == other_key:
        raise HTTPException(status_code=400, detail="Pick two different teams.")
    mine, theirs = aliased(models.MatchTeam), aliased(models.MatchTeam)
    rows = (
        db.query(mine.match_key, mine.alliance, theirs.alliance, models.Match, models.Event)
        .join(theirs, theirs.match_key == mine.match_key)
        .join(models.Match, models.Match.match_key == mine.match_key)
        .join(models.Event, models.Event.event_key == models.Match.event_key)
        .filter(mine.team_key == team_key, theirs.team_key == other_key)
        .all()
    )
    keys = [row[0] for row in rows]
    results: dict[tuple[str, str], models.MatchAllianceResult] = {}
    if keys:
        for result in db.query(models.MatchAllianceResult).filter(models.MatchAllianceResult.match_key.in_(keys)):
            results[(result.match_key, str(result.alliance).lower())] = result

    together, against = _tally(), _tally()
    matches: list[dict[str, Any]] = []
    for match_key, my_alliance, their_alliance, match, event in rows:
        my_alliance, their_alliance = str(my_alliance).lower(), str(their_alliance).lower()
        relation = "together" if my_alliance == their_alliance else "against"
        opponent = "blue" if my_alliance == "red" else "red"
        mine_row, other_row = results.get((match_key, my_alliance)), results.get((match_key, opponent))
        outcome = str(mine_row.result).lower() if mine_row is not None and mine_row.result else None
        if outcome in ("win", "loss", "tie"):
            tally = together if relation == "together" else against
            tally["played"] += 1
            tally[{"win": "wins", "loss": "losses", "tie": "ties"}[outcome]] += 1
        else:
            outcome = None
        matches.append({
            "match_key": match_key,
            "event_key": event.event_key,
            "event_name": event.name,
            "season": int(event.year),
            "comp_level": match.comp_level,
            "relation": relation,
            "alliance": my_alliance,
            "score": mine_row.total_score if mine_row is not None else None,
            "opponent_score": other_row.total_score if other_row is not None else None,
            "result": outcome,
            "time": match.actual_time or match.time,
        })
    matches.sort(
        key=lambda m: (m["time"] or 0, COMP_LEVEL_ORDER.get(str(m["comp_level"] or "qm").lower(), 9), m["match_key"]),
        reverse=True,
    )
    return {
        "ok": True,
        "team_key": team_key,
        "other_key": other_key,
        "together": together,
        "against": against,
        "seasons": sorted({m["season"] for m in matches}, reverse=True),
        "matches": matches[:_MATCH_LIMIT],
        "match_count": len(matches),
    }
