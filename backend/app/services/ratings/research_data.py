# Seasons for the rating research harness, built from raw TBA files saved by
# scripts/fetch_tba_season.py. No database: the same MatchResult objects the replay uses,
# so any model can run through 2022-2026 as-of. Season components for 2026 come from the
# production parser; every season gets the game-agnostic totals.
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.services.ratings.replay import MatchCard, MatchResult, Season, split_holdout, typical_robot
from app.services.scoring.alliance_results import parse_alliance_results

SIDES = ("red", "blue")


@dataclass
class ResearchSeason:
    season: Season
    # event_key -> {"event_type", "week", "district", "name"}
    events: dict[str, dict] = field(default_factory=dict)
    rookie_year: dict[str, int] = field(default_factory=dict)
    # Matches skipped, and why (the loader never drops one silently).
    skipped: dict[str, int] = field(default_factory=dict)


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _components(row: dict, breakdown: dict, *, exclude_dq: bool) -> tuple[dict, float | None]:
    # Components and the points a team model may explain for one alliance side.
    dq = bool(row["dq_team_keys"])
    out = dict(row["components"] or {})
    foul = row["foul_points_received"]
    if foul is not None:
        out["foul_points_received"] = foul
    out["ranking_points"] = row["ranking_points"]
    out["surrogate_team_keys"] = list(row["surrogate_team_keys"])
    out["dq_team_keys"] = list(row["dq_team_keys"])
    out["result"] = row["result"]
    points = row["score_without_fouls"]
    if points is not None:
        out["total_points"] = float(points)
    for name, key in (("auto_points", "autoPoints"), ("teleop_points", "teleopPoints")):
        value = _number(breakdown.get(key))
        if value is not None:
            out[name] = value
    if dq and exclude_dq:
        # A disqualified alliance's score says nothing about how its robots play.
        return {k: v for k, v in out.items() if k in ("foul_points_received", "ranking_points", "surrogate_team_keys", "dq_team_keys", "result")}, None
    return out, float(points) if points is not None else None


def load_cached_season(directory: Path | str, year: int, *, exclude_dq: bool = True) -> ResearchSeason:
    # Every match with a score and three teams a side, in playing order (time, match key).
    folder = Path(directory) / str(year)
    if not folder.is_dir():
        raise FileNotFoundError(f"No cached season {year} under {directory}; run scripts/fetch_tba_season.py")
    results: list[MatchResult] = []
    events: dict[str, dict] = {}
    rookies: dict[str, int] = {}
    skipped = {"unplayed": 0, "not_three_teams": 0}
    raw_cards: list[tuple[int, str, str, str, dict, dict]] = []
    for path in sorted(folder.glob("*.json")):
        payload = json.loads(path.read_text())
        event = payload["event"]
        event_key = str(event["key"]).lower()
        events[event_key] = {k: event.get(k) for k in ("event_type", "week", "district", "name", "start_date")}
        for team in payload.get("teams") or []:
            if isinstance(team.get("rookie_year"), int) and team.get("key"):
                rookies[str(team["key"]).lower()] = team["rookie_year"]
        for match in payload["matches"]:
            rows = parse_alliance_results(match, year)
            if not rows:
                skipped["unplayed"] += 1
                continue
            when = match.get("actual_time") or match.get("time") or match.get("predicted_time")
            by_side = {row["alliance"]: row for row in rows}
            if not when or any(len(by_side[s]["team_keys"]) != 3 for s in SIDES):
                skipped["not_three_teams"] += 1
                continue
            raw_cards.append((int(when), event_key, str(match["key"]).lower(), str(match.get("comp_level") or "").lower(), match, by_side))
    event_start: dict[str, int] = {}
    for when, event_key, *_ in raw_cards:
        event_start[event_key] = min(event_start.get(event_key, when), when)
    for when, event_key, match_key, level, match, rows in raw_cards:
        breakdowns = match.get("score_breakdown") if isinstance(match.get("score_breakdown"), dict) else {}
        comps, points = {}, {}
        for side in SIDES:
            side_breakdown = breakdowns.get(side) if isinstance(breakdowns.get(side), dict) else {}
            comps[side], points[side] = _components(rows[side], side_breakdown, exclude_dq=exclude_dq)
        card = MatchCard(match_key, event_key, when, level, event_start[event_key], tuple(rows["red"]["team_keys"]), tuple(rows["blue"]["team_keys"]))
        results.append(MatchResult(
            card=card,
            red_score=rows["red"]["total_score"],
            blue_score=rows["blue"]["total_score"],
            red_points=points["red"],
            blue_points=points["blue"],
            robots={},
            red_components=comps["red"],
            blue_components=comps["blue"],
        ))
    results.sort(key=lambda r: (r.card.time, r.card.match_key))
    holdout = split_holdout(results)
    training = {r.card.event_key for r in results} - holdout
    season = Season(year=year, results=results, holdout_events=holdout, typical=typical_robot(results, training))
    return ResearchSeason(season=season, events=events, rookie_year=rookies, skipped=skipped)


def event_week(research: ResearchSeason, event_key: str) -> int:
    # TBA's week is 0-based and missing for the Championship: that goes after the last week.
    week = research.events.get(event_key, {}).get("week")
    if isinstance(week, int):
        return week
    weeks = [e["week"] for e in research.events.values() if isinstance(e.get("week"), int)]
    return (max(weeks) + 1) if weeks else 0
