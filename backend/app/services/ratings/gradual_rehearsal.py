# Offline application experiment. It never changes an operational active bundle.
from __future__ import annotations

import copy
from dataclasses import asdict, replace
import math
from time import perf_counter

import numpy as np
from sqlalchemy import func

from app.db import models
from app.services.ratings import state_fitting as state, weekly_rehearsal as weekly
from app.services.ratings.fast_innovations import PackedInnovations
from app.services.ratings.research_fit import fit_likelihood, fittable, innovations, loglik
from app.services.ratings.state_inputs import digest

RECIPE = "state-map-gradual-rehearsal-v1"
POLICY = {"fractions":[1.,.5,.25,.125,.0625,.03125,.015625],"likelihood_tolerance":1e-9,
          "parent":"actually active version","selection_clock":"09:00 UTC; recheck impact at 10:00 publication",
          "layer_fit":"once for chosen state; failure rejects update","newest_brier_tolerance":0.,
          "first_match_brier_tolerance":.001,"serving_eligible":False}


def interpolate(start, goal, fraction):
    if not 0 < fraction <= 1:
        raise ValueError("state step fraction must be in (0,1]")
    state.validate_params(asdict(start))
    state.validate_params(asdict(goal))
    if fraction == 1:
        return goal
    return replace(start,**{name:getattr(start,name) if getattr(start,name) == getattr(goal,name) else
        math.exp(math.log(getattr(start,name))+fraction*math.log(getattr(goal,name)/getattr(start,name))) for name in state.NAMES})


def choose_step(start, goal, objective, impact):
    baseline = objective(start)
    trials = []
    for fraction in POLICY["fractions"]:
        params = interpolate(start,goal,fraction)
        value = objective(params)
        acceptable = math.isfinite(value) and value <= baseline+POLICY["likelihood_tolerance"]
        movement = impact(params) if acceptable else None
        trials.append({"fraction":fraction,"objective":value if math.isfinite(value) else None,
                       "likelihood_passed":acceptable,"impact":movement})
        if acceptable and movement["passed"]:
            return params,{"fraction":fraction,"start_objective":baseline,"trials":trials}
    return None,{"fraction":0.,"start_objective":baseline,"trials":trials}


def fit_job(db, year, cutoff, code_sha, replay, active, anchors):
    started = perf_counter()
    cutoff = state.layers.utc(cutoff)
    if cutoff != state.layers.monday(cutoff):
        raise ValueError("gradual fit cutoff must be Monday 00:00 UTC")
    parent = db.get(models.RatingCalibration,active)
    if parent is None or parent.season != year or parent.recipe not in (state.BOOTSTRAP_RECIPE,RECIPE):
        raise ValueError("gradual fitting needs its actual active parent")
    parent_sha = digest(parent.params)
    existing = db.query(models.RatingCalibration).filter_by(season=year,recipe=RECIPE,fitted_through=cutoff).first()
    if existing:
        if existing.params["parent_active"] != active or existing.params["parent_sha256"] != parent_sha:
            raise ValueError("committed gradual fit has another active parent")
        return {"status":"already_fitted","id":existing.id}
    if parent.recipe != state.BOOTSTRAP_RECIPE and state.layers.utc(parent.fitted_through) >= cutoff:
        raise ValueError("a later gradual fit cannot initialize an earlier one")
    bootstrap = state.latest(db,year,state.BOOTSTRAP_RECIPE)
    if bootstrap is None:
        raise ValueError("missing gradual bootstrap")
    artifact = bootstrap.params["artifact"]
    start,centre,_ = weekly.version(db,active)
    inputs,data_sha,skipped = state.inputs_for(db,year,cutoff.timestamp(),bootstrap.params["context"],updated_before=cutoff.timestamp())
    eligible = sum(r.red_points is not None and r.blue_points is not None for r in inputs.research.season.results)
    if eligible < weekly.POLICY["minimum_fit_matches"]:
        return {"status":"insufficient_actual_matches"}
    pull = state.WeeklyPull(artifact,start)
    names = state.NAMES if cutoff.timestamp() < inputs.times[0]+28*86400 else tuple(n for n in state.NAMES if n not in ("beta","rookie"))
    names = fittable(inputs,names)
    packed = PackedInnovations(inputs) if weekly.POLICY["compiled"] else None
    parity = {"start":packed.verify(start)} if packed else {}
    errors = []
    fitted_layers = copy.deepcopy(centre)
    selected,selection = None,None
    recent_change = None

    def deadline():
        remaining = weekly.POLICY["fit_wall_seconds"]-(perf_counter()-started)
        if remaining <= 0:
            raise TimeoutError("gradual fitting exceeded its wall-time budget")
        return remaining

    try:
        goal_fit = fit_likelihood(inputs,start,names,int(cutoff.timestamp()),pull=pull,
            max_evals=weekly.POLICY["state_max_evals"],restarts=weekly.POLICY["state_restarts"],
            max_seconds=deadline(),innovations_fn=packed,zero_terms=())
    except TimeoutError as exc:
        errors.append(str(exc))
        goal_fit = {"params":start,"converged":False,"timed_out":True}
    goal = goal_fit["params"]
    if packed:
        parity["goal"] = packed.verify(goal)
    if not goal_fit["converged"]:
        errors.append("optimizer did not converge; exhausted fits cannot become candidates")
    try:
        state.validate_params(asdict(goal))
        for name in state.NAMES:
            if name not in names and getattr(goal,name) != getattr(start,name):
                raise ValueError(f"{name} changed after its fitting freeze")
            lo,hi = pull.bounds(name)
            if not lo-1e-9 <= math.log(getattr(goal,name)) <= hi+1e-9:
                raise ValueError(f"{name} exceeds bounds relative to the active model")
    except ValueError as exc:
        errors.append(str(exc))

    def objective(params):
        deadline()
        nu,s = packed(params,int(cutoff.timestamp())) if packed else innovations(inputs,params,int(cutoff.timestamp()))
        return -loglik(nu,s)+sum(pull.penalty(name,math.log(getattr(params,name))) for name in names)

    def impact(params):
        deadline()
        model = replay.new_model(params)
        for record in replay.ordered:
            model.observe(record["result"])
        deadline()
        # The original anchors are reused for every trial; previews never advance them.
        return weekly.rating_impact(replay.models[active],model,replay.total,anchors)[0]

    if not errors:
        try:
            selected,selection = choose_step(start,goal,objective,impact)
            if selected is None:
                errors.append("no positive state step passes likelihood and rating-impact checks")
        except TimeoutError as exc:
            errors.append(str(exc))
    if selected is not None:
        if packed:
            parity["fitted"] = packed.verify(selected)
        try:
            deadline()
            _,table,_,_ = state.replay(inputs,selected)
            fitted_layers = state.fit_layers(table,centre)
            state.layers.validate_params(fitted_layers,centre)
            _,old,_,_ = state.replay(inputs,start)
            mask = table["time"] >= cutoff.timestamp()-14*86400
            recent,baseline = state.layers.subset(table,mask),state.layers.subset(old,mask)
            decided = (recent["y"] != .5) & np.isfinite(recent["ra"]) & np.isfinite(recent["ba"])
            if decided.any():
                a,b = state.layers.apply(recent,fitted_layers)[2],state.layers.apply(baseline,centre)[2]
                recent_change = float(np.mean((a[decided]-recent["y"][decided])**2-(b[decided]-baseline["y"][decided])**2))
                if recent_change > .001:
                    errors.append("last-two-week Brier sanity check failed")
            deadline()
        except (ValueError,np.linalg.LinAlgError,TimeoutError) as exc:
            errors.append(str(exc))
    params = selected if selected is not None and not errors else start
    if errors:
        fitted_layers = copy.deepcopy(centre)
    metrics = {"kind":"offline_gradual_state_fit","goal_fit":{k:v for k,v in goal_fit.items() if k != "params"},
        "selection":selection,"errors":errors,"skipped":skipped,"recent_insample_brier_change":recent_change,
        "rehearsal_parity":parity,"rehearsal_seconds":perf_counter()-started,"serving_eligible":False}
    payload = {"schema":RECIPE,"state":asdict(params),"layers":fitted_layers,"goal_state":asdict(goal),
        "parent_active":active,"parent_sha256":parent_sha,"bootstrap_id":bootstrap.id,
        "bootstrap_sha256":digest(bootstrap.params),"data_sha256":data_sha,
        "fraction":selection["fraction"] if selection and not errors else 0.}
    digest(payload)
    if digest(parent.params) != parent_sha:
        raise ValueError("gradual fitting rewrote its active parent")
    version = (db.query(func.max(models.RatingCalibration.version)).filter_by(season=year,recipe=RECIPE).scalar() or 0)+1
    row = models.RatingCalibration(season=year,recipe=RECIPE,version=version,params=payload,
        fitted_through=cutoff,matches=len(inputs.times),code_sha=code_sha,fit_metrics=metrics,status="rejected" if errors else "candidate")
    db.add(row)
    db.commit()
    return {"status":row.status,"id":row.id,"matches":row.matches,"errors":errors}
