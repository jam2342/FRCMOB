# Win probability from the team model (ratings/team_model.py): each alliance's expected
# points and how unsure that is, from results before the match. The rating reads the
# same beliefs, so the rating and the prediction never disagree about who is better.
#
# A played match keeps the prediction made just before it; an upcoming one is forecast
# from current beliefs. The probability is a logistic on margin / spread, its scale
# fitted on the older 2026 events by how many matches here the least-seen team had
# (scripts/evaluate_rating_replay.py).
from __future__ import annotations

import bisect
import math
import threading
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
    *, capture_context: dict | None = None,
) -> dict[str, tuple[float, float, int, object, list[float]]]:
    # match_key -> (red-minus-blue expected points, its standard deviation, fewest
    # earlier matches here among the six teams, the full Prediction, the learned
    # correction's features).
    from app.services.ratings.replay import MatchCard
    from app.services.ratings.team_model_service import correction_features
    # A page request: serve the last run while a newer one builds (team_model_service).
    run = season_run(db, int(str(event_key)[:4]), allow_stale=True)
    if capture_context is not None:
        capture_context["run"] = run
    event_start = db.query(
        func.min(func.coalesce(models.Match.actual_time, models.Match.time, models.Match.predicted_time))
    ).filter(models.Match.event_key == event_key).scalar()
    if capture_context is not None:
        capture_context["event_start"] = event_start
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


# Where an alliance's expected points rank among every alliance the season has seen:
# both sides of every played match, as the model expected them just before it. 0-100,
# the same scale at a week-1 district event and on Einstein, so "how strong are these
# opponents" means the same thing everywhere (Team Center's schedule difficulty).
_strength_scales: dict[int, tuple[object, list[float]]] = {}
_strength_lock = threading.Lock()


def season_alliance_points(db: Session, season: int) -> list[float]:
    run = season_run(db, season, allow_stale=True)
    with _strength_lock:
        cached = _strength_scales.get(season)
        if cached is not None and cached[0] is run:
            return cached[1]
    points = sorted(
        value
        for prediction, _seen, _features in run.before_match.values()
        for value in (prediction.red_mean, prediction.blue_mean)
        if math.isfinite(value)
    )
    with _strength_lock:
        _strength_scales[season] = (run, points)
    return points


def strength_percentile(scale: list[float], points: float) -> float | None:
    # Mid-rank, so the season's single strongest alliance reads just under 100.
    if not scale or not math.isfinite(points):
        return None
    below = bisect.bisect_left(scale, points)
    at_or_below = bisect.bisect_right(scale, points)
    return round(100.0 * (below + at_or_below) / (2 * len(scale)), 1)


def custom_matchup(db: Session, event_key: str, red: list[str], blue: list[str], *, playoff: bool = False) -> dict:
    # Any six teams at this event, as if they met now: expected scores and win chance from the
    # same model and correction as the schedule's predictions (routes_events schedule). Nothing
    # is stored, and no team is entered into a match.
    from app.services.ratings.win_correction import corrected
    from app.services.seasons import is_frozen, is_rated

    season = int(str(event_key)[:4])
    red_keys = [t.strip().lower() for t in red]
    blue_keys = [t.strip().lower() for t in blue]
    if not is_rated(season) or is_frozen(db, season):
        reason = "season_not_rated" if not is_rated(season) else "season_frozen"
        return {"ok": True, "event_key": event_key, "red": red_keys, "blue": blue_keys, "unavailable": reason}
    teams_here = {str(t).lower() for (t,) in db.query(models.EventTeam.team_key).filter(models.EventTeam.event_key == event_key)}
    six = red_keys + blue_keys
    if len(red_keys) != 3 or len(blue_keys) != 3 or len(set(six)) != 6 or any(t not in teams_here for t in six):
        raise ValueError("Pick three different teams from this event for each alliance.")

    match_key = f"{event_key}_matchup"
    capture_context = {}
    details = match_predictions(db, event_key, [(match_key, red_keys, blue_keys)], comp_levels={match_key: "sf" if playoff else "qm"}, capture_context=capture_context)
    if match_key not in details:
        return {"ok": True, "event_key": event_key, "red": red_keys, "blue": blue_keys, "unavailable": "no_prediction"}
    margin, sd, seen, prediction, feature_row = details[match_key]
    red_prob = max(0.0, min(1.0, corrected(season, red_win_prob(margin, sd, matches_seen=seen, playoff=playoff), feature_row)))
    red_points, blue_points = prediction.red_mean + prediction.red_fouls, prediction.blue_mean + prediction.blue_fouls
    from app.core.config import settings
    if settings.rating_prediction_log_enabled:
        from types import SimpleNamespace
        import numpy as np
        from app.services.ratings import calibrations
        from app.services.ratings.prediction_log import feature_rows
        bundle = calibrations.active_bundle(db, season)
        if bundle and bundle.calibration_id:
            active = db.get(models.RatingCalibration, bundle.calibration_id)
            match = SimpleNamespace(match_key=match_key, comp_level="sf" if playoff else "qm", actual_time=None,
                                    time=capture_context["event_start"], predicted_time=None)
            features = feature_rows(db, season, event_key, [match], details, {match_key: {"red_win_prob": red_prob}},
                                    run=capture_context["run"], sides={match_key: (red_keys, blue_keys)}, event_start=capture_context["event_start"])
            table = {key: np.array([value]) for key, value in features[match_key]["row"].items()}
            table["y"] = np.array([0.5])
            r, b, prob = calibrations.apply(table, active.params)
            red_points, blue_points, red_prob = float(r[0]) + prediction.red_fouls, float(b[0]) + prediction.blue_fouls, float(prob[0])
    return {
        "ok": True,
        "event_key": event_key,
        "red": red_keys,
        "blue": blue_keys,
        "playoff": playoff,
        "red_points": round(red_points, 1),
        "blue_points": round(blue_points, 1),
        "margin_sd": round(sd, 1),
        "red_win_prob": round(red_prob, 4),
        "blue_win_prob": round(1.0 - red_prob, 4),
        # Fewest matches any of the six has played here: below three, trust it less.
        "matches_seen": int(seen),
    }
