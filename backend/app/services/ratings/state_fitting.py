# Versioned MAP state candidates. Activation stays blocked until the component bridge and rehearsal exist.
from __future__ import annotations

from dataclasses import asdict
import copy
from datetime import datetime, timezone
import math

import numpy as np
from sqlalchemy import func

from app.core.config import settings
from app.db import models
from app.services.ratings import calibrations as layers
from app.services.ratings.research_filter import ScaleFreeParams
from app.services.ratings.research_fit import Pull, fit_likelihood, scale_free_z
from app.services.ratings.research_models import mean_share
from app.services.ratings.research_win import (
    ScoreCalibration, context, correction_features, side_arrays, score_design, _score_sd,
    sigmoid, TEMP_POOLED_SD, TEMP_CONTEXT_SD, WEIGHT_SD,
)
from app.services.ratings.state_bootstrap import BOOTSTRAP_RECIPE, NAMES, RECIPE, validate_bootstrap, validate_params
from app.services.ratings.state_inputs import digest, inputs_for, load_results, season_boundary


class WeeklyPull(Pull):
    def __init__(self, bootstrap, previous):
        super().__init__(bootstrap["pull"]["centre"], bootstrap["pull"]["width"])
        self.previous = previous

    def bounds(self, name):
        lo, hi = super().bounds(name)
        centre = math.log(getattr(self.previous, name))
        return max(lo, centre-math.log(1.5)), min(hi, centre+math.log(1.5))


def latest(db, season, recipe):
    return db.query(models.RatingCalibration).filter_by(season=season, recipe=recipe).filter(
        models.RatingCalibration.status != "rejected").order_by(models.RatingCalibration.version.desc()).first()


def install_bootstrap(db, artifact, code_sha):
    season = artifact["season"]
    validate_bootstrap(artifact, season)
    bundle = layers.ensure_bundle(db, season)
    if bundle.frozen or db.get(models.FrozenSeason, season):
        raise ValueError("season is frozen")
    previous, skipped = load_results(db, season-1, season_boundary(season))
    if not previous:
        raise ValueError("previous-season actual results are required; no scoring-unit stand-in is allowed")
    z = scale_free_z(previous, ScaleFreeParams(**artifact["prior_params"]))
    context = {"z_previous": z, "previous_unit": mean_share(previous)}
    if context["previous_unit"] <= 0:
        raise ValueError("previous-season scoring unit must be positive")
    payload = {"artifact": artifact, "context": context, "previous_data_sha256": digest([asdict(r) for r in previous])}
    stored = latest(db, season, BOOTSTRAP_RECIPE)
    if stored and digest(stored.params) == digest(payload):
        return stored
    version = (db.query(func.max(models.RatingCalibration.version)).filter_by(season=season, recipe=BOOTSTRAP_RECIPE).scalar() or 0)+1
    stored = models.RatingCalibration(season=season, recipe=BOOTSTRAP_RECIPE, version=version, params=payload,
        fitted_through=datetime.fromtimestamp(season_boundary(season), timezone.utc), matches=len(previous),
        status="candidate", code_sha=code_sha, fit_metrics={"kind": "bootstrap", "previous_skipped": skipped, "serving_eligible": False})
    db.add(stored)
    db.flush()
    return stored


def feature(model, card, prediction, here, total, first):
    block = model.blocks[card.event_key]
    def side(teams):
        ids = [block.index[t] for t in teams]
        means = [float(block.mean[i]) for i in ids]
        return max(means), min(means), max(math.sqrt(max(float(block.cov[i,i]),0)) for i in ids)
    best_r, worst_r, sd_r = side(card.red)
    best_b, worst_b, sd_b = side(card.blue)
    teams = card.red+card.blue
    return {"rm": prediction.red_mean, "bm": prediction.blue_mean, "rv": prediction.red_var, "bv": prediction.blue_var,
        "mv": prediction.red_var+prediction.blue_var, "red_fouls": 0.0, "blue_fouls": 0.0,
        "u": model.unit.value(), "best_r": best_r, "worst_r": worst_r, "best_b": best_b, "worst_b": worst_b,
        "sd_r": sd_r, "sd_b": sd_b, "min_here": min(here.get((t,card.event_key),0) for t in teams),
        "min_before": min(total.get(t,0)-here.get((t,card.event_key),0) for t in teams),
        "playoff": float(card.comp_level != "qm"), "season_frac": max(card.event_start-first,0)/(86400*120), "p_live": .5}


def replay(inputs, params, *, record=True):
    model = inputs.model(params)
    here, total, rows = {}, {}, []
    results = inputs.research.season.results
    first = min((r.card.event_start for r in results), default=0)
    for result in results:
        card = result.card
        if record:
            p = model.predict(card)
            row = feature(model, card, p, here, total, first)
            row.update(key=card.match_key, event=card.event_key, time=card.time,
                       ra=np.nan if result.red_points is None else result.red_points,
                       ba=np.nan if result.blue_points is None else result.blue_points,
                       y=.5 if result.red_score == result.blue_score else float(result.red_score > result.blue_score))
            rows.append(row)
        model.observe(result)
        for team in card.red+card.blue:
            here[(team,card.event_key)] = here.get((team,card.event_key),0)+1
            total[team] = total.get(team,0)+1
    table = {key: np.array([r[key] for r in rows]) for key in rows[0]} if rows else {}
    return model, table, here, total


def box_quadratic(a, b, lo, hi, initial):
    coef = np.clip(np.array(initial,dtype=float),lo,hi)
    for _ in range(10000):
        for j in range(len(coef)):
            coef[j] = np.clip((b[j]-a[j]@coef+a[j,j]*coef[j])/a[j,j],lo[j],hi[j])
        projected = np.clip(coef-(a@coef-b)/np.diag(a),lo,hi)
        if np.max(np.abs(projected-coef)) < 1e-9:
            return coef
    raise ValueError("bounded quadratic optimizer did not converge")


def bounded_logistic(f, y, offset, centre, precision, lo, hi):
    w = np.clip(np.array(centre,dtype=float),lo,hi)
    def objective(weights):
        logits = offset+f@weights
        delta = weights-centre
        return float(np.sum(np.logaddexp(0,logits)-y*logits)+.5*np.sum(precision*delta*delta))
    for _ in range(100):
        p = sigmoid(offset+f@w)
        h = f.T@(f*(p*(1-p))[:,None])+np.diag(precision)
        gradient = f.T@(p-y)+precision*(w-centre)
        proposed = box_quadratic(h,h@w-gradient,lo,hi,w)
        if np.max(np.abs(proposed-w)) < 1e-8:
            return proposed
        old = objective(w)
        for step in range(40):
            next_w = w+(proposed-w)*.5**step
            if objective(next_w) <= old+1e-10:
                w = next_w
                break
        else:
            raise ValueError("bounded logistic line search failed")
    raise ValueError("bounded logistic optimizer did not converge")


def bounded_score_fit(table, centre):
    sides = side_arrays(table)
    x = score_design(sides, True)
    residual = sides["y"]-sides["x"]
    noise = max(float(np.var(residual)), 1e-9)
    precision = 1/_score_sd(True)**2
    a = x.T@x/noise+np.diag(precision)
    initial = np.array(centre["score_coef"])
    b = x.T@residual/noise+precision*initial
    lo = np.maximum(-layers.SCORE_LIMITS,initial-layers.SCORE_STEP)
    hi = np.minimum(layers.SCORE_LIMITS,initial+layers.SCORE_STEP)
    return ScoreCalibration(box_quadratic(a,b,lo,hi,initial),True)


def fit_layers(table, centre):
    decided = (table["y"] != .5) & np.isfinite(table["ra"]) & np.isfinite(table["ba"])
    score = bounded_score_fit(table, centre)
    base, y, ctx = layers.win_base(table)[decided], table["y"][decided], context(table)[decided]
    def temperature(mask, mean, prior_sd, previous):
        return float(bounded_logistic(base[mask,None],y[mask],np.zeros(int(mask.sum())),np.array([mean]),
            np.array([1/prior_sd**2]),np.array([max(.25,previous/1.5)]),np.array([min(4,previous*1.5)]))[0])
    pooled = temperature(np.ones(len(y),bool),centre["pooled_temperature"],TEMP_POOLED_SD,centre["pooled_temperature"])
    temps = np.array([temperature(ctx==c,pooled+centre["temperatures"][c]-centre["pooled_temperature"],
                                  TEMP_CONTEXT_SD,centre["temperatures"][c]) for c in range(4)])
    weights = np.array(centre["weights"])
    weights = bounded_logistic(correction_features(table)[decided],y,temps[ctx]*base,weights,np.full(len(weights),1/WEIGHT_SD**2),
                              np.maximum(-1,weights-.25),np.minimum(1,weights+.25))
    return {"schema": layers.SCHEMA, "score_coef": score.coef.tolist(), "temperatures": temps.tolist(),
            "pooled_temperature": pooled, "weights": weights.tolist(), "blend": centre["blend"]}


def fit_week(db, season, through, code_sha, *, max_evals=400, innovations_factory=None):
    through = layers.utc(through)
    if through != layers.monday(through):
        raise ValueError("state fit cutoff must be Monday 00:00 UTC")
    bundle = layers.ensure_bundle(db, season)
    if bundle.frozen or db.get(models.FrozenSeason, season):
        return {"status": "frozen"}
    bootstrap = latest(db, season, BOOTSTRAP_RECIPE)
    if bootstrap is None:
        return {"status": "missing_state_bootstrap"}
    existing = db.query(models.RatingCalibration).filter_by(season=season, recipe=RECIPE, fitted_through=through).first()
    if existing:
        return {"status": "already_fitted", "id": existing.id}
    bootstrap_id = bootstrap.id
    bootstrap_payload = copy.deepcopy(bootstrap.params)
    artifact = bootstrap_payload["artifact"]
    validate_bootstrap(artifact, season)
    inputs, data_sha, skipped = inputs_for(db, season, through.timestamp(), bootstrap_payload["context"],updated_before=through.timestamp())
    eligible = sum(r.red_points is not None and r.blue_points is not None for r in inputs.research.season.results)
    if eligible < 150:
        return {"status": "insufficient_actual_matches"}
    previous = latest(db, season, RECIPE)
    if previous and layers.utc(previous.fitted_through) >= through:
        raise ValueError("state fit cutoffs must advance; a later fit cannot initialize an earlier one")
    if previous and previous.params["bootstrap_id"] != bootstrap.id:
        previous = None
    start = validate_params(previous.params["state"] if previous else artifact["params"])
    pull = WeeklyPull(artifact, start)
    names = NAMES if through.timestamp() < inputs.times[0]+28*86400 else tuple(n for n in NAMES if n not in ("beta", "rookie"))
    centre = copy.deepcopy(previous.params["layers"] if previous else artifact["layers"])
    previous_id = previous.id if previous else None
    db.commit()  # Release the season lock before the expensive optimizer; recheck before publishing.
    errors = []
    accelerated = innovations_factory(inputs,start) if innovations_factory else None
    try:
        fit = fit_likelihood(inputs, start, names, int(through.timestamp()), pull=pull, max_evals=max_evals, restarts=1,
                             max_seconds=3000,innovations_fn=accelerated)
    except TimeoutError as exc:
        errors.append(str(exc))
        fit = {"params":start,"converged":False,"timed_out":True}

    params = fit["params"]
    try:
        validate_params(asdict(params))
        for name in NAMES:
            value = math.log(getattr(params, name))
            lo, hi = pull.bounds(name)
            if not lo-1e-9 <= value <= hi+1e-9:
                raise ValueError(f"{name} exceeds hard bounds or weekly step limit")
    except ValueError as exc:
        errors.append(str(exc))
    if not fit["converged"]:
        errors.append("optimizer did not converge; exhausted fits cannot become candidates")
    fitted_layers = centre
    metrics = {k:v for k,v in fit.items() if k != "params"}
    if not errors:
        _, table, _, _ = replay(inputs, params)
        try:
            fitted_layers = fit_layers(table, centre)
            layers.validate_params(fitted_layers, centre)
        except (ValueError,np.linalg.LinAlgError) as exc:
            errors.append(str(exc))
        if not errors:
            _, old, _, _ = replay(inputs, start)
            recent = table["time"] >= through.timestamp()-14*86400
            selected = layers.subset(table, recent)
            baseline = layers.subset(old, recent)
            decided = (selected["y"] != .5) & np.isfinite(selected["ra"]) & np.isfinite(selected["ba"])
            if decided.any():
                change = float(np.mean((layers.apply(selected,fitted_layers)[2][decided]-selected["y"][decided])**2 - (layers.apply(baseline,centre)[2][decided]-baseline["y"][decided])**2))
                metrics["recent_insample_brier_change"] = change
                if change > .001:
                    errors.append("last-two-week Brier sanity check failed")
    bundle = layers.ensure_bundle(db, season)
    current_bootstrap = latest(db, season, BOOTSTRAP_RECIPE)
    current_previous = latest(db, season, RECIPE)
    if current_previous and current_previous.params["bootstrap_id"] != bootstrap_id:
        current_previous = None
    if bundle.frozen or db.get(models.FrozenSeason, season):
        return {"status": "frozen_during_fit"}
    existing = db.query(models.RatingCalibration).filter_by(season=season,recipe=RECIPE,fitted_through=through).first()
    if existing:
        return {"status":"already_fitted","id":existing.id}
    if current_bootstrap.id != bootstrap_id or (current_previous.id if current_previous else None) != previous_id:
        return {"status": "superseded_during_fit"}
    _, current_sha, _ = inputs_for(db, season, through.timestamp(), bootstrap_payload["context"],updated_before=through.timestamp())
    if current_sha != data_sha:
        return {"status": "inputs_changed_during_fit"}
    payload = {"schema": RECIPE, "bootstrap_id": bootstrap_id, "state": asdict(params), "layers": fitted_layers,
               "data_sha256": data_sha, "bootstrap_sha256": digest(bootstrap_payload)}
    # Reject nonfinite payloads before PostgreSQL's JSON encoder sees them.
    digest(payload)
    version = (db.query(func.max(models.RatingCalibration.version)).filter_by(season=season, recipe=RECIPE).scalar() or 0)+1
    row = models.RatingCalibration(season=season, recipe=RECIPE, version=version, params=payload,
        fitted_through=through, matches=len(inputs.times), status="rejected" if errors else "candidate", code_sha=code_sha,
        fit_metrics={**metrics, "errors": errors, "skipped": skipped, "fitted_names": list(names), "serving_eligible": False,
                     "layer_optimizer": "box-constrained MAP",
                     "release_blockers": ["total-state component adapter", "rating impact gate", "full rehearsal"]})
    db.add(row)
    db.flush()
    return {"status": row.status, "id": row.id, "matches": row.matches, "errors": errors}


def weekly_job(db, season, *, now, code_sha, artifact=None, max_evals=400):
    if not settings.rating_state_fit_enabled:
        return {"status": "disabled"}
    if artifact is not None:
        if artifact["season"] != season:
            raise ValueError("state bootstrap season differs from job season")
        install_bootstrap(db, artifact, code_sha)
    result = fit_week(db, season, layers.monday(now), code_sha, max_evals=max_evals)
    db.commit()
    return result
