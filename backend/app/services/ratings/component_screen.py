# A fixed-recipe representation screen, not the per-component MAP or release rehearsal.
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from app.services.ratings.calibrations import monday
from app.services.ratings.component_filter import ComponentFilter
from app.services.ratings.component_spec import Component, ComponentSpec, field, number, project
from app.services.ratings.replay import MatchCard, MatchResult
from app.services.ratings.research_eval import paired_cluster_bootstrap
from app.services.ratings.research_filter import ScaleFreeParams
from app.services.ratings.research_fit import scale_free_z
from app.services.ratings.research_models import mean_share
from app.services.ratings.state_bootstrap import build_bootstrap, validate_bootstrap
from app.services.ratings.state_inputs import digest
from app.services.ratings.strength import curve_for_anchors, points_to_rating

RECIPE = "component-representation-screen-v1"
POLICY = {"minimum_closure":.99,"minimum_matches":1500,"minimum_weeks":2,
          "log_loss_tolerance":.002,"mae_relative_tolerance":.01,"context_brier_tolerance":.003,
          "coverage_bounds":[.76,.84],"median_rating_step":1.,"maximum_rating_step":5.,
          "minimum_rating_matches":8,"minimum_rating_teams":20,"bootstrap_draws":2000,
          "state_refits":False,"win_layer":"fixed logistic 1.702 * margin / total spread"}

# Research declarations do not replace the serving registry. C is alliance-points only.
SOURCES = {
    2022:{"B":(("autoPoints",),("teleopPoints",)),"C":(("autoPoints",),("teleopCargoPoints",),("endgamePoints",))},
    2023:{"B":(("autoPoints",),("teleopPoints",)),"C":(("autoPoints",),("teleopGamePiecePoints",),("endGameChargeStationPoints",),("endGameParkPoints",),("linkPoints",))},
    2024:{"B":(("autoPoints",),("teleopPoints",)),"C":(("autoPoints",),("teleopTotalNotePoints",),("endGameTotalStagePoints",))},
    2025:{"B":(("autoPoints",),("teleopPoints",)),"C":(("autoPoints",),("teleopCoralPoints",),("algaePoints",),("endGameBargePoints",))},
    2026:{"B":(("totalAutoPoints",),("totalTeleopPoints",)),"C":(("hubScore.autoPoints",),("hubScore.teleopPoints",),("totalTowerPoints",))},
}


def specs(year):
    if year not in SOURCES:
        raise ValueError("component screen accepts examined 2022–2026 seasons only")
    return {"A":ComponentSpec(year,"A",1,(Component("total",("score_without_fouls",)),)),
            **{tier:ComponentSpec(year,tier,1,tuple(Component(f"part{i+1}",paths) for i,paths in enumerate(sources)))
               for tier,sources in SOURCES[year].items()}}


@dataclass
class Inputs:
    year: int
    records: list
    rookies: set
    source_sha256: str
    skipped: dict


def load(cache, year):
    declared = specs(year)
    records,hashes,skipped,rookies,keys = [],{},Counter(),set(),set()
    for path in sorted((Path(cache)/str(year)).glob("*.json")):
        raw = path.read_bytes()
        payload = json.loads(raw)
        event = payload["event"]
        key = str(event["key"]).lower()
        if not key.startswith(str(year)):
            raise ValueError("component cache contains another season")
        hashes[path.name] = hashlib.sha256(raw).hexdigest()
        if type(event.get("event_type")) is not int or event["event_type"] not in range(6):
            skipped["unofficial_event"] += 1
            continue
        rookies.update(str(t["key"]).lower() for t in payload.get("teams",[]) if t.get("rookie_year") == year)
        for m in payload["matches"]:
            match = str(m["key"]).lower()
            if not match.startswith(key+"_") or str(m.get("event_key",key)).lower() != key:
                raise ValueError("component match belongs to another event")
            if match in keys:
                raise ValueError("duplicate component match")
            keys.add(match)
            actual = number(m.get("actual_time"))
            sides,breakdowns = m.get("alliances") or {},m.get("score_breakdown") or {}
            teams = {c:tuple(str(t).lower() for t in sides.get(c,{}).get("team_keys",[])) for c in ("red","blue")}
            scores = {c:number(sides.get(c,{}).get("score")) for c in teams}
            if actual is None or actual <= 0:
                skipped["missing_actual_time"] += 1
                continue
            if any(len(t) != 3 for t in teams.values()) or len(set(teams["red"]+teams["blue"])) != 6:
                skipped["invalid_lineup"] += 1
                continue
            if any(v is None or v < 0 for v in scores.values()):
                skipped["unplayed"] += 1
                continue
            projections,closure,points = {t:{} for t in declared},{t:{} for t in declared},{}
            for color in teams:
                b = breakdowns.get(color) or {}
                eligible = not sides[color].get("dq_team_keys")
                points[color] = project(declared["A"],scores[color],b)["total"] if eligible else None
                for tier,spec in declared.items():
                    values = project(spec,scores[color],b)
                    complete = eligible and points[color] is not None and all(v is not None for v in values.values())
                    adjust = field(b,"adjustPoints")
                    closure[tier][color] = complete and (tier == "A" or adjust is not None and abs(values["other"]-adjust) <= 1e-6)
                    projections[tier][color] = {name:value if complete else None for name,value in values.items() if name != "total" or tier == "A"}
            card = MatchCard(match,key,int(actual),str(m.get("comp_level") or ""),int(actual),teams["red"],teams["blue"])
            result = MatchResult(card,int(scores["red"]),int(scores["blue"]),points["red"],points["blue"],
                red_components={"total_points":points["red"]} if points["red"] is not None else {},
                blue_components={"total_points":points["blue"]} if points["blue"] is not None else {})
            posted = number(m.get("post_result_time"))
            posted = int(posted) if posted is not None and posted > actual else None
            if posted is None:
                skipped["missing_or_invalid_publication"] += 1
            records.append({"result":result,"posted":posted,"type":event["event_type"],"projections":projections,"closure":closure})
    if not records:
        raise ValueError("no scored actual component matches")
    records.sort(key=lambda r:(r["result"].card.time,r["result"].card.match_key))
    return Inputs(year,records,rookies,digest(hashes),dict(skipped))


def bootstrap(reports, year):
    if year == 2022:
        return ScaleFreeParams(margin_cov=False,noise_count=.2),ScaleFreeParams(),{},["2022 uses the pre-existing stand-in ratios, derived from 2026; reference only"]
    fits,runs,hashes = {},[],{}
    for earlier in range(2022,year):
        for variant in ("hindsight_mle","e3_noise_count_zstatic"):
            path = Path(reports)/"runs"/f"{variant}-{earlier}.json"
            data = json.loads(path.read_text())
            if data["season"] != earlier or data["variant"] != variant:
                raise ValueError("earlier-season component bootstrap provenance mismatch")
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            if variant == "hindsight_mle":
                fits[str(earlier)] = {"mle":data["plan"][-1]["params"]}
            else:
                runs.append(data)
    artifact = build_bootstrap(year,fits,runs,hashes)
    validate_bootstrap(artifact,year)
    return ScaleFreeParams(**artifact["params"]),ScaleFreeParams(**artifact["prior_params"]),hashes,[]


def loss(row, tier, metric):
    p = row[tier]
    y = row["result"]
    if metric in ("brier","log_loss"):
        z = (p.red_mean-p.blue_mean)/math.sqrt(max(p.red_var+p.blue_var,1e-12))
        probability = 1/(1+math.exp(-max(-30.,min(30.,1.702*z))))
        outcome = float(y.red_score > y.blue_score)
        if metric == "brier":
            return (probability-outcome)**2
        return -(outcome*math.log(max(probability,1e-12))+(1-outcome)*math.log(max(1-probability,1e-12)))
    values = []
    for mean,var,actual in ((p.red_mean,p.red_var,y.red_points),(p.blue_mean,p.blue_var,y.blue_points)):
        if actual is None:
            continue
        error = actual-mean
        if metric == "mae":
            values.append(abs(error))
        elif metric == "nll":
            values.append(.5*(math.log(2*math.pi*max(var,1e-12))+error*error/max(var,1e-12)))
        else:
            values.append(float(abs(error) <= 1.2816*math.sqrt(max(var,0.))))
    return sum(values)/len(values) if values else None


def compare(rows, challenger, baseline):
    rows = [r for r in rows if r["result"].red_points is not None and r["result"].blue_points is not None]
    output = {}
    for metric in ("brier","log_loss","mae","nll","coverage"):
        selected = [r for r in rows if metric not in ("brier","log_loss") or r["result"].red_score != r["result"].blue_score]
        values = {name:{r["result"].card.match_key:(r["result"].card.event_key,loss(r,name,metric)) for r in selected} for name in (challenger,baseline)}
        output[metric] = paired_cluster_bootstrap(values[challenger],values[baseline],n=POLICY["bootstrap_draws"],seed=7)
        for name in (challenger,baseline):
            output[metric][name] = sum(v[1] for v in values[name].values())/len(selected) if selected else None
    return output


def impact(models, played, challenger, baseline):
    teams = sorted(t for t,n in played.items() if n >= POLICY["minimum_rating_matches"])
    if len(teams) < POLICY["minimum_rating_teams"]:
        return {"n":len(teams),"passed":False}
    anchor = [models["A"].team_points(t) for t in teams]
    median,p99 = np.quantile(anchor,[.5,.99])
    if median <= 0 or p99 <= median:
        return {"n":len(teams),"passed":False}
    curve = curve_for_anchors(float(median),float(p99))
    delta = [abs(points_to_rating(models[challenger].team_points(t),*curve)-points_to_rating(models[baseline].team_points(t),*curve)) for t in teams]
    typical,maximum = float(np.median(delta)),max(delta)
    return {"n":len(teams),"median":typical,"max":maximum,"anchor_median":float(median),"anchor_p99":float(p99),
            "passed":typical < POLICY["median_rating_step"] and maximum <= POLICY["maximum_rating_step"]}


def gate(rows, challenger, baseline, rating_impact):
    metrics = compare(rows,challenger,baseline)
    common = [r for r in rows if r["result"].red_points is not None and r["result"].blue_points is not None]
    weeks = {monday(datetime.fromtimestamp(r["result"].card.time,timezone.utc)) for r in common}
    closure = [r["closure"][challenger][c] for r in common for c in ("red","blue")]
    def context(select):
        chosen = [r for r in common if select(r)]
        return compare(chosen,challenger,baseline)["brier"] if chosen else {"n":0}
    contexts = {"playoffs":context(lambda r:r["result"].card.comp_level != "qm"),"first_here":context(lambda r:r["first_here"])}
    coverage = metrics["coverage"][challenger]
    guards = {"support":len(common) >= POLICY["minimum_matches"] and len(weeks) >= POLICY["minimum_weeks"],
        "closure":bool(closure) and sum(closure)/len(closure) >= POLICY["minimum_closure"],
        "score_likelihood":metrics["nll"].get("hi90",math.inf) < 0,
        "log_loss":metrics["log_loss"].get("mean_diff",math.inf) <= POLICY["log_loss_tolerance"],
        "mae":bool(common) and metrics["mae"][challenger] <= metrics["mae"][baseline]*(1+POLICY["mae_relative_tolerance"]),
        "coverage":coverage is not None and POLICY["coverage_bounds"][0] <= coverage <= POLICY["coverage_bounds"][1],
        "contexts":all(v.get("n",0)>0 and v["mean_diff"] <= POLICY["context_brier_tolerance"] for v in contexts.values()),
        "rating_impact":rating_impact["passed"]}
    return {"passed":all(guards.values()),"guards":guards,"metrics":metrics,"contexts":contexts,
            "closure_fraction":sum(closure)/len(closure) if closure else None,"rating_impact":rating_impact}


def evaluate(data, params, *, z_previous=None, previous_unit=None):
    declared = specs(data.year)
    models = {tier:ComponentFilter(spec,params,z_previous=z_previous,rookies=data.rookies,previous_unit=previous_unit) for tier,spec in declared.items()}
    pending = sorted((r["posted"],r["result"].card.match_key,r) for r in data.records if r["posted"] is not None)
    first = monday(datetime.fromtimestamp(data.records[0]["result"].card.time,timezone.utc))
    last = monday(datetime.fromtimestamp(data.records[-1]["result"].card.time,timezone.utc))+timedelta(days=7)
    cutoffs = []
    while first <= last:
        cutoffs.append(first)
        first += timedelta(days=7)
    moments = sorted([(int(c.timestamp()),0,c) for c in cutoffs]+[(r["result"].card.time,1,r) for r in data.records],key=lambda x:(x[0],x[1]))
    applied,rows,checks,played,here = 0,[],[],Counter(),Counter()
    for time,kind,item in moments:
        while applied < len(pending) and pending[applied][0] < time:
            past = pending[applied][2]
            for tier,model in models.items():
                model.observe(past["result"],past["projections"][tier])
            for team in past["result"].card.red+past["result"].card.blue:
                played[team] += 1
                here[(team,past["result"].card.event_key)] += 1
            applied += 1
        if kind == 0:
            known = [r for r in rows if r["posted"] is not None and r["posted"] < time]
            checks.append({"cutoff":item.isoformat(),"training_forecasts_sha256":digest([r["result"].card.match_key for r in known]),
                "pairs":{f"{c}_minus_{b}":gate(known,c,b,impact(models,played,c,b)) for c,b in (("B","A"),("C","A"),("C","B"))}})
        else:
            card = item["result"].card
            rows.append({**item,**{tier:model.predict(card) for tier,model in models.items()},
                         "first_here":any(here[(t,card.event_key)] == 0 for t in card.red+card.blue)})
    starts = {}
    for row in rows:
        card = row["result"].card
        starts[card.event_key] = min(starts.get(card.event_key,card.time),card.time)
    newest = set(sorted(starts,key=lambda e:(starts[e],e))[-max(1,math.ceil(len(starts)*.2)):])
    first = min(starts.values())
    slices = {"all":lambda r:True,"newest_events":lambda r:r["result"].card.event_key in newest,
        "first_14_days":lambda r:r["result"].card.time < first+14*86400,
        "championships":lambda r:r["type"] in (2,3,4,5),"playoffs":lambda r:r["result"].card.comp_level != "qm",
        "first_here":lambda r:r["first_here"]}
    comparisons = {f"{c}_minus_{b}":{name:compare([r for r in rows if select(r)],c,b) for name,select in slices.items()}
                   for c,b in (("B","A"),("C","A"),("C","B"))}
    return {"season":data.year,"comparisons":comparisons,"weekly_checks":checks,
        "first_eligible_cutoff":{pair:next((w["cutoff"] for w in checks if w["pairs"][pair]["passed"]),None) for pair in comparisons},
        "forecast_sha256":digest([{**{k:asdict(r[k]) for k in ("A","B","C")},"key":r["result"].card.match_key} for r in rows]),
        "settings":models["A"].settings(),"specs":{t:asdict(s) for t,s in declared.items()},"source_sha256":data.source_sha256,
        "skipped":data.skipped,"history_matches":len(rows),"applied_results":applied,"serving_eligible":False}


def context(cache, reports, year):
    params,prior,hashes,limitations = bootstrap(reports,year)
    if year == 2022:
        return params,{},None,hashes,limitations
    previous = load(cache,year-1)
    boundary = datetime(year,1,1,tzinfo=timezone.utc).timestamp()
    results = [r["result"] for r in previous.records if r["posted"] is not None and r["posted"] < boundary]
    if not results:
        raise ValueError("missing earlier-season published results")
    hashes[f"cache-{year-1}"] = previous.source_sha256
    return params,scale_free_z(results,prior),mean_share(results),hashes,limitations
