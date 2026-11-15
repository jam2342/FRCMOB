# Release ceiling for the week-4 check: the whole-season (hindsight) state settings, with
# win/score layers refitted every Monday exactly as the live system fits them. The old
# ceiling also refitted the layers on the whole season and scored them in-sample, an
# advantage no live system can earn. Offline only; never serves anything.
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.ratings import calibrations as layers, state_fitting as state, weekly_rehearsal as weekly
from app.services.ratings.state_inputs import digest

RECIPE = "hindsight-state-prospective-layers-v1"
POLICY = {"minimum_fit_matches":150,"sanity_brier":.001,"sanity_days":14,"centre":"previous ceiling layers",
          "prior":"fixed earlier-season bootstrap layers","state":"whole-season hindsight fit, fixed",
          "clock":"Monday 00:00 data cutoff, 09:00 job, 10:00 publication, strictly later forecasts",
          "serving_eligible":False}


def fit_week(table, centre, prior, cutoff):
    # The live rule: bounded fit around the active layers, pulled to the fixed prior, then
    # the last-two-week in-sample sanity check. Any failure keeps the active layers.
    try:
        fitted = state.fit_layers(table,centre,prior=prior)
        layers.validate_params(fitted,centre)
        recent = layers.subset(table,table["time"] >= cutoff-POLICY["sanity_days"]*86400)
        decided = (recent["y"] != .5) & np.isfinite(recent["ra"]) & np.isfinite(recent["ba"])
        if decided.any():
            a,b = layers.apply(recent,fitted)[2],layers.apply(recent,centre)[2]
            change = float(np.mean((a[decided]-recent["y"][decided])**2-(b[decided]-recent["y"][decided])**2))
            if change > POLICY["sanity_brier"]:
                return centre,["last-two-week Brier sanity check failed"]
        return fitted,[]
    except (ValueError,np.linalg.LinAlgError) as exc:
        return centre,[str(exc)]


def layer_path(source_root, year, report, versions, params):
    bootstrap = versions[report["bootstrap_id"]]
    artifact,context = bootstrap["artifact"],bootstrap["context"]
    params = state.validate_params(params if isinstance(params,dict) else asdict(params))
    centre = copy.deepcopy(artifact["layers"])
    path = [{"publication":None,"cutoff":None,"layers":centre,"errors":[],"matches":0}]
    engine = create_engine(f"sqlite:///file:{(Path(source_root)/'scratch.sqlite').resolve()}?mode=ro&uri=true")
    try:
        for job in report["jobs"]:
            week = datetime.fromisoformat(job["cutoff"])
            cutoff = week.timestamp()
            with Session(engine) as db:
                inputs,fingerprint,_ = state.inputs_for(db,year,cutoff,context,updated_before=cutoff)
            results = inputs.research.season.results
            if job.get("applied") and versions[job["fit"]["id"]]["data_sha256"] != fingerprint:
                raise ValueError("ceiling prefix inputs differ from the source fit")
            eligible = sum(r.red_points is not None and r.blue_points is not None for r in results)
            if eligible < POLICY["minimum_fit_matches"]:
                continue
            if any(r.card.time >= cutoff for r in results):
                raise ValueError("ceiling training data reaches past its cutoff")
            _,table,_,_ = state.replay(inputs,params)
            centre,errors = fit_week(table,centre,artifact["layers"],cutoff)
            publication = (week+timedelta(hours=weekly.POLICY["publication_hour_utc"])).timestamp()
            path.append({"publication":publication,"cutoff":cutoff,"layers":centre,"errors":errors,"matches":eligible,
                         "data_sha256":fingerprint})
    finally:
        engine.dispose()
    return path


def in_effect(path, time):
    chosen = path[0]
    for entry in path[1:]:
        if entry["publication"] < time:
            chosen = entry
    return chosen


def forecasts(oracle, frames, path):
    # oracle: the saved whole-season supplement; its raw features are as issued under the
    # hindsight state (results posted before each start), so only the layers change here.
    raw = {f["key"]:f["prediction"]["raw"] for f in oracle["forecasts"]}
    out = {}
    for frame in frames:
        entry = in_effect(path,frame["time"])
        out[frame["key"]] = weekly.predicted(raw[frame["key"]],entry["layers"],entry["publication"] or 0,digest(entry["layers"]))
    return out


def summarize(frames, source, oracle, path):
    frames = copy.deepcopy(frames)
    ceiling = forecasts(oracle,frames,path)
    old = {f["key"]:f["prediction"] for f in oracle["forecasts"]}
    for f in frames:
        f["forecasts"]["ceiling"],f["forecasts"]["old_ceiling"] = ceiling[f["key"]],old[f["key"]]
    first = min(f["time"] for f in frames)
    starts = {}
    for f in frames:
        starts[f["event"]] = min(starts.get(f["event"],f["time"]),f["time"])
    newest = set(sorted(starts,key=lambda k:(starts[k],k))[-max(1,-(-len(starts)//5)):])
    slices = {"week4_on":lambda f:f["time"] >= first+21*86400,"all":lambda f:True,
              "newest_events":lambda f:f["event"] in newest,"early_14_days":lambda f:f["time"] < first+14*86400}
    metrics = {name:{"active_minus_ceiling":weekly.compare([f for f in frames if keep(f)],source,b="ceiling"),
                     "active_minus_old_ceiling":weekly.compare([f for f in frames if keep(f)],source,b="old_ceiling"),
                     "ceiling_minus_old_ceiling":weekly.compare([f for f in frames if keep(f)],source,a="ceiling",b="old_ceiling")}
               for name,keep in slices.items()}
    tolerance = weekly.POLICY["hindsight_brier_tolerance"]
    late = metrics["week4_on"]
    return {"metrics":metrics,"checks":{
        "week4_within_ceiling":late["active_minus_ceiling"]["brier"].get("hi90",float("inf")) <= tolerance,
        "week4_within_old_ceiling":late["active_minus_old_ceiling"]["brier"].get("hi90",float("inf")) <= tolerance},
        "path":[{k:v for k,v in e.items() if k != "layers"}|{"layers_sha256":digest(e["layers"])} for e in path],
        "policy":POLICY}
