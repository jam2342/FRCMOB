# Offline check of a positive score forecast: the mean of the model's own Gaussian
# predictive distribution with impossible negative outcomes counted as zero,
# E[max(0, Y)] for Y ~ N(mean, var). No fitted parameter; win chances are untouched.
from __future__ import annotations

import copy
import math

import numpy as np

from app.services.ratings.research_eval import paired_cluster_bootstrap

RECIPE = "score-censored-mean-v1"
POLICY = {"transform":"mean*Phi(mean/sd)+sd*phi(mean/sd), sd = sqrt(side predictive variance)",
          "bias_limit_pct":3.0,"top_decile_bias_change_pp":.1,"decile":.1,"seed":7,"draws":2000,
          "gated_base":"active","reported_bases":["active","debut_score"],"serving_eligible":False}


def censored_mean(mean, var):
    if not (math.isfinite(mean) and math.isfinite(var)) or var <= 0:
        raise ValueError("a positive score needs a finite mean and a positive variance")
    sd = math.sqrt(var)
    z = mean/sd
    if z < -30:
        # The true value underflows long before here; a forecast this far below zero means broken state.
        raise ValueError("predictive mean is more than 30 sd below zero")
    if z < -8:
        # z*Phi(z)+phi(z) cancels badly far in the tail; its asymptotic series doesn't.
        return sd*math.exp(-z*z/2)/math.sqrt(2*math.pi)/(z*z)*(1-3/(z*z)+15/z**4)
    phi = math.exp(-z*z/2)/math.sqrt(2*math.pi)
    return mean*.5*math.erfc(-z/math.sqrt(2))+sd*phi


def apply(frames, base):
    out = copy.deepcopy(frames)
    for frame in out:
        p = frame["forecasts"][base]
        frame["forecasts"][base+"_positive"] = {**copy.deepcopy(p),"rm":censored_mean(p["rm"],p["rv"]),
            "bm":censored_mean(p["bm"],p["bv"]),"base":base}
    return out


def sides(frames, source, variant):
    out = []
    for f in frames:
        r,p = source[f["key"]]["result"],f["forecasts"][variant]
        for color,actual,mean in (("red",r.red_points,p["rm"]),("blue",r.blue_points,p["bm"])):
            if actual is not None:
                out.append({"key":f"{f['key']}|{color}","event":f["event"],"playoff":bool(f["playoff"]),"time":f["time"],
                            "first":bool(f["first_here"]),"actual":float(actual),"pred":float(mean)})
    return out


def bias(rows):
    predicted = sum(r["pred"] for r in rows)
    return {"sides":len(rows),"bias_pct":100*(sum(r["actual"] for r in rows)-predicted)/predicted if predicted > 0 else None,
            "mae":sum(abs(r["actual"]-r["pred"]) for r in rows)/len(rows) if rows else None}


def paired_mae(new, old):
    a = {r["key"]:(r["event"],abs(r["actual"]-r["pred"])) for r in new}
    b = {r["key"]:(r["event"],abs(r["actual"]-r["pred"])) for r in old}
    return paired_cluster_bootstrap(a,b,n=POLICY["draws"],seed=POLICY["seed"])


def compare(frames, source, base):
    old,new = sides(frames,source,base),sides(frames,source,base+"_positive")
    if [r["key"] for r in old] != [r["key"] for r in new]:
        raise ValueError("positive and base forecasts cover different sides")
    quals = [i for i,r in enumerate(old) if not r["playoff"]]
    ordered = sorted(old[i]["pred"] for i in quals)
    low = ordered[max(0,math.ceil(len(ordered)*POLICY["decile"])-1)] if ordered else None
    high = float(np.quantile(ordered,1-POLICY["decile"])) if ordered else None
    first = min((f["time"] for f in frames),default=0)
    groups = {"qualifications":quals,"bottom_decile":[i for i in quals if old[i]["pred"] <= low],
              "top_decile":[i for i in quals if old[i]["pred"] >= high],"playoffs":[i for i,r in enumerate(old) if r["playoff"]],
              "first_early14":[i for i in quals if old[i]["first"] and old[i]["time"] < first+14*86400],
              "first_after14":[i for i in quals if old[i]["first"] and old[i]["time"] >= first+14*86400],
              "base_negative":[i for i,r in enumerate(old) if r["pred"] < 0]}
    metrics = {name:{"base":bias([old[i] for i in idx]),"positive":bias([new[i] for i in idx]),
                     "positive_minus_base_mae":paired_mae([new[i] for i in idx],[old[i] for i in idx])}
               for name,idx in groups.items()}
    shifts = [n["pred"]-o["pred"] for n,o in zip(new,old)]
    every = [(f["forecasts"][base+"_positive"],f["forecasts"][base]) for f in frames]
    return {"thresholds":{"bottom_decile":low,"top_decile":high},"metrics":metrics,
            "negative":{"base":sum(p[k] < 0 for _,p in every for k in ("rm","bm")),
                        "positive":sum(q[k] <= 0 for q,_ in every for k in ("rm","bm"))},
            "shift":{"max":max(shifts,default=0.),"min":min(shifts,default=0.),"over_one_point":sum(s > 1 for s in shifts),
                     "mean":float(np.mean(shifts)) if shifts else 0.},
            "wins_unchanged":all(q["p"] == p["p"] for q,p in every)}


def checks(result):
    m = result["metrics"]
    top = m["top_decile"]
    return {"all_positive":result["negative"]["positive"] == 0 and result["shift"]["min"] >= -1e-9,
            "wins_unchanged":result["wins_unchanged"],
            "bottom_decile_mae_not_worse":m["bottom_decile"]["positive_minus_base_mae"].get("hi90",math.inf) <= 0,
            "qualification_mae_not_worse":m["qualifications"]["positive_minus_base_mae"].get("hi90",math.inf) <= 0,
            "qualification_bias":abs(m["qualifications"]["positive"]["bias_pct"]) <= POLICY["bias_limit_pct"],
            "top_decile_unmoved":abs(top["positive"]["bias_pct"]-top["base"]["bias_pct"]) <= POLICY["top_decile_bias_change_pp"]}
