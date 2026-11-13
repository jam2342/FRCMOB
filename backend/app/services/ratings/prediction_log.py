from __future__ import annotations

import copy
import math
import json
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.ratings import calibrations


def feature_rows(db, season, event_key, matches, details, predictions, *, run=None, sides=None, event_start=None):
    from app.services.ratings.team_model_service import season_run

    run = run or season_run(db, season, allow_stale=True)
    # This is a new, explicitly named live-forecast recipe, not the phase 2 state model.
    scores = [float(v) for r in run.results for v in (r.red_score, r.blue_score) if v >= 0]
    unit = max(sum(scores) / (3 * len(scores)), 1.0) if scores else 1.0
    start = min((r.card.event_start for r in run.results), default=0)
    event_start = event_start if event_start is not None else min((m.actual_time or m.time or m.predicted_time for m in matches if m.actual_time or m.time or m.predicted_time), default=start)
    if sides is None:
        grouped = defaultdict(lambda: {"red": [], "blue": []})
        for team in db.query(models.MatchTeam).filter_by(event_key=event_key):
            if team.alliance in ("red", "blue"):
                grouped[team.match_key][team.alliance].append(team.team_key.lower())
        sides = {key: (teams["red"], teams["blue"]) for key, teams in grouped.items()}
    from app.services.ratings.state_inputs import results_digest
    source_data = None
    if settings.rating_state_fit_enabled:
        if run.state_results_sha256 is None:
            run.state_results_sha256 = results_digest(run.results)
        source_data = run.state_results_sha256
    rows = {}
    for match in matches:
        key = match.match_key.lower()
        if key not in details or key not in predictions:
            continue
        _margin, _sd, seen, p, _old_features = details[key]
        red, blue = sides.get(key, ([], []))
        if len(red) != 3 or len(blue) != 3:
            continue
        def side(keys):
            beliefs = [run.model.belief(t, event_key, event_start) for t in keys]
            means = [sum(b.mean for b in v.values()) / unit for v in beliefs]
            sd = max(math.sqrt(max(sum(b.var for b in v.values()), 0)) / unit for v in beliefs)
            return max(means), min(means), sd
        best_r, worst_r, sd_r = side(red)
        best_b, worst_b, sd_b = side(blue)
        before = min(run.played_total.get(t, 0) - run.played_here.get((t, event_key), 0) for t in red + blue)
        rows[key] = {"schema": calibrations.SCHEMA, "results_sha256": source_data,
                     "red_teams":list(red), "blue_teams":list(blue), "event_start":event_start,
                     "scheduled_start":match.predicted_time or match.time, "row": {
            "rm": p.red_mean, "bm": p.blue_mean, "red_fouls": p.red_fouls, "blue_fouls": p.blue_fouls,
            "rv": max(p.red_var or 0, 1e-6), "bv": max(p.blue_var or 0, 1e-6),
            "mv": max((p.red_var or 0) + (p.blue_var or 0) + p.red_fouls_var + p.blue_fouls_var, 1e-6),
            "min_here": seen, "min_before": before, "u": unit, "best_r": best_r, "worst_r": worst_r,
            "best_b": best_b, "worst_b": worst_b, "sd_r": sd_r, "sd_b": sd_b,
            "playoff": float(match.comp_level != "qm"), "season_frac": max(event_start - (start or event_start), 0) / (86400 * 120),
            "p_live": float(predictions[key]["red_win_prob"]),
        }}
    return rows


def record(db: Session, match, prediction, features, *, bundle_version=0, now=None, completed=False):
    now = calibrations.utc(now or datetime.now(timezone.utc))
    json.dumps({"prediction": prediction, "features": features}, allow_nan=False)
    db.flush()
    # Serialize against ingestion's update of this match and other capture writers.
    db.query(models.Match).filter_by(match_key=match.match_key).with_for_update().populate_existing().one()
    log = db.get(models.MatchPredictionLog, match.match_key)
    if log is not None and log.locked_at is not None:
        return log
    start = match.actual_time or match.predicted_time or match.time
    if completed or match.actual_time is not None or (start is not None and start <= now.timestamp()):
        if log is not None and (completed or match.actual_time is not None):
            log.locked_at = now
        return log
    if start is None or not prediction.get("available"):
        return log
    if log is None:
        log = models.MatchPredictionLog(match_key=match.match_key, event_key=match.event_key,
            season=int(match.event_key[:4]), first_shown_at=now, first=copy.deepcopy(prediction), final_at=now,
            final=copy.deepcopy(prediction), features=copy.deepcopy(features), bundle_version=bundle_version, backfilled=False)
        db.add(log)
    elif calibrations.utc(log.final_at) < now and (log.final != prediction or log.features != features or log.bundle_version != bundle_version):
        log.final_at, log.final, log.features = now, copy.deepcopy(prediction), copy.deepcopy(features)
        log.bundle_version = bundle_version
    db.flush()
    return log


def backfill(db, match, prediction, *, now=None):
    db.flush()
    db.query(models.Match).filter_by(match_key=match.match_key).with_for_update().one()
    log = db.get(models.MatchPredictionLog, match.match_key)
    if log is not None:
        return log
    now = calibrations.utc(now or datetime.now(timezone.utc))
    log = models.MatchPredictionLog(match_key=match.match_key, event_key=match.event_key, season=int(match.event_key[:4]),
        first_shown_at=now, first=copy.deepcopy(prediction), final_at=now, final=copy.deepcopy(prediction),
        features={}, bundle_version=0, locked_at=now, backfilled=True)
    db.add(log)
    db.flush()
    return log


def schedule(db, season, event_key, matches, details, schedule_rows, *, now=None, run=None, event_start=None):
    if not settings.rating_prediction_log_enabled:
        return
    now = calibrations.utc(now or datetime.now(timezone.utc))
    by_key = {r["match_key"]: r for r in schedule_rows}
    logs = {r.match_key: r for r in db.query(models.MatchPredictionLog).filter_by(event_key=event_key)}
    production = {key: row.get("prediction", {}) for key, row in by_key.items()}
    features = feature_rows(db, season, event_key, matches, details, production, run=run, event_start=event_start) if details else {}
    bundle = calibrations.active_bundle(db, season)
    version = bundle.bundle_version if bundle else 0
    active = db.get(models.RatingCalibration, bundle.calibration_id) if bundle and bundle.calibration_id else None
    results = {}
    for result in db.query(models.MatchAllianceResult).filter_by(event_key=event_key):
        if result.total_score is not None and result.total_score >= 0:
            results.setdefault(result.match_key, set()).add(result.alliance)
    for match in sorted(matches, key=lambda m: m.match_key):
        row = by_key[match.match_key]
        prediction = row.get("prediction", {})
        complete = results.get(match.match_key, set()) >= {"red", "blue"}
        log = logs.get(match.match_key)
        known_start = match.actual_time or match.predicted_time or match.time
        if log is not None and (log.locked_at is not None or complete or (known_start is not None and known_start <= now.timestamp())):
            if log.locked_at is None and (complete or match.actual_time is not None):
                record(db, match, prediction, {}, now=now, completed=complete)
            row["prediction"] = stored_prediction(log, match)
            continue
        if complete:
            if log is None and prediction.get("available"):
                log = backfill(db, match, prediction, now=now)
            elif log is not None:
                record(db, match, prediction, {}, now=now, completed=True)
            if log:
                row["prediction"] = stored_prediction(log, match)
            continue
        if active and match.match_key in features and match.actual_time is None:
            t = {k: np.array([v]) for k, v in features[match.match_key]["row"].items()}
            t["y"] = np.array([0.5])
            red, blue, p = calibrations.apply(t, active.params)
            probability = float(p[0])
            prediction = {**prediction, "red_points": round(float(red[0]), 1), "blue_points": round(float(blue[0]), 1),
                "red_win_prob": round(probability, 4), "blue_win_prob": round(1 - probability, 4),
                "red_win_prob_deterministic": round(probability, 4), "source_label": "versioned_live_forecast_v1",
                "favored_alliance": "red" if probability > 0.5 else "blue" if probability < 0.5 else None,
                "edge_confidence_0_1": round(abs(probability - 0.5) * 2, 4)}
            bonus = copy.deepcopy(prediction.get("bonus_rp_chances"))
            if bonus:
                for color, win in (("red", probability), ("blue", 1-probability)):
                    bonus[f"{color}_expected_rp"] = round(3*win + sum(bonus.get(color, {}).values()), 2)
                prediction["bonus_rp_chances"] = bonus
            row["prediction"] = prediction
        if log is not None and match.actual_time is None and log.final == prediction and log.features == features.get(match.match_key, {}) and log.bundle_version == version:
            continue
        captured = record(db, match, prediction, features.get(match.match_key, {}), bundle_version=version, now=now)
        if captured is not None and captured.locked_at is not None:
            row["prediction"] = stored_prediction(captured, match)
    db.commit()


def stored_prediction(log, match):
    prediction = log.final
    backfilled = log.backfilled
    # An actual start can arrive late and invalidate the most recent capture.
    if not backfilled and match.actual_time is not None and calibrations.utc(log.final_at).timestamp() >= match.actual_time:
        if calibrations.utc(log.first_shown_at).timestamp() < match.actual_time:
            prediction = log.first
        else:
            return {"available": False, "source_label": "no_valid_pre_match_capture", "backfilled": False}
    return {**copy.deepcopy(prediction), "backfilled": backfilled}
