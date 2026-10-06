# Win probability from official fuel rates known before each match.
#
# A replay of the 56 newest 2026 events (scripts/evaluate_win_probability_replay.py),
# each match predicted from earlier results only: 74.8% right, Brier 0.173. The rating
# formula given the same information (ratings recomputed before every match, OPR from
# earlier scores) managed 69.2% / 0.195, and it was worse on every slice.
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models

OFFICIAL_SOURCES = ("tba_score_breakdown", "tba_score_breakdown_backfill")
# Logistic scale per fuel/active-minute of alliance margin, fitted (no intercept, so red
# and blue stay symmetric) on 15,189 matches from the older 2026 events.
FUEL_WIN_SCALE = 0.0715
# Before teams have played here the margin rests on their previous event, and a single
# scale was overconfident (a first match read 99.97%). Fitted per bucket of "fewest
# earlier matches here among the six teams" (evaluate_win_probability_replay.py
# --calibration): holdout Brier 0.230 -> 0.210 with none played, 0.184 -> 0.178 with
# one or two; from three on the single scale stays (the refit was slightly worse).
FUEL_WIN_SCALE_NONE_PLAYED = 0.0466
FUEL_WIN_SCALE_FEW_PLAYED = 0.0552
# A team's rate here is shrunk toward its previous event's rate until a few matches
# are in: weight n / (n + SHRINK_MATCHES).
SHRINK_MATCHES = 3.0

MatchSides = tuple[str, list[str], list[str]]  # match_key, red team keys, blue team keys


def fuel_red_win_prob(margin: float, matches_seen: int = 3) -> float:
    scale = (
        FUEL_WIN_SCALE_NONE_PLAYED if matches_seen <= 0
        else FUEL_WIN_SCALE_FEW_PLAYED if matches_seen < 3
        else FUEL_WIN_SCALE
    )
    z = max(-30.0, min(30.0, scale * float(margin)))
    return 1.0 / (1.0 + math.exp(-z))


def _prior_event_fuel(db: Session, event_key: str, team_keys: set[str], event_start: int | None) -> dict[str, float]:
    # Each team's mean official fuel rate at its most recent event of this season that
    # finished before this one started.
    if not team_keys or not event_start:
        return {}
    season = str(event_key)[:4]
    rows = (
        db.query(
            models.TeamMatchFinding.event_key,
            func.lower(models.TeamMatchFinding.team_key),
            func.avg(models.TeamMatchFinding.fuel_scoring_rate),
            func.max(models.Match.time),
        )
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .filter(
            func.lower(models.TeamMatchFinding.team_key).in_(sorted(team_keys)),
            models.TeamMatchFinding.event_key != event_key,
            models.TeamMatchFinding.event_key.like(f"{season}%"),
            models.TeamMatchFinding.source.in_(OFFICIAL_SOURCES),
            models.TeamMatchFinding.fuel_scoring_rate.isnot(None),
        )
        .group_by(models.TeamMatchFinding.event_key, func.lower(models.TeamMatchFinding.team_key))
        .all()
    )
    latest: dict[str, tuple[int, float]] = {}
    for _other_event, team_key, mean_rate, last_time in rows:
        if mean_rate is None or not last_time or int(last_time) >= int(event_start):
            continue
        if team_key not in latest or int(last_time) > latest[team_key][0]:
            latest[team_key] = (int(last_time), float(mean_rate))
    return {team_key: rate for team_key, (_t, rate) in latest.items()}


def fuel_margin_details(
    db: Session,
    event_key: str,
    ordered_matches: Iterable[MatchSides],
    event_start: int | None,
) -> dict[str, tuple[float, int]]:
    # Each match's margin plus the fewest earlier matches here among its six teams.
    # Red-minus-blue sum of each alliance's expected fuel rate, using only matches
    # earlier in `ordered_matches` (which must be in playing order). Teams with no
    # estimate take the mean of the known ones. A match is left out (the rating
    # formula covers it) unless both alliances have a known team: with one side
    # unknown, filling it with the known side's mean always gives a margin of 0.
    ordered = [(m.lower(), [t.lower() for t in red], [t.lower() for t in blue]) for m, red, blue in ordered_matches]
    played_rates = {
        (str(match_key).lower(), str(team_key).lower()): float(rate)
        for match_key, team_key, rate in db.query(
            models.TeamMatchFinding.match_key,
            models.TeamMatchFinding.team_key,
            models.TeamMatchFinding.fuel_scoring_rate,
        ).filter(
            models.TeamMatchFinding.event_key == event_key,
            models.TeamMatchFinding.source.in_(OFFICIAL_SOURCES),
            models.TeamMatchFinding.fuel_scoring_rate.isnot(None),
        )
    }
    team_keys = {t for _m, red, blue in ordered for t in red + blue}
    prior = _prior_event_fuel(db, event_key, team_keys, event_start)

    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])  # sum, count
    margins: dict[str, tuple[float, int]] = {}
    for match_key, red, blue in ordered:
        estimates: dict[str, list[float | None]] = {}
        for color, teams in (("red", red), ("blue", blue)):
            values: list[float | None] = []
            for team_key in teams:
                total, count = totals[team_key]
                running = total / count if count else None
                base = prior.get(team_key)
                if running is None:
                    values.append(base)
                elif base is None:
                    values.append(running)
                else:
                    values.append((count * running + SHRINK_MATCHES * base) / (count + SHRINK_MATCHES))
            estimates[color] = values
        known = [v for values in estimates.values() for v in values if v is not None]
        both_sides = all(any(v is not None for v in estimates[c]) for c in ("red", "blue"))
        if both_sides and red and blue:
            fill = sum(known) / len(known)
            margin = sum(v if v is not None else fill for v in estimates["red"]) - sum(
                v if v is not None else fill for v in estimates["blue"]
            )
            seen = min(int(totals[team_key][1]) for team_key in red + blue)
            margins[match_key] = (margin, seen)
        # Only after predicting: this match's own result must not inform its prediction.
        for team_key in red + blue:
            rate = played_rates.get((match_key, team_key))
            if rate is not None:
                totals[team_key][0] += rate
                totals[team_key][1] += 1
    return margins
