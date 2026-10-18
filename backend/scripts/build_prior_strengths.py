#!/usr/bin/env python3
# Each team's end-of-season strength for `--season`, next season's starting belief
# (team_prior_strengths), plus TBA rookie years for the teams we store.
#
# The game changes every year, so strength is measured game-agnostically: the team
# model on each alliance's whole score without foul points, and z = ln(points / the
# season's median team). --source tba reads a past season straight from TBA (one request
# per event; nothing else is stored); --source db reads our own alliance results.
#
#   PYTHONPATH=. python scripts/build_prior_strengths.py --season 2025 --source tba
#   PYTHONPATH=. python scripts/build_prior_strengths.py --season 2026 --source db
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import models
from app.db.session import SessionLocal
from app.services.ratings.replay import MatchCard, MatchResult, load_season
from app.services.ratings.team_model import SeasonTeamModel, TeamModelParams

# Official events: regional, district, district championship (+ divisions), Championship.
OFFICIAL_EVENT_TYPES = {0, 1, 2, 3, 4, 5}
TOTAL = (("total", "total_points"),)
MIN_MATCHES = 8
Z_CLIP = 2.5


def _tba_results(season: int, rookie_years: dict[str, int]) -> list[MatchResult]:
    from app.tba.client import TBAClient

    tba = TBAClient()
    results: list[MatchResult] = []
    for event in tba.events(season) or []:
        if not isinstance(event, dict) or event.get("event_type") not in OFFICIAL_EVENT_TYPES:
            continue
        event_key = str(event["key"]).lower()
        for team in tba.event_teams(event_key) or []:
            if isinstance(team, dict) and isinstance(team.get("rookie_year"), int):
                rookie_years[str(team["key"]).lower()] = team["rookie_year"]
        played = []
        for match in tba.event_matches(event_key) or []:
            alliances = match.get("alliances") or {}
            breakdowns = match.get("score_breakdown") or {}
            sides = {}
            for color in ("red", "blue"):
                payload = alliances.get(color) or {}
                score = payload.get("score")
                teams = tuple(str(t).lower() for t in payload.get("team_keys") or [])
                if not isinstance(score, int) or score < 0 or len(teams) != 3:
                    break
                fouls = (breakdowns.get(color) or {}).get("foulPoints")
                sides[color] = (teams, score, score - fouls if isinstance(fouls, int) else score)
            if len(sides) == 2:
                when = match.get("actual_time") or match.get("time") or 0
                played.append((int(when), str(match["key"]).lower(), str(match.get("comp_level") or ""), sides))
        if not played:
            continue
        start = min(t for t, *_ in played)
        for when, key, level, sides in sorted(played):
            card = MatchCard(key, event_key, when, level, start, sides["red"][0], sides["blue"][0])
            results.append(MatchResult(
                card, sides["red"][1], sides["blue"][1], float(sides["red"][2]), float(sides["blue"][2]), {},
                {"total_points": sides["red"][2]}, {"total_points": sides["blue"][2]},
            ))
    results.sort(key=lambda r: (r.card.time, r.card.match_key))
    return results


def _db_results(db, season: int) -> list[MatchResult]:
    out = []
    for r in load_season(db, season).results:
        if r.red_points is None or r.blue_points is None:
            continue
        out.append(MatchResult(
            r.card, r.red_score, r.blue_score, r.red_points, r.blue_points, {},
            {"total_points": r.red_points}, {"total_points": r.blue_points},
        ))
    return out


def season_strengths(results: list[MatchResult]) -> dict[str, tuple[float, float, int]]:
    # team -> (end-of-season points per match, z, matches)
    per_robot = [v / 3.0 for r in results for v in (r.red_points, r.blue_points) if v is not None]
    priors = {"total": sum(per_robot) / len(per_robot) if per_robot else 0.0, "tower": 0.0}
    model = SeasonTeamModel(priors, TeamModelParams(), components=TOTAL)
    played: dict[str, int] = defaultdict(int)
    for result in results:
        model.predict(result.card)
        model.observe(result)
        for team in result.card.red + result.card.blue:
            played[team] += 1
    points = {team: model.belief(team)["total"].mean for team in played}
    typical = median(p for team, p in points.items() if played[team] >= MIN_MATCHES)
    return {
        team: (p, max(-Z_CLIP, min(Z_CLIP, math.log(max(p, 1.0) / typical))), played[team])
        for team, p in points.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--source", choices=("tba", "db"), default="db")
    parser.add_argument("--rookies-only", action="store_true", help="Only fill teams.rookie_year from the season's TBA team lists.")
    args = parser.parse_args()

    started = time.perf_counter()
    db = SessionLocal()
    rookie_years: dict[str, int] = {}
    if args.rookies_only:
        from app.tba.client import TBAClient

        tba = TBAClient()
        for event in tba.events(args.season) or []:
            if isinstance(event, dict) and event.get("event_type") in OFFICIAL_EVENT_TYPES:
                for team in tba.event_teams(str(event["key"])) or []:
                    if isinstance(team, dict) and isinstance(team.get("rookie_year"), int):
                        rookie_years[str(team["key"]).lower()] = team["rookie_year"]
        results, strengths = [], {}
    else:
        results = _tba_results(args.season, rookie_years) if args.source == "tba" else _db_results(db, args.season)
        strengths = season_strengths(results)
    now = datetime.now(timezone.utc)
    for team, (p, z, n) in strengths.items():
        db.merge(models.TeamPriorStrength(
            season=args.season, team_key=team, points=round(p, 3), z=round(z, 4), matches=n, source=args.source, updated_at=now,
        ))
    updated_rookies = 0
    if rookie_years:
        for team in db.query(models.Team).filter(models.Team.team_key.in_(sorted(rookie_years))):
            if team.rookie_year != rookie_years[team.team_key.lower()]:
                team.rookie_year = rookie_years[team.team_key.lower()]
                updated_rookies += 1
    db.commit()
    db.close()
    ordered = sorted(strengths.items(), key=lambda kv: -kv[1][0])
    print(json.dumps({
        "season": args.season,
        "source": args.source,
        "matches": len(results),
        "teams": len(strengths),
        "rookie_years_seen": len(rookie_years),
        "rookie_years_updated": updated_rookies,
        "top": [(t, round(p, 1), round(z, 2), n) for t, (p, z, n) in ordered[:5]],
        "seconds": round(time.perf_counter() - started, 1),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
