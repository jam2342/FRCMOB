# Strict playing-time inputs for the total-score state candidate.
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json

from app.db import models
from app.services.ratings.replay import MatchCard, MatchResult, Season
from app.services.ratings.research_data import ResearchSeason
from app.services.ratings.research_fit import SeasonInputs


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def load_results(db, season, through):
    # No scheduled-time fallback: it cannot prove a result was available before a fit.
    matches = db.query(models.Match).join(models.Event).filter(
        models.Event.year == season, models.Match.actual_time.is_not(None),
        models.Match.actual_time < int(through)).all()
    keys = {m.match_key for m in matches}
    sides = defaultdict(dict)
    for row in db.query(models.MatchAllianceResult).filter_by(season_year=season):
        if row.match_key in keys and row.alliance in ("red", "blue"):
            sides[row.match_key][row.alliance] = row
    starts = {}
    prepared, skipped = [], defaultdict(int)
    for match in matches:
        rows = sides.get(match.match_key, {})
        if set(rows) != {"red", "blue"} or any(r.total_score is None or r.total_score < 0 for r in rows.values()):
            skipped["incomplete_score"] += 1
            continue
        teams = [str(t).lower() for color in ("red", "blue") for t in rows[color].team_keys or []]
        if len(teams) != 6 or len(set(teams)) != 6:
            skipped["invalid_alliance"] += 1
            continue
        points, components = {}, {}
        for color, row in rows.items():
            value = row.score_without_fouls
            points[color] = float(value) if value is not None and value >= 0 and not row.dq_team_keys else None
            components[color] = {"source_total_points":float(value) if value is not None else None}
            if points[color] is not None:
                components[color]["total_points"] = points[color]
        if all(value is None for value in points.values()):
            skipped["no_eligible_observations"] += 1
        start = int(match.actual_time)
        starts[match.event_key] = min(starts.get(match.event_key, start), start)
        prepared.append((match, rows, teams, points, components))
    results = []
    for match, rows, teams, points, components in prepared:
        card = MatchCard(match.match_key.lower(), match.event_key.lower(), int(match.actual_time), match.comp_level,
                         starts[match.event_key], tuple(teams[:3]), tuple(teams[3:]))
        results.append(MatchResult(card, rows["red"].total_score, rows["blue"].total_score,
                                   points["red"], points["blue"], red_components=components["red"], blue_components=components["blue"]))
    results.sort(key=lambda r: (r.card.time, r.card.match_key))
    return results, dict(skipped)


def results_digest(results):
    return digest([(r.card.match_key, r.card.time, r.card.red, r.card.blue, r.red_score, r.blue_score,
                    r.red_components.get("source_total_points",r.red_points),
                    r.blue_components.get("source_total_points",r.blue_points)) for r in results])


def inputs_for(db, season, through, context):
    results, skipped = load_results(db, season, through)
    rookies = sorted(t.lower() for (t,) in db.query(models.Team.team_key).filter(models.Team.rookie_year == season))
    first = results[0].card.time if results else 0
    research = ResearchSeason(Season(season, results, set(), {}))
    inputs = SeasonInputs(research, [(r.card.time-first)//604800 for r in results], [r.card.time for r in results],
                          dict(context["z_previous"]), set(rookies), context["previous_unit"], [])
    fingerprint = digest({"results": [asdict(r) for r in results], "rookies": rookies, "context": context})
    return inputs, fingerprint, skipped


def season_boundary(season):
    return int(datetime(season, 1, 1, tzinfo=timezone.utc).timestamp())
