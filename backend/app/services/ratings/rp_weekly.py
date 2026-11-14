# Candidate per-RP corrections start at identity and learn only earlier forecasts.
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import numpy as np

from app.services.ratings.calibrations import monday
from app.services.ratings.component_spec import number
from app.services.ratings.rp_fitting import calibrated, logit
from app.services.ratings.state_fitting import bounded_logistic
from app.services.ratings.state_inputs import digest

RECIPE = "flag-rp-weekly-v1"
POLICY = {"minimum_labels":150,"minimum_each_class":5,"prior_precision":[4.,16.],
          "intercept_bounds":[-2.,2.],"slope_bounds":[.25,4.],
          "intercept_weekly_step":.25,"slope_weekly_factor":1.5}


def identity():
    return {"intercept":0.,"slope":1.,"reference":"asof_base_logit"}


def implementation_sha256():
    folder = Path(__file__).resolve().parent
    return digest({name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in
                   ("rp_weekly.py","flag_rp.py","component_spec.py","rp_fitting.py","state_fitting.py","research_filter.py")})


def validate_correction(c, previous=None):
    if set(c) != set(identity()) or c["reference"] != "asof_base_logit":
        raise ValueError("invalid RP correction schema")
    a,b = number(c["intercept"]),number(c["slope"])
    if a is None or b is None or not -2 <= a <= 2 or not .25 <= b <= 4:
        raise ValueError("RP correction outside hard bounds")
    if previous is not None and (abs(a-previous["intercept"]) > .25+1e-10 or max(b/previous["slope"],previous["slope"]/b) > 1.5+1e-10):
        raise ValueError("RP correction exceeds weekly movement bounds")


def fit_roles(rows, names, previous=None):
    previous = previous or {name:identity() for name in names}
    if set(previous) != set(names):
        raise ValueError("RP predecessor has another spec")
    corrections, counts = {}, {}
    for name in names:
        before = previous[name]
        validate_correction(before)
        observed = [r for r in rows if r["rp"] == name]
        for r in observed:
            if type(r["y"]) is not bool or any(number(r[k]) is None or not 0 < r[k] < 1 for k in ("p","base")):
                raise ValueError("invalid RP training label or probability")
        earned = sum(r["y"] for r in observed)
        supported = len(observed) >= POLICY["minimum_labels"] and min(earned,len(observed)-earned) >= POLICY["minimum_each_class"]
        counts[name] = {"n":len(observed),"earned":earned,"fitted":supported}
        if not supported:
            correction = identity()
        else:
            offset = logit(np.array([r["base"] for r in observed]))
            x = np.column_stack((np.ones(len(observed)),logit(np.array([r["p"] for r in observed]))-offset))
            y = np.array([r["y"] for r in observed],dtype=float)
            lo = np.array([max(-2.,before["intercept"]-.25),max(.25,before["slope"]/1.5)])
            hi = np.array([min(2.,before["intercept"]+.25),min(4.,before["slope"]*1.5)])
            coef = bounded_logistic(x,y,offset,np.array([0.,1.]),np.array(POLICY["prior_precision"]),lo,hi)
            correction = {"intercept":float(coef[0]),"slope":float(coef[1]),"reference":"asof_base_logit"}
        validate_correction(correction,before)
        corrections[name] = correction
    return corrections,counts


def walk_forward(data, rows):
    names = [rp.name for rp in data.spec.bonus_rps]
    posted = {card.match_key:when for card,_,when,_ in data.matches}
    pending = sorted((posted[r["key"].split("/")[0]],r["key"],r) for r in rows
                     if posted[r["key"].split("/")[0]] is not None)
    observed_weeks = sorted({monday(datetime.fromtimestamp(r["time"],timezone.utc)) for r in rows})
    weeks = []
    if observed_weeks:
        cutoff = observed_weeks[0]
        while cutoff <= observed_weeks[-1]:
            weeks.append(cutoff)
            cutoff += timedelta(days=7)
    previous, training, timeline, applied = None, [], [], 0
    for cutoff in weeks:
        while applied < len(pending) and pending[applied][0] < cutoff.timestamp():
            training.append(pending[applied][2])
            applied += 1
        corrections,counts = fit_roles(training,names,previous)
        timeline.append({"cutoff":cutoff.isoformat(),"corrections":corrections,"counts":counts,
                         "training_sha256":digest(training)})
        previous = corrections
    lookup = {p["cutoff"]:p for p in timeline}
    probabilities = {}
    for r in rows:
        week = monday(datetime.fromtimestamp(r["time"],timezone.utc)).isoformat()
        probabilities[r["key"]] = calibrated(r["p"],r["base"],lookup[week]["corrections"][r["rp"]])
    return probabilities,timeline
