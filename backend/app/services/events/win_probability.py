# Win probability from the team model (ratings/team_model.py): each alliance's expected
# points and how unsure that is, from results before the match. The rating reads the
# same beliefs, so the rating and the prediction never disagree about who is better.
#
# A played match keeps the prediction made just before it; an upcoming one is forecast
# from current beliefs. The probability is a logistic on margin / spread, its scale
# fitted on the older 2026 events by how many matches here the least-seen team had
# (scripts/evaluate_rating_replay.py).
from __future__ import annotations

import math
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings.team_model_service import season_run

# Logistic scale on (margin / standard deviation), by fewest matches here among the six,
# fitted on the older 2026 events: with nobody seen here yet the beliefs lean on earlier
# events and are trusted less than their spread alone says.
WIN_SCALE = 2.633
WIN_SCALE_NONE_PLAYED = 1.827
WIN_SCALE_FEW_PLAYED = 2.238

MatchSides = tuple[str, list[str], list[str]]  # match_key, red team keys, blue team keys


# Playoffs get their own scale (Phase 4): alliances are set and teams play differently.
WIN_SCALE_PLAYOFF = 2.244


def red_win_prob(margin: float, sd: float, matches_seen: int = 3, *, playoff: bool = False) -> float:
    scale = (
        WIN_SCALE_PLAYOFF if playoff
        else WIN_SCALE_NONE_PLAYED if matches_seen <= 0
        else WIN_SCALE_FEW_PLAYED if matches_seen < 3
        else WIN_SCALE
    )
    z = max(-30.0, min(30.0, scale * float(margin) / max(1e-6, float(sd))))
    return 1.0 / (1.0 + math.exp(-z))


def match_predictions(
    db: Session,
    event_key: str,
    matches: Iterable[MatchSides],
    comp_levels: dict[str, str] | None = None,
) -> dict[str, tuple[float, float, int, object, list[float]]]:
    # match_key -> (red-minus-blue expected points, its standard deviation, fewest
    # earlier matches here among the six teams, the full Prediction, the learned
    # correction's features).
    from app.services.ratings.replay import MatchCard
    from app.services.ratings.team_model_service import correction_features
    # A page request: serve the last run while a newer one builds (team_model_service).
    run = season_run(db, int(str(event_key)[:4]), allow_stale=True)
    event_start = db.query(
        func.min(func.coalesce(models.Match.actual_time, models.Match.time, models.Match.predicted_time))
    ).filter(models.Match.event_key == event_key).scalar()
    out: dict[str, tuple[float, float, int]] = {}
    for match_key, red, blue in matches:
        key = match_key.lower()
        red_keys, blue_keys = tuple(t.lower() for t in red), tuple(t.lower() for t in blue)
        if not red_keys or not blue_keys:
            continue
        played = run.before_match.get(key)
        if played is not None:
            prediction, seen, feature_row = played
        else:
            prediction = run.model.forecast(event_key, red_keys, blue_keys, event_start)
            seen = min(run.played_here.get((t, event_key), 0) for t in red_keys + blue_keys)
            before_events = min(run.played_total.get(t, 0) - run.played_here.get((t, event_key), 0) for t in red_keys + blue_keys)
            card = MatchCard(key, event_key, 0, (comp_levels or {}).get(key, "qm"), event_start or 0, red_keys, blue_keys)
            feature_row = correction_features(run.model, card, prediction, seen, before_events)
        # Matches are won on the full score: points plus foul points received.
        margin = (prediction.red_mean + prediction.red_fouls) - (prediction.blue_mean + prediction.blue_fouls)
        variance = (prediction.red_var or 0.0) + (prediction.blue_var or 0.0) + prediction.red_fouls_var + prediction.blue_fouls_var
        out[key] = (margin, math.sqrt(max(variance, 1e-6)), seen, prediction, feature_row)
    return out
