# An event's forecast (Phase 6): standings so far, the rest of the quals and the
# playoffs simulated from the team model (simulation/event_sim.py). Cached per event
# until its results or the season run change.
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.services.events.win_probability import match_predictions, red_win_prob
from app.services.ratings.win_correction import corrected
from app.services.ratings.ranking_points import BONUS_RP_CALIBRATION, bonus_chances, event_thresholds
from app.services.ratings.team_model_service import season_run
from app.services.simulation.event_sim import QualMatch, Standing, simulate

RUNS = 2000
_cache: dict[str, tuple[tuple, dict]] = {}
_lock = threading.Lock()


def finals_outcome(db: Session, event_key: str) -> tuple[list[str], list[str]] | None:
    # The event's winners and finalists, once an alliance has won two finals matches.
    # Each alliance keeps its colour through the finals; backups who played count.
    rows = (
        db.query(models.MatchAllianceResult)
        .join(models.Match, models.Match.match_key == models.MatchAllianceResult.match_key)
        .filter(models.MatchAllianceResult.event_key == event_key, func.lower(models.Match.comp_level) == "f")
        .all()
    )
    wins: dict[str, int] = defaultdict(int)
    teams: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        color = str(row.alliance or "").lower()
        if row.result == "win":
            wins[color] += 1
        for team in row.team_keys or []:
            if team not in teams[color]:
                teams[color].append(team)
    winner = next((color for color, count in wins.items() if count >= 2), None)
    if winner is None:
        return None
    loser = "blue" if winner == "red" else "red"
    return teams[winner], teams[loser]


def _standings(teams: list[str], quals: list, results: dict) -> dict[str, Standing]:
    standings: dict[str, Standing] = {t: Standing() for t in teams}
    for match in quals:
        key = match.match_key.lower()
        red_row, blue_row = results.get((key, "red")), results.get((key, "blue"))
        if red_row is None or blue_row is None:
            continue
        for row in (red_row, blue_row):
            surrogates = set(row.surrogate_team_keys or [])
            for team in row.team_keys or []:
                if team in surrogates or team not in standings:
                    continue  # a surrogate match doesn't count toward a team's Ranking Score
                entry = standings[team]
                entry.ranking_points += float(row.ranking_points or 0)
                entry.matches += 1
                entry.points += float(row.score_without_fouls or row.total_score or 0)
    return standings


def _ordered(teams: list[str], standings: dict[str, Standing]) -> list[str]:
    return sorted(
        teams,
        key=lambda t: (
            -(standings[t].ranking_points / standings[t].matches if standings[t].matches else 0.0),
            -(standings[t].points / standings[t].matches if standings[t].matches else 0.0),
        ),
    )


def _final_payload(db: Session, event_key: str, teams: list[str], quals: list, results: dict, outcome) -> dict[str, Any]:
    # A finished event: what happened, from results and stored ratings (no simulation,
    # no season run, so a frozen season never rebuilds).
    standings = _standings(teams, quals, results)
    winners, finalists = outcome if outcome else ([], [])
    points = {
        row.team_key.lower(): ((row.details_json or {}).get("expected_points") or {}).get("total")
        for row in db.query(models.EventTeamRating).filter(models.EventTeamRating.event_key == event_key)
    }
    rows = []
    for rank, team in enumerate(_ordered(teams, standings), start=1):
        entry = standings[team]
        rows.append({
            "team_key": team,
            "current_rank": rank if entry.matches else None,
            "ranking_score": round(entry.ranking_points / entry.matches, 2) if entry.matches else None,
            "matches_played": entry.matches,
            "points_per_match": round(float(points[team]), 1) if isinstance(points.get(team), (int, float)) else None,
            "won_event": team in winners,
            "finalist": team in finalists,
        })
    rows.sort(key=lambda r: (r["current_rank"] or 999, r["team_key"]))
    return {
        "ok": True,
        "event_key": event_key,
        "finished": True,
        "simulated": False,
        "runs": 0,
        "quals_played": sum(1 for m in quals if (m.match_key.lower(), "red") in results),
        "quals_remaining": 0,
        "playoffs_started": True,
        "winners": winners,
        "finalists": finalists,
        "teams": rows,
    }


def event_forecast(db: Session, event_key: str, runs: int = RUNS) -> dict[str, Any]:
    from app.services.seasons import is_frozen, is_rated

    started = time.perf_counter()
    event_key = event_key.lower()
    season = int(event_key[:4])
    if not is_rated(season):
        return {"ok": True, "event_key": event_key, "finished": False, "simulated": False, "unavailable": "season_not_rated", "teams": []}
    results = {
        (row.match_key.lower(), row.alliance): row
        for row in db.query(models.MatchAllianceResult).filter(models.MatchAllianceResult.event_key == event_key)
    }
    outcome = finals_outcome(db, event_key)
    if outcome is not None or is_frozen(db, season):
        teams = sorted({str(t).lower() for (t,) in db.query(models.EventTeam.team_key).filter(models.EventTeam.event_key == event_key)})
        quals = [m for m in db.query(models.Match).filter(models.Match.event_key == event_key) if str(m.comp_level or "").lower() == "qm"]
        return _final_payload(db, event_key, teams, quals, results, outcome)
    run = season_run(db, season, allow_stale=True)
    fingerprint = (id(run), len(results), max((str(r.updated_at) for r in results.values()), default=""))
    with _lock:
        cached = _cache.get(event_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

    teams = sorted({str(t).lower() for (t,) in db.query(models.EventTeam.team_key).filter(models.EventTeam.event_key == event_key)})
    matches = db.query(models.Match).filter(models.Match.event_key == event_key).all()
    sides: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"red": [], "blue": []})
    for match_key, team_key, alliance in db.query(models.MatchTeam.match_key, models.MatchTeam.team_key, models.MatchTeam.alliance).filter(
        models.MatchTeam.event_key == event_key
    ):
        if alliance in ("red", "blue"):
            sides[match_key.lower()][alliance].append(team_key.lower())
    quals = [m for m in matches if str(m.comp_level or "").lower() == "qm"]
    playoffs_started = any(
        (m.match_key.lower(), "red") in results for m in matches if str(m.comp_level or "").lower() != "qm"
    )

    standings = _standings(teams, quals, results)
    remaining_cards = []
    for match in quals:
        key = match.match_key.lower()
        if (key, "red") in results and (key, "blue") in results:
            continue
        if len(sides[key]["red"]) == 3 and len(sides[key]["blue"]) == 3:
            remaining_cards.append((key, sides[key]["red"], sides[key]["blue"]))

    thresholds = event_thresholds(season, [row.components or {} for row in results.values()])
    calibration = BONUS_RP_CALIBRATION.get(season, {})
    predictions = match_predictions(db, event_key, remaining_cards)
    remaining = []
    for key, red, blue in remaining_cards:
        margin, sd, seen, prediction, feature_row = predictions[key]
        p = corrected(season, red_win_prob(margin, sd, seen), feature_row)
        remaining.append(QualMatch(
            tuple(red), tuple(blue), p,
            bonus_chances(season, thresholds, prediction.red_hub, prediction.red_tower, calibration),
            bonus_chances(season, thresholds, prediction.blue_hub, prediction.blue_tower, calibration),
            prediction.red_mean, prediction.blue_mean,
        ))

    event_start = min((int(m.actual_time or m.time or 0) for m in matches if (m.actual_time or m.time)), default=None)
    model = run.model
    strength = {t: sum(b.mean for b in model.belief(t, event_key, event_start).values()) for t in teams}

    def playoff_win(a: tuple[str, ...], b: tuple[str, ...]) -> float:
        sa = model.alliance_strength(event_key, a, event_start)["total"]
        sb = model.alliance_strength(event_key, b, event_start)["total"]
        return red_win_prob(sa[0] - sb[0], (sa[1] + sb[1]) ** 0.5, 3, playoff=True)

    # A playoff-only event (Einstein lists every Championship team and plays no quals)
    # has no rankings to simulate.
    has_quals = bool(quals)
    simulated = simulate(teams, standings, remaining, strength, playoff_win, runs=runs) if teams and has_quals else {}
    current = _ordered(teams, standings)
    rows = []
    for rank, team in enumerate(current, start=1):
        entry = standings[team]
        rows.append({
            "team_key": team,
            "current_rank": rank if entry.matches else None,
            "ranking_score": round(entry.ranking_points / entry.matches, 2) if entry.matches else None,
            "matches_played": entry.matches,
            "points_per_match": round(strength.get(team, 0.0), 1),
            **simulated.get(team, {}),
        })
    rows.sort(key=lambda r: (r.get("expected_rank") or 999, r["team_key"]))
    payload = {
        "ok": True,
        "event_key": event_key,
        "finished": False,
        "runs": runs,
        "quals_played": len(quals) - len(remaining),
        "quals_remaining": len(remaining),
        # Once playoffs have results, their seeding is real: until the finals are won the
        # playoff chances here still re-run selection from the rankings.
        "playoffs_started": playoffs_started,
        "simulated": bool(simulated),
        "teams": rows,
        "seconds": round(time.perf_counter() - started, 2),
    }
    with _lock:
        _cache[event_key] = (fingerprint, payload)
    return payload
