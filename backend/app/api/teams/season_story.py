# A team's season told event by event (My Team and Team Center → Season story): where it
# finished each time, its record, best match and rating, then the season's totals. Each
# event is also what the recap picture is drawn from.
from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import models
from app.db.session import get_db
from app.services.intel.helpers import _normalize_team_key
from app.services.seasons import SEASON_GAMES, current_season
from app.services.simulation.forecast import _ordered, _standings, finals_outcome

router = APIRouter(tags=["teams"])


def _best(current: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    # The best match is the highest-scoring win; with no win, the highest score.
    if current is None:
        return candidate
    key = lambda m: (m["result"] == "win", m["score"] or 0)  # noqa: E731
    return candidate if key(candidate) > key(current) else current


@router.get("/{team_key}/season-story")
def get_team_season_story(
    team_key: str,
    season: int | None = Query(default=None, ge=1992, le=2100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    team_key = _normalize_team_key(team_key)
    year = season or current_season()
    team = db.get(models.Team, team_key)
    slots = (
        db.query(models.MatchTeam, models.Match)
        .join(models.Match, models.Match.match_key == models.MatchTeam.match_key)
        .join(models.Event, models.Event.event_key == models.MatchTeam.event_key)
        .filter(models.MatchTeam.team_key == team_key, models.Event.year == year)
        .all()
    )
    by_event: dict[str, list[tuple[models.MatchTeam, models.Match]]] = defaultdict(list)
    for slot, match in slots:
        by_event[slot.event_key].append((slot, match))

    events: list[dict[str, Any]] = []
    totals = {"wins": 0, "losses": 0, "ties": 0}
    season_best: dict[str, Any] | None = None
    event_wins = 0
    for event_key, rows in by_event.items():
        event = db.get(models.Event, event_key)
        results = {
            (row.match_key.lower(), row.alliance): row
            for row in db.query(models.MatchAllianceResult).filter(models.MatchAllianceResult.event_key == event_key)
        }
        record = {"wins": 0, "losses": 0, "ties": 0}
        best: dict[str, Any] | None = None
        played_playoffs = False
        start = None
        for slot, match in rows:
            alliance = str(slot.alliance).lower()
            mine = results.get((match.match_key.lower(), alliance))
            theirs = results.get((match.match_key.lower(), "blue" if alliance == "red" else "red"))
            when = match.actual_time or match.time
            if when:
                start = when if start is None else min(start, when)
            if mine is None or mine.result not in ("win", "loss", "tie"):
                continue
            record[{"win": "wins", "loss": "losses", "tie": "ties"}[mine.result]] += 1
            if str(match.comp_level).lower() != "qm":
                played_playoffs = True
            best = _best(best, {
                "match_key": match.match_key,
                "alliance": alliance,
                "score": mine.total_score,
                "opponent_score": theirs.total_score if theirs is not None else None,
                "result": mine.result,
            })
        if not sum(record.values()):
            continue  # an event still to come, or one listed but never played

        teams = sorted({str(t).lower() for (t,) in db.query(models.EventTeam.team_key).filter(models.EventTeam.event_key == event_key)} | {team_key})
        quals = [m for m in db.query(models.Match).filter(models.Match.event_key == event_key) if str(m.comp_level or "").lower() == "qm"]
        standings = _standings(teams, quals, results)
        ranked = [t for t in _ordered(teams, standings) if standings[t].matches]
        outcome = finals_outcome(db, event_key)
        finish = (
            "won" if outcome and team_key in outcome[0]
            else "finalist" if outcome and team_key in outcome[1]
            else "playoffs" if played_playoffs
            else "quals"
        )
        rating = db.query(models.EventTeamRating).filter(
            models.EventTeamRating.event_key == event_key, models.EventTeamRating.team_key == team_key
        ).one_or_none()
        details = (rating.details_json or {}) if rating is not None else {}
        events.append({
            "event_key": event_key,
            "event_name": event.name if event is not None else event_key,
            "start": start,
            "qual_rank": ranked.index(team_key) + 1 if team_key in ranked else None,
            "field_size": len(ranked) or None,
            "record": record,
            "best_match": best,
            "finish": finish,
            "rating_0_100": round(float(rating.rating_0_100), 1) if rating is not None and rating.rating_0_100 is not None else None,
            "points_per_match": (details.get("expected_points") or {}).get("total"),
        })
        for key in totals:
            totals[key] += record[key]
        if best is not None:
            season_best = _best(season_best, {**best, "event_key": event_key})
        event_wins += finish == "won"

    events.sort(key=lambda e: (e["start"] is None, e["start"] or 0, e["event_key"]))
    return {
        "ok": True,
        "team_key": team_key,
        "nickname": team.nickname if team is not None else None,
        "season": year,
        "game": SEASON_GAMES.get(year),
        "events": events,
        "totals": {**totals, "events": len(events), "event_wins": event_wins, "best_match": season_best},
    }
