# Offline RP fitting. Training, validation and evaluation seasons stay separate.
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from app.services.ratings.component_spec import bonus_flags, get_spec, number
from app.services.ratings.flag_rp import FlagRpModel, FlagRpParams, RECIPE, sigmoid
from app.services.ratings.replay import MatchCard
from app.services.ratings.research_eval import paired_cluster_bootstrap
from app.services.ratings.state_fitting import box_quadratic
from app.services.ratings.state_inputs import digest

SCHEMA = "flag-rp-fit-v1"
BOUNDS = {"prior_var":(.1,4.),"match_var":(.0001,.1),"event_var":(.001,1.)}
POLICY = {"training_seasons":[2022,2023],"validation_season":2024,"coordinate_rounds":2,
          "coordinate_factor":2.,"calibration_ridge":.001,
          "validation_brier_tolerance":.0005,"validation_log_loss_tolerance":.002}


def implementation_sha256():
    folder = Path(__file__).resolve().parent
    return digest({name:hashlib.sha256((folder/name).read_bytes()).hexdigest()
                   for name in ("rp_fitting.py","flag_rp.py","component_spec.py","state_fitting.py","research_filter.py")})


@dataclass
class PublishedSeason:
    spec: object
    # (card, flags, publication time, event type)
    matches: list
    source_sha256: str
    skipped: dict


def load_published(cache, season):
    spec = get_spec(season)
    matches, sources, skipped = [], {}, Counter()
    for path in sorted((Path(cache)/str(season)).glob("*.json")):
        raw = path.read_bytes()
        payload = json.loads(raw)
        event = payload["event"]
        if not event["key"].lower().startswith(str(season)):
            raise ValueError("cached event belongs to another season")
        sources[path.name] = hashlib.sha256(raw).hexdigest()
        for m in payload["matches"]:
            if not str(m.get("key","")).lower().startswith(event["key"].lower()+"_") or str(m.get("event_key",event["key"])).lower() != event["key"].lower():
                raise ValueError("cached match belongs to another event")
            if m.get("comp_level") != "qm":
                skipped["not_qualification"] += 1
                continue
            when = number(m.get("actual_time"))
            if when is None or when <= 0:
                skipped["missing_actual_time"] += 1
                continue
            sides = m.get("alliances") or {}
            teams = {c:tuple(str(t).lower() for t in sides.get(c,{}).get("team_keys",[])) for c in ("red","blue")}
            if any(len(t) != 3 for t in teams.values()) or len(set(teams["red"]+teams["blue"])) != 6:
                skipped["invalid_lineup"] += 1
                continue
            if any((v := number(sides.get(c,{}).get("score"))) is None or v < 0 for c in sides):
                skipped["unplayed"] += 1
                continue
            posted = number(m.get("post_result_time"))
            posted = int(posted) if posted is not None and posted > when else None
            if posted is None:
                skipped["missing_or_invalid_publication"] += 1
            breakdown = m.get("score_breakdown") or {}
            flags = {c:bonus_flags(spec,{} if sides[c].get("dq_team_keys") else breakdown.get(c)) for c in ("red","blue")}
            card = MatchCard(m["key"].lower(),event["key"].lower(),int(when),"qm",int(when),teams["red"],teams["blue"])
            matches.append((card,flags,posted,event.get("event_type")))
    if not sources or not matches:
        raise ValueError("missing scored season for RP fitting")
    matches.sort(key=lambda r:(r[0].time,r[0].match_key))
    if len({r[0].match_key for r in matches}) != len(matches):
        raise ValueError("duplicate cached match")
    return PublishedSeason(spec,matches,digest(sources),dict(skipped))


def forecasts(data, params):
    model = FlagRpModel(data.spec,params)
    pending = sorted((posted,card.match_key,card,flags) for card,flags,posted,_ in data.matches if posted is not None)
    applied, rows = 0, []
    for card,flags,_,event_type in data.matches:
        while applied < len(pending) and pending[applied][0] < card.time:
            _,_,past,observed = pending[applied]
            model.observe(past,observed)
            applied += 1
        prediction = model.forecast(card)
        if event_type not in (0,1,2,3,4,5):
            continue
        for color in ("red","blue"):
            for rp, estimate in prediction[color].items():
                y = flags[color][rp]
                if type(y) is bool:
                    rows.append({"key":f"{card.match_key}/{color}/{rp}","event":card.event_key,"time":card.time,
                        "rp":rp,"y":y,"p":estimate["probability"],"base":estimate["base_rate"]})
    return rows


def logit(p):
    p = np.clip(p,1e-9,1-1e-9)
    return np.log(p)-np.log1p(-p)


def calibrated(p, base, calibration):
    offset = logit(base)
    return sigmoid(float(offset+calibration["intercept"]+calibration["slope"]*(logit(p)-offset)))


def weights(groups):
    out = []
    for rows in groups:
        counts = Counter(r["rp"] for r in rows)
        if not counts:
            raise ValueError("no eligible RP labels")
        out.extend(1/(len(groups)*len(counts)*counts[r["rp"]]) for r in rows)
    return np.array(out)


def objective(groups):
    rows = [r for group in groups for r in group]
    p = np.array([r["p"] for r in rows])
    y = np.array([r["y"] for r in rows],dtype=float)
    return float(weights(groups)@(-y*np.log(np.clip(p,1e-9,1))-(1-y)*np.log(np.clip(1-p,1e-9,1))))


def fit_calibration(groups):
    rows = [r for group in groups for r in group]
    offset = logit(np.array([r["base"] for r in rows]))
    x = np.column_stack((np.ones(len(rows)),logit(np.array([r["p"] for r in rows]))-offset))
    y = np.array([r["y"] for r in rows],dtype=float)
    w = weights(groups)
    centre, coef = np.array([0.,1.]),np.array([0.,1.])
    precision = POLICY["calibration_ridge"]*np.array([1.,4.])
    lo, hi = np.array([-2.,.25]),np.array([2.,4.])
    def loss(c):
        z = offset+x@c
        return float(w@(np.logaddexp(0,z)-y*z)+.5*np.sum(precision*(c-centre)**2))
    for _ in range(100):
        z = np.clip(offset+x@coef,-30,30)
        p = 1/(1+np.exp(-z))
        gradient = x.T@(w*(p-y))+precision*(coef-centre)
        hessian = x.T@(x*(w*p*(1-p))[:,None])+np.diag(precision)
        proposal = box_quadratic(hessian,hessian@coef-gradient,lo,hi,coef)
        if np.max(np.abs(proposal-coef)) < 1e-8:
            return {"intercept":float(coef[0]),"slope":float(coef[1]),"reference":"asof_base_logit"}
        old = loss(coef)
        for power in range(30):
            next_coef = coef+(proposal-coef)*.5**power
            if loss(next_coef) <= old+1e-12:
                coef = next_coef
                break
        else:
            raise ValueError("RP calibration line search failed")
    raise ValueError("RP calibration did not converge")


def fit(cache, *, progress=None):
    years = POLICY["training_seasons"]
    data = [load_published(cache,y) for y in years]
    trials, memo = [], {}
    def trial(params):
        key = tuple(asdict(params).values())
        if key not in memo:
            groups = [forecasts(d,params) for d in data]
            score = objective(groups)
            memo[key] = score
            trials.append({"params":asdict(params),"balanced_log_loss":score})
            if progress:
                progress(len(trials),score)
        return memo[key]
    params = FlagRpParams()
    best = trial(params)
    for _ in range(POLICY["coordinate_rounds"]):
        for name,(lo,hi) in BOUNDS.items():
            current = params
            for factor in (1/POLICY["coordinate_factor"],POLICY["coordinate_factor"]):
                value = min(hi,max(lo,getattr(current,name)*factor))
                candidate = replace(current,**{name:value})
                score = trial(candidate)
                if score < best-1e-12:
                    params,best = candidate,score
    groups = [forecasts(d,params) for d in data]
    return {"schema":SCHEMA,"model_recipe":RECIPE,"implementation_sha256":implementation_sha256(),
        "policy":{**POLICY,"training_seasons":list(years)},"params":asdict(params),"calibration":fit_calibration(groups),
        "source_sha256":{str(y):d.source_sha256 for y,d in zip(years,data)},
        "spec_sha256":{str(y):d.spec.fingerprint for y,d in zip(years,data)},
        "labels":{str(y):len(r) for y,r in zip(years,groups)},"trials":trials,
        "search":"two deterministic coordinate passes; finite search, not a global optimum",
        "serving_eligible":False}


def validate_artifact(artifact):
    if artifact["schema"] != SCHEMA or artifact["policy"] != POLICY or artifact["serving_eligible"] is not False:
        raise ValueError("unsupported RP fit artifact")
    if artifact["model_recipe"] != RECIPE or artifact["implementation_sha256"] != implementation_sha256():
        raise ValueError("RP artifact belongs to another implementation")
    if set(artifact["source_sha256"]) != {"2022","2023"} or set(artifact["spec_sha256"]) != {"2022","2023"}:
        raise ValueError("RP fit must use 2022–2023 only")
    if set(artifact["params"]) != set(BOUNDS):
        raise ValueError("incomplete RP parameters")
    params = FlagRpParams(**artifact["params"])
    if any(not lo <= getattr(params,k) <= hi for k,(lo,hi) in BOUNDS.items()):
        raise ValueError("RP parameters exceed training bounds")
    c = artifact["calibration"]
    if set(c) != {"intercept","slope","reference"} or c["reference"] != "asof_base_logit":
        raise ValueError("unsupported RP calibration")
    if number(c["intercept"]) is None or number(c["slope"]) is None or not -2 <= c["intercept"] <= 2 or not .25 <= c["slope"] <= 4:
        raise ValueError("RP calibration outside bounds")
    digest(artifact)
    return params,c


def compare(rows, challenger, *, calibrated_rows=None):
    expected = {r["key"]:r for r in rows}
    actual = {r["key"]:r for r in challenger}
    if len(expected) != len(rows) or len(actual) != len(challenger) or expected.keys() != actual.keys() or any((r["event"],r["y"]) != (actual[k]["event"],actual[k]["y"]) for k,r in expected.items()):
        raise ValueError("RP comparisons require identical labels")
    out = {}
    for rp in ["pooled",*sorted({r["rp"] for r in rows})]:
        keys = [k for k,r in expected.items() if rp == "pooled" or r["rp"] == rp]
        metrics = {}
        for metric in ("brier","log_loss"):
            def loss(row,p):
                if metric == "brier":
                    return (p-row["y"])**2
                p = min(1-1e-9,max(1e-9,p))
                return -(row["y"]*math.log(p)+(1-row["y"])*math.log(1-p))
            a = {k:(actual[k]["event"],loss(actual[k],calibrated_rows[k] if calibrated_rows is not None else actual[k]["p"])) for k in keys}
            b = {k:(expected[k]["event"],loss(expected[k],expected[k]["p"])) for k in keys}
            metrics[metric] = paired_cluster_bootstrap(a,b,seed=7)
        out[rp] = metrics
    return out


def validate_on_2024(cache, artifact):
    params,calibration = validate_artifact(artifact)
    data = load_published(cache,2024)
    old,raw = forecasts(data,FlagRpParams()),forecasts(data,params)
    probabilities = {r["key"]:calibrated(r["p"],r["base"],calibration) for r in raw}
    comparison = compare(old,raw,calibrated_rows=probabilities)
    guards = {"pooled_log_loss":comparison["pooled"]["log_loss"]["hi90"] <= 1e-12,
        "brier":all(v["brier"]["mean_diff"] <= POLICY["validation_brier_tolerance"] for v in comparison.values()),
        "log_loss":all(v["log_loss"]["mean_diff"] <= POLICY["validation_log_loss_tolerance"] for v in comparison.values())}
    return {"season":2024,"artifact_sha256":digest(artifact),"source_sha256":data.source_sha256,
            "comparison":comparison,"raw_fitted_minus_default":compare(old,raw),
            "calibrated_minus_raw":compare(raw,raw,calibrated_rows=probabilities),
            "guards":guards,"passed":all(guards.values()),"serving_eligible":False}
