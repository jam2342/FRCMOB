# Three-layer fits on captured live forecasts. The production state model is unchanged.
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import func

from app.core.config import settings
from app.db import models
from app.services.ratings.research_eval import paired_cluster_bootstrap
from app.services.ratings.research_win import (
    ScoreCalibration, Temperatures, all_sides, base_logit, context, correction_features,
    fit_correction, fit_score_calibration, fit_temperatures, side_arrays, sigmoid,
)

RECIPE = "live-forecast-logistic-v1"
SCHEMA = "live-forecast-v1"
SCORE_LIMITS = np.array([1.2] * 4 + [0.4] * 4 + [0.6] * 3)
SCORE_STEP = 0.15


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def monday(value):
    value = utc(value)
    return (value - timedelta(days=value.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def identity():
    return {"schema": SCHEMA, "score_coef": [0.0] * 11, "temperatures": [1.0] * 4,
            "pooled_temperature": 1.0, "weights": [0.0] * 12, "blend": 1.0}


def validate_params(params, previous=None):
    if set(params) != set(identity()) or params["schema"] != SCHEMA:
        raise ValueError("unsupported calibration schema")
    json.dumps(params, allow_nan=False)
    limits = {"score_coef": SCORE_LIMITS, "weights": np.ones(12)}
    for name, limit in limits.items():
        value = np.asarray(params[name], dtype=float)
        if value.shape != limit.shape or not np.isfinite(value).all() or np.any(np.abs(value) > limit):
            raise ValueError(f"{name} outside hard bounds")
        if previous is not None and np.any(np.abs(value - previous[name]) > (SCORE_STEP if name == "score_coef" else 0.25) + 1e-12):
            raise ValueError(f"{name} exceeds weekly step limit")
    temp = np.r_[params["temperatures"], params["pooled_temperature"]]
    if temp.shape != (5,) or not np.isfinite(temp).all() or np.any((temp < 0.25) | (temp > 4)):
        raise ValueError("temperatures outside hard bounds")
    if previous is not None:
        prior = np.r_[previous["temperatures"], previous["pooled_temperature"]]
        if np.any((temp / prior > 1.5 + 1e-12) | (prior / temp > 1.5 + 1e-12)):
            raise ValueError("temperature exceeds weekly step limit")
    if not 0 <= params["blend"] <= 1:
        raise ValueError("blend outside hard bounds")
    if previous is not None and abs(params["blend"] - previous["blend"]) > 0.25 + 1e-12:
        raise ValueError("blend exceeds weekly step limit")


def win_base(t):
    return base_logit({**t, "rm": t["rm"] + t["red_fouls"], "bm": t["bm"] + t["blue_fouls"]}, "logistic")


def apply(t, params):
    validate_params(params)
    rows = all_sides(t, np.arange(len(t["y"])))
    cal = ScoreCalibration(np.array(params["score_coef"]), True)
    scores = rows["u"] * cal.apply(rows)
    temps = Temperatures(params["pooled_temperature"], np.array(params["temperatures"]))
    offset = temps.apply(win_base(t), context(t))
    p = sigmoid(offset + correction_features(t) @ np.array(params["weights"]))
    p = params["blend"] * p + (1 - params["blend"]) * t["p_live"]
    return scores[:len(p)], scores[len(p):], p


def active_bundle(db, season, *, lock=False):
    query = db.query(models.RatingActiveBundle).filter_by(season=season)
    if lock:
        query = query.with_for_update()
    return query.one_or_none()


def ensure_bundle(db, season):
    # Serialize creation and version allocation even before this season has a bundle.
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy import text
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 730000 + season})
    bundle = active_bundle(db, season, lock=True)
    if bundle is None:
        bundle = models.RatingActiveBundle(season=season, bundle_version=0, frozen=False)
        db.add(bundle)
        db.flush()
    return bundle


def captured_table(db, season, *, through):
    # Labels join by key; only a capture before the ACTUAL start is valid evidence.
    logs = db.query(models.MatchPredictionLog).filter_by(season=season, backfilled=False).all()
    matches = {m.match_key: m for m in db.query(models.Match).join(models.Event).filter(models.Event.year == season)}
    actual = {}
    for result in db.query(models.MatchAllianceResult).filter_by(season_year=season):
        if result.total_score is not None and result.total_score >= 0:
            actual.setdefault(result.match_key, {})[result.alliance] = (float(result.total_score), result.score_without_fouls)
    out = []
    for log in logs:
        match, scores = matches.get(log.match_key), actual.get(log.match_key, {})
        if match is None or match.actual_time is None or log.locked_at is None or not {"red", "blue"} <= scores.keys():
            continue
        if log.features.get("schema") != SCHEMA or utc(log.final_at).timestamp() >= match.actual_time or match.actual_time >= utc(through).timestamp():
            continue
        if scores["red"][1] is None or scores["blue"][1] is None:
            continue
        out.append({**log.features["row"], "key": log.match_key, "event": log.event_key, "time": match.actual_time,
                    "captured_at": utc(log.final_at).timestamp(), "ra": float(scores["red"][1]), "ba": float(scores["blue"][1]),
                    "y": 0.5 if scores["red"][0] == scores["blue"][0] else float(scores["red"][0] > scores["blue"][0])})
    out.sort(key=lambda r: (r["time"], r["key"]))
    return {key: np.array([row[key] for row in out]) for key in out[0]} if out else {}


def subset(t, mask):
    return {key: value[mask] for key, value in t.items()}


def fit_week(db, season, through, code_sha):
    through = utc(through)
    if through != monday(through):
        raise ValueError("weekly cutoff must be Monday 00:00 UTC")
    bundle = ensure_bundle(db, season)
    if bundle.frozen or db.get(models.FrozenSeason, season):
        return {"status": "frozen"}
    existing = db.query(models.RatingCalibration).filter_by(season=season, recipe=RECIPE, fitted_through=through).first()
    if existing:
        return {"status": "already_fitted", "id": existing.id}
    t = captured_table(db, season, through=through)
    if not t or np.sum(t["y"] != 0.5) < 150:
        return {"status": "insufficient_captured_matches"}
    current = db.get(models.RatingCalibration, bundle.calibration_id) if bundle.calibration_id else None
    centre = copy.deepcopy(current.params if current else identity())
    scores = fit_score_calibration(side_arrays(t), ScoreCalibration(np.array(centre["score_coef"]), True), terms=True)
    decided = t["y"] != 0.5
    temps = fit_temperatures(win_base(t)[decided], t["y"][decided], context(t)[decided],
                             Temperatures(centre["pooled_temperature"], np.array(centre["temperatures"])))
    weights = fit_correction(correction_features(t)[decided], t["y"][decided],
                             temps.apply(win_base(t), context(t))[decided], np.array(centre["weights"]))
    params = {"schema": SCHEMA, "score_coef": scores.coef.tolist(), "temperatures": temps.by_context.tolist(),
              "pooled_temperature": temps.pooled, "weights": weights.tolist(), "blend": centre["blend"]}
    errors = []
    try:
        validate_params(params, centre)
    except ValueError as exc:
        errors.append(str(exc))
    metrics = {"bounds_errors": errors}
    # This is a fit sanity check, not out-of-sample promotion evidence.
    if not errors:
        recent = subset(t, t["time"] >= (through - timedelta(days=14)).timestamp())
        if len(recent["y"]):
            a, b = apply(recent, params)[2], apply(recent, centre)[2]
            mask = recent["y"] != 0.5
            metrics["recent_brier_change"] = float(np.mean((a[mask] - recent["y"][mask])**2 - (b[mask] - recent["y"][mask])**2)) if mask.any() else 0.0
            if metrics["recent_brier_change"] > 0.001:
                errors.append("recent in-sample Brier sanity check failed")
    version = (db.query(func.max(models.RatingCalibration.version)).filter_by(season=season, recipe=RECIPE).scalar() or 0) + 1
    row = models.RatingCalibration(season=season, recipe=RECIPE, version=version, params=params,
        fitted_through=through, matches=len(t["y"]), code_sha=code_sha, fit_metrics=metrics, status="rejected" if errors else "candidate")
    db.add(row)
    db.flush()
    return {"status": row.status, "id": row.id, "matches": row.matches, "errors": errors}


def loss_rows(t, params):
    red, blue, p = apply(t, params)
    q = np.clip(p, 1e-6, 1 - 1e-6)
    y = t["y"]
    ll = -(y * np.log(q) + (1 - y) * np.log(1 - q))
    mae = (np.abs(red - t["ra"]) + np.abs(blue - t["ba"])) / 2
    red_inside = (np.abs(red - t["ra"]) <= 1.2816 * np.sqrt(t["rv"])).astype(float)
    blue_inside = (np.abs(blue - t["ba"]) <= 1.2816 * np.sqrt(t["bv"])).astype(float)
    coverage = (red_inside + blue_inside) / 2
    return {str(k): {"event": str(t["event"][i]), "time": int(t["time"][i]), "brier": float((p[i] - y[i])**2),
                    "log_loss": float(ll[i]), "mae": float(mae[i]), "coverage": float(coverage[i]), "p": float(p[i]),
                    "y": float(y[i]), "score_error": float(t["ra"][i] + t["ba"][i] - red[i] - blue[i]), "score_total": float(red[i] + blue[i]), "first": bool(t["min_here"][i] == 0), "playoff": bool(t["playoff"][i]),
                    "late": bool(t["season_frac"][i] >= 0.35)} for i, k in enumerate(t["key"]) if y[i] != 0.5}


def evaluate_version(db, calibration, through):
    t = captured_table(db, calibration.season, through=through)
    if not t:
        return {"status": "no_evidence"}
    # A forecast captured before this fit existed cannot validate this fit.
    cutoff = max(utc(calibration.fitted_through).timestamp(), utc(calibration.created_at).timestamp())
    t = subset(t, (t["captured_at"] > cutoff) & (t["time"] > cutoff))
    if not len(t["y"]):
        return {"status": "no_evidence"}
    rows = loss_rows(t, calibration.params)
    for start in sorted({monday(datetime.fromtimestamp(r["time"], timezone.utc)) for r in rows.values()}):
        week = {k: r for k, r in rows.items() if monday(datetime.fromtimestamp(r["time"], timezone.utc)) == start}
        # Store exact common-match evidence; resampling aggregated unmatched events can bias a gate.
        row = db.get(models.RatingGateScore, (calibration.id, start, "all"))
        if row is None:
            row = models.RatingGateScore(calibration_id=calibration.id, week_start=start, slice="all")
            db.add(row)
        row.n, row.metrics, row.per_event = len(week), {"through": utc(through).isoformat()}, week
    db.flush()
    return {"status": "scored", "matches": len(rows)}


def evidence(db, calibration_id):
    rows = {}
    for score in db.query(models.RatingGateScore).filter_by(calibration_id=calibration_id, slice="all"):
        rows.update(score.per_event)
    return rows


def compare_evidence(candidate, champion, *, complexity=True):
    keys = sorted(candidate.keys() & champion.keys())
    if len(keys) < 1500:
        return {"passed": False, "reason": "need 1500 common out-of-sample matches", "n": len(keys)}
    weeks = {monday(datetime.fromtimestamp(candidate[k]["time"], timezone.utc)) for k in keys}
    if len(weeks) < 2:
        return {"passed": False, "reason": "need two weeks", "n": len(keys)}
    def paired(metric, chosen):
        return paired_cluster_bootstrap({k: (candidate[k]["event"], candidate[k][metric]) for k in chosen},
                                         {k: (champion[k]["event"], champion[k][metric]) for k in chosen}, seed=7)
    brier = paired("brier", keys)
    guards = {"log_loss": paired("log_loss", keys)["mean_diff"] <= 0.002,
              "points": np.mean([candidate[k]["mae"] for k in keys]) <= 1.01 * np.mean([champion[k]["mae"] for k in keys]),
              "coverage": 0.76 <= np.mean([candidate[k]["coverage"] for k in keys]) <= 0.84}
    for sl in ("first", "playoff", "late"):
        chosen = [k for k in keys if candidate[k][sl]]
        guards[sl] = bool(chosen) and paired("brier", chosen)["hi90"] <= (0.0 if sl == "late" else 0.003)
    first_keys = [k for k in keys if candidate[k]["first"]]
    guards["first_score_bias"] = bool(first_keys) and abs(sum(candidate[k]["score_error"] for k in first_keys)) <= 0.03 * max(sum(candidate[k]["score_total"] for k in first_keys), 1e-9)
    passed = brier["events"] >= 2 and brier["hi90"] < 0 and brier["mean_diff"] <= (-0.0015 if complexity else 0) and all(guards.values())
    return {"passed": bool(passed), "n": len(keys), "weeks": len(weeks), "brier": brier, "guards": {k: bool(v) for k,v in guards.items()}}


def production_evidence(db, season, through):
    t = captured_table(db, season, through=through)
    if not t:
        return {}
    # Frozen production probabilities in the feature record are never re-estimated.
    rows = loss_rows(t, identity())
    for i, key in enumerate(t["key"]):
        if str(key) not in rows:
            continue
        r, p, y = rows[str(key)], float(t["p_live"][i]), float(t["y"][i])
        q = np.clip(p, 1e-6, 1 - 1e-6)
        r.update(brier=(p-y)**2, log_loss=float(-(y*np.log(q)+(1-y)*np.log(1-q))),
                 mae=float((abs(t["rm"][i]-t["ra"][i])+abs(t["bm"][i]-t["ba"][i]))/2))
    return rows


def activate(db, calibration_id, *, now=None):
    if not settings.rating_autotune or not settings.rating_prediction_log_enabled:
        raise ValueError("RATING_AUTOTUNE is off; active bundle is pinned")
    row = db.get(models.RatingCalibration, calibration_id)
    if row is not None and row.recipe.startswith("flag-rp-"):
        raise ValueError("RP candidates cannot activate before prospective gates, serving integration and full rehearsal")
    if row is not None and row.recipe.startswith("state-map-total-"):
        raise ValueError("state candidates cannot activate before the component adapter, rating-impact gate and full rehearsal")
    if row is None or row.recipe != RECIPE or row.status == "rejected":
        raise ValueError("unknown or rejected calibration")
    bundle = ensure_bundle(db, row.season)
    if bundle.frozen or db.get(models.FrozenSeason, row.season):
        raise ValueError("season is frozen")
    if bundle.calibration_id == row.id:
        return {"status": "already_active"}
    if row.season < 2027:
        return {"status": "gate_failed", "gate": {"passed": False, "reason": "examined seasons are development evidence; require 2027 or later"}}
    now = utc(now or datetime.now(timezone.utc))
    if utc(row.fitted_through) >= now:
        raise ValueError("fit is in the future")
    previous = db.get(models.RatingCalibration, bundle.calibration_id) if bundle.calibration_id else None
    validate_params(row.params, previous.params if previous else identity())
    evaluate_version(db, row, now)
    baseline = production_evidence(db, row.season, now)
    verdict = compare_evidence(evidence(db, row.id), baseline)
    if previous:
        evaluate_version(db, previous, now)
        current = compare_evidence(evidence(db, row.id), evidence(db, previous.id), complexity=False)
        verdict["against_active"] = current
        verdict["passed"] = verdict["passed"] and current["passed"]
    row.fit_metrics = {**row.fit_metrics, "gate": verdict}
    if not verdict["passed"]:
        db.flush()
        return {"status": "gate_failed", "gate": verdict}
    if previous:
        previous.status = "retired"
    bundle.previous_id, bundle.calibration_id = bundle.calibration_id, row.id
    bundle.bundle_version += 1
    bundle.updated_at = now
    row.status, row.activated_at = "active", now
    db.flush()
    return {"status": "active", "bundle_version": bundle.bundle_version, "id": row.id}


def rollback(db, season, *, now=None):
    bundle = ensure_bundle(db, season)
    if bundle.calibration_id is None:
        return {"status": "already_baseline"}
    current = db.get(models.RatingCalibration, bundle.calibration_id)
    current.status = "rolled_back"
    bundle.calibration_id, bundle.previous_id = bundle.previous_id, None
    bundle.bundle_version += 1
    bundle.updated_at = utc(now or datetime.now(timezone.utc))
    if bundle.calibration_id:
        db.get(models.RatingCalibration, bundle.calibration_id).status = "active"
    db.flush()
    return {"status": "rolled_back", "bundle_version": bundle.bundle_version}


def safety_check(db, season, *, now=None):
    bundle = ensure_bundle(db, season)
    if not bundle.calibration_id:
        return {"status": "baseline"}
    now = utc(now or datetime.now(timezone.utc))
    current = db.get(models.RatingCalibration, bundle.calibration_id)
    evaluate_version(db, current, now)
    recent = (now - timedelta(days=14)).timestamp()
    active = {k: r for k, r in evidence(db, current.id).items() if r["time"] >= recent}
    baseline = production_evidence(db, season, now)
    keys = sorted(active.keys() & baseline.keys())
    if len(keys) < 150:
        return {"status": "insufficient_evidence"}
    comparison = paired_cluster_bootstrap({k: (active[k]["event"], active[k]["brier"]) for k in keys},
                                           {k: (baseline[k]["event"], baseline[k]["brier"]) for k in keys}, seed=7)
    if comparison["lo90"] > 0:
        # The frozen production baseline is the known safety target. Don't roll into an untested prior fit.
        bundle.previous_id = None
        result = rollback(db, season, now=now)
        return {**result, "comparison": comparison}
    return {"status": "kept", "comparison": comparison}


def weekly_job(db, season, *, now, code_sha):
    if not settings.rating_autotune or not settings.rating_prediction_log_enabled:
        return {"status": "disabled"}
    bundle = ensure_bundle(db, season)
    if db.get(models.FrozenSeason, season):
        return {"status": "season_frozen"}
    safety = safety_check(db, season, now=now)
    if bundle.frozen or safety["status"] == "rolled_back":
        db.commit()
        return {"status": "frozen" if bundle.frozen else "rolled_back", "safety": safety}
    result = fit_week(db, season, monday(now), code_sha)
    versions = db.query(models.RatingCalibration).filter_by(season=season, recipe=RECIPE).filter(models.RatingCalibration.status != "rejected").all()
    for version in versions:
        evaluate_version(db, version, now)
    # Promote the earliest prospectively validated fit; a brand-new fit has no evidence.
    # Subsequent versions also earn evidence before activation (conservative until rehearsal).
    for version in sorted(versions, key=lambda v: v.version):
        if version.status != "candidate":
            continue
        decision = activate(db, version.id, now=now)
        if decision["status"] == "active":
            result["activation"] = decision
            break
    result["safety"] = safety
    db.commit()
    return result
