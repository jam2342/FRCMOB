# Offline score-only challenger conditional on an immutable state/win rehearsal.
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from time import perf_counter

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.ratings import calibrations as layers, gradual_rehearsal as gradual, state_fitting as state, weekly_rehearsal as weekly
from app.services.ratings.research_rehearsal import write_settings
from app.services.ratings.research_win import all_sides, context, fit_score_calibration, score_design, side_arrays, SCORE_SD
from app.services.ratings.state_inputs import digest

RECIPE = "first-score-season-age-v1"
POLICY = {"transition_days":14.,"intercept_limit":1.2,"intercept_step":layers.SCORE_STEP,
          "prior_sd":SCORE_SD[0],"bias_limit_pct":gradual.POLICY["first_score_bias_limit_pct"],"source_calibration_prior":"bootstrap",
          "minimum_season":2023,"maximum_season":2026,"serving_eligible":False,
          "scope":"conditional score-only study; source state/win decisions are fixed"}


def hashes(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("source root cannot be a symlink")
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("source files cannot be symlinks")
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def validate(value, previous=None):
    value = np.asarray(value,dtype=float)
    if value.shape != (2,) or not np.isfinite(value).all() or np.any(np.abs(value) > POLICY["intercept_limit"]+1e-12):
        raise ValueError("age-score intercepts outside hard bounds")
    if previous is not None and np.any(np.abs(value-validate(previous)) > POLICY["intercept_step"]+1e-12):
        raise ValueError("age-score intercept exceeds weekly step")
    return value


def design(table, sides):
    age = np.asarray(table["season_frac"],dtype=float)[sides["row"]]*120/POLICY["transition_days"]
    if not np.isfinite(age).all() or np.any(age < 0):
        raise ValueError("season age must be known, finite and nonnegative")
    late = np.clip(age,0,1)
    first = sides["ctx"] == 0
    return np.column_stack([first*(1-late),first*late])


def fit(table, nuisance, prior, previous=None):
    # Earlier research nuisance fits are unconstrained; only endpoint outputs serve this study.
    coef = np.asarray(nuisance["score_coef"],dtype=float).copy()
    if coef.shape != (11,) or not np.isfinite(coef).all():
        raise ValueError("invalid nuisance score coefficients")
    prior = validate(prior)
    initial = prior if previous is None else validate(previous)
    sides = side_arrays(table)
    x = design(table,sides)
    coef[0] = 0.
    residual = sides["y"]-sides["x"]-score_design(sides,True)@coef
    noise = max(float(np.var(sides["y"]-sides["x"])),1e-9) if len(residual) else 1.
    precision = np.full(2,1/POLICY["prior_sd"]**2)
    lo,hi = np.full(2,-POLICY["intercept_limit"]),np.full(2,POLICY["intercept_limit"])
    if previous is not None:
        lo,hi = np.maximum(lo,initial-POLICY["intercept_step"]),np.minimum(hi,initial+POLICY["intercept_step"])
    fitted = state.box_quadratic(x.T@x/noise+np.diag(precision),x.T@residual/noise+precision*prior,lo,hi,initial)
    return validate(fitted,previous).tolist()


def bootstrap(year, tables):
    if year not in range(POLICY["minimum_season"],POLICY["maximum_season"]+1):
        raise ValueError("age-score study accepts examined 2023–2026 only")
    fits,provenance = [],{}
    for earlier in range(2023,year):
        path = Path(tables)/f"{earlier}.npz"
        provenance[str(earlier)] = hashlib.sha256(path.read_bytes()).hexdigest()
        with np.load(path,allow_pickle=False) as raw:
            table = {k:raw[k] for k in raw.files}
        lower = datetime(earlier,1,1,tzinfo=timezone.utc).timestamp()
        upper = datetime(earlier+1,1,1,tzinfo=timezone.utc).timestamp()
        if not len(table["time"]) or not np.all((table["time"] >= lower) & (table["time"] < upper)) or any(
                not str(key).startswith(str(earlier)) for key in table["key"]):
            raise ValueError("earlier-season age-score table contains another season")
        nuisance = layers.identity()
        nuisance["score_coef"] = fit_score_calibration(side_arrays(table),terms=True).coef.tolist()
        fits.append(fit(table,nuisance,[0.,0.]))
    return {"endpoints":np.mean(fits,axis=0).tolist() if fits else [0.,0.],"source_tables":provenance,
            "provisional":not bool(fits)}


def source_run(root):
    root = Path(root)
    checkpoint_sha = json.loads((root/"head.json").read_text())["sha256"]
    checkpoint = weekly.read_object(root/"checkpoints",checkpoint_sha)
    if checkpoint["identity"] != json.loads((root/"manifest.json").read_text()):
        raise ValueError("source checkpoint identity differs")
    reports = [(p.stem,weekly.read_object(root/"reports",p.stem)) for p in (root/"reports").glob("*.json")]
    reports = [(sha,r) for sha,r in reports if r["checkpoint"] == checkpoint_sha and r["status"] == "complete"]
    if len(reports) != 1:
        raise ValueError("need one complete source report at the checkpoint")
    report_sha,report = reports[0]
    if report.get("calibration_prior") != POLICY["source_calibration_prior"] or report.get("application") != "gradual":
        raise ValueError("age-score study requires a fixed-prior gradual source")
    if not all(report["checks"][k] for k in ("full_season","active_parent","history_preserved","rating_steps","rollback_restored")):
        raise ValueError("source mechanical checks failed")
    weekly.journal_hashes(root,checkpoint["journals"])
    frames = [f for sha in checkpoint["journals"] for f in weekly.read_object(root/"journals",sha)["forecasts"]]
    with sqlite3.connect(f"file:{(root/'scratch.sqlite').resolve()}?mode=ro",uri=True) as db:
        versions = {i:json.loads(p) for i,p in db.execute("SELECT id,params FROM rating_calibrations")}
        if list(db.execute("SELECT calibration_id FROM rating_active_bundle")) != [(None,)]:
            raise ValueError("source actual active pointer must be null")
    if {str(i):digest(v) for i,v in versions.items()} != checkpoint["versions"]:
        raise ValueError("source calibration hashes differ")
    transitions,active = [],report["bootstrap_id"]
    artifact = versions[active]["artifact"]
    layers.validate_params(artifact["layers"])
    for job in report["jobs"]:
        if job["active_before"] != active:
            raise ValueError("source active chain differs")
        cutoff = datetime.fromisoformat(job["cutoff"])
        if job["safety"]["rollback"]:
            active = report["bootstrap_id"]
            transitions.append(((cutoff+timedelta(hours=weekly.POLICY["job_hour_utc"])).timestamp(),active))
        if job["applied"]:
            p = versions[job["fit"]["id"]]
            if p["schema"] != gradual.RECIPES["bootstrap"] or p["calibration_prior_sha256"] != digest(artifact["layers"]):
                raise ValueError("source fit has another calibration prior")
            if p["parent_active"] != active or p["parent_sha256"] != digest(versions[active]):
                raise ValueError("source fit parent differs")
            parent = versions[active]
            layers.validate_params(p["layers"],parent["artifact"]["layers"] if "artifact" in parent else parent["layers"])
            active = job["fit"]["id"]
            transitions.append(((cutoff+timedelta(hours=weekly.POLICY["publication_hour_utc"])).timestamp(),active))
        if job["active_after"] != active:
            raise ValueError("source publication chain differs")
    active,at = report["bootstrap_id"],0
    for frame in sorted(frames,key=lambda f:(f["time"],f["key"])):
        while at < len(transitions) and transitions[at][0] < frame["time"]:
            active = transitions[at][1]
            at += 1
        p = frame["forecasts"]["active"]
        if p["version"] != active or p["params_sha256"] != checkpoint["versions"][str(active)]:
            raise ValueError("source forecast uses an unavailable version")
    return frames,report,versions,report_sha


def predict(frames, versions, source_versions):
    out = copy.deepcopy(frames)
    for frame in out:
        source = frame["forecasts"]["active"]
        payload = versions[source["version"]]
        raw = {k:np.array([v]) for k,v in source["raw"].items()}
        raw["y"] = np.array([.5])
        sides = all_sides(raw,np.array([0]))
        sides["row"] = np.array([0,0])
        params = source_versions[source["version"]]
        nuisance = params["artifact"]["layers"] if "artifact" in params else params["layers"]
        delta = raw["u"][0]*(design(raw,sides)@validate(payload["endpoints"])-(sides["ctx"] == 0)*nuisance["score_coef"][0])
        sha = digest(payload)
        frame["forecasts"]["age_score"] = {**copy.deepcopy(source),"rm":source["rm"]+float(delta[0]),
            "bm":source["bm"]+float(delta[1]),"source_version":source["version"],"version":sha,"params_sha256":sha}
    return out


def rehearse(root, source_root, data, prior, protocol_sha, code_sha, *, progress=None):
    root,source_root = Path(root),Path(source_root)
    if root.is_symlink() or root.resolve().is_relative_to(source_root.resolve()) or source_root.resolve().is_relative_to(root.resolve()):
        raise ValueError("age-score output must not overlap its source")
    before = hashes(source_root)
    frames,report,source_versions,report_sha = source_run(source_root)
    if report["identity"]["season"] != data.year or report["identity"]["data_sha256"] != data.source_sha256:
        raise ValueError("source season or cache changed")
    artifact = source_versions[report["bootstrap_id"]]["artifact"]
    expected = {str(y):artifact["source_sha256"][f"{y}.npz"] for y in artifact["layer_source_seasons"]}
    if prior["source_tables"] != expected:
        raise ValueError("age-score prior tables differ from source earlier seasons")
    if len(frames) != len(data.records) or {f["key"] for f in frames} != {r["result"].card.match_key for r in data.records}:
        raise ValueError("source forecasts are incomplete or duplicated")
    cards = {r["result"].card.match_key:r["result"].card for r in data.records}
    if any((f["time"],f["event"]) != (cards[f["key"]].time,cards[f["key"]].event_key) for f in frames):
        raise ValueError("source forecast playing times differ from cache")
    identity = {"recipe":RECIPE,"protocol_sha256":protocol_sha,"source_report":report_sha,
                "source_hashes":before,"prior":prior,"data_sha256":data.source_sha256,"season":data.year}
    root.mkdir(parents=True,exist_ok=True)
    for folder in ("versions","checkpoints","journals","reports"):
        if (root/folder).is_symlink():
            raise ValueError("age-score output folder cannot be a symlink")
    head = root/"head.json"
    if head.is_symlink():
        raise ValueError("age-score checkpoint pointer cannot be a symlink")
    checkpoint = weekly.read_object(root/"checkpoints",json.loads(head.read_text())["sha256"]) if head.exists() else None
    if checkpoint and checkpoint["identity"] != identity:
        raise ValueError("age-score source, prior or protocol changed")
    run_code_sha = checkpoint["code_sha"] if checkpoint else code_sha
    saved = dict(checkpoint["versions"]) if checkpoint else {}
    fitted,metrics = {},[]
    bootstrap_id = report["bootstrap_id"]
    def store(identifier, metadata, calculate, previous=None):
        if str(identifier) in saved:
            payload = weekly.read_object(root/"versions",saved[str(identifier)])
            if any(payload[k] != v for k,v in metadata.items()):
                raise ValueError("age-score saved fit provenance differs")
            validate(payload["endpoints"],previous)
        else:
            endpoints,detail = calculate()
            payload = {**metadata,"endpoints":validate(endpoints,previous).tolist(),"fit":detail}
            saved[str(identifier)] = write_settings(root/"versions",payload).stem
            cp = write_settings(root/"checkpoints",{"identity":identity,"versions":saved,"code_sha":run_code_sha})
            weekly.atomic_json(head,{"sha256":cp.stem})
            if progress:
                progress({"season":data.year,"source_version":identifier,"version":saved[str(identifier)]})
        fitted[identifier] = payload
        return payload
    store(bootstrap_id,{"recipe":RECIPE,"source_version":bootstrap_id,"source_sha256":digest(source_versions[bootstrap_id]),
        "prior_sha256":digest(prior),"parent":None},lambda:(prior["endpoints"],{"kind":"earlier-season bootstrap"}))
    engine = create_engine(f"sqlite:///file:{(source_root/'scratch.sqlite').resolve()}?mode=ro&uri=true")
    try:
        for job in report["jobs"]:
            if not job["applied"]:
                continue
            identifier = job["fit"]["id"]
            p = source_versions[identifier]
            parent = fitted[p["parent_active"]]
            cutoff = datetime.fromisoformat(job["cutoff"]).timestamp()
            metadata = {"recipe":RECIPE,"source_version":identifier,"source_sha256":digest(p),"prior_sha256":digest(prior),
                "parent":digest(parent),"data_sha256":p["data_sha256"],"cutoff":job["cutoff"]}
            def calculate():
                started = perf_counter()
                with Session(engine) as db:
                    inputs,fingerprint,_ = state.inputs_for(db,data.year,cutoff,source_versions[bootstrap_id]["context"],updated_before=cutoff)
                if fingerprint != p["data_sha256"]:
                    raise ValueError("source prefix inputs differ")
                _,table,_,_ = state.replay(inputs,state.validate_params(p["state"]))
                endpoints = fit(table,p["layers"],prior["endpoints"],parent["endpoints"])
                elapsed = perf_counter()-started
                budget = (weekly.POLICY["publication_hour_utc"]-weekly.POLICY["job_hour_utc"])*3600
                if elapsed+job["fit_seconds"] > budget:
                    raise ValueError("age-score fit exceeds remaining publication budget")
                return endpoints,{"seconds":elapsed,"first_sides":int(np.sum(side_arrays(table)["ctx"] == 0)),"data_sha256":fingerprint}
            payload = store(identifier,metadata,calculate,parent["endpoints"])
            metrics.append({"source_version":identifier,"cutoff":job["cutoff"],"endpoints":payload["endpoints"],"fit":payload["fit"]})
    finally:
        engine.dispose()
    if len(saved) != len(fitted):
        raise ValueError("checkpoint contains unexpected age-score versions")
    projected = predict(frames,fitted,source_versions)
    if before != hashes(source_root):
        raise ValueError("age-score study changed source files")
    journal = write_settings(root/"journals",{"identity":identity,"forecasts":projected})
    return {"identity":identity,"code_sha":run_code_sha,"frames":projected,"source_report":report,"fits":metrics,"journal":journal.stem,
            "versions":saved,"history_preserved":True}


def bias(frames, source, variant, selected=None):
    actual,predicted,error,sides = 0.,0.,0.,0
    for frame in frames:
        result,p = source[frame["key"]]["result"],frame["forecasts"][variant]
        for color,value,mean,variance in (("red",result.red_points,p["rm"],p["rv"]),("blue",result.blue_points,p["bm"],p["bv"])):
            if value is not None and variance > 0 and (selected is None or (frame["key"],color) in selected):
                actual += value
                predicted += mean
                error += abs(value-mean)
                sides += 1
    return {"sides":sides,"mean_bias_pct":100*(actual-predicted)/predicted if predicted > 0 else None,
            "mae":error/sides if sides else None}


def summarize(run, data):
    frames,source = run["frames"],{r["result"].card.match_key:r for r in data.records}
    first = min(f["time"] for f in frames)
    starts = {}
    for f in frames:
        starts[f["event"]] = min(starts.get(f["event"],f["time"]),f["time"])
    newest = set(sorted(starts,key=lambda k:(starts[k],k))[-math.ceil(len(starts)*.2):])
    first_qual = [f for f in frames if f["first_here"] and not f["playoff"]]
    slices = {"all":frames,"qualifications":[f for f in frames if not f["playoff"]],"first_qualifications":first_qual,
        "first_early14":[f for f in first_qual if f["time"] < first+14*86400],
        "first_after14":[f for f in first_qual if f["time"] >= first+14*86400],
        "newest_events":[f for f in frames if f["event"] in newest],
        "championships":[f for f in frames if f["event_type"] in (2,3,4,5)]}
    metrics = {k:{"age_minus_reference":weekly.compare(v,source,a="age_score",b="active"),
        "bias":{variant:bias(v,source,variant) for variant in ("age_score","active","bootstrap")}} for k,v in slices.items()}
    checks = {"wins_unchanged":all(f["forecasts"]["age_score"]["p"] == f["forecasts"]["active"]["p"] for f in frames),
        "nonfirst_scores_unchanged":all(all(f["forecasts"]["age_score"][k] == f["forecasts"]["active"][k] for k in ("rm","bm"))
            for f in frames if context({k:np.array([v]) for k,v in f["forecasts"]["active"]["raw"].items()})[0] != 0),
        "history_preserved":run["history_preserved"]}
    for name in ("first_qualifications","first_early14","first_after14"):
        value = metrics[name]["bias"]["age_score"]["mean_bias_pct"]
        checks[name+"_bias"] = value is not None and abs(value) <= POLICY["bias_limit_pct"]
    for name in ("qualifications","first_qualifications"):
        checks[name+"_mae_not_worse"] = metrics[name]["age_minus_reference"]["mae"].get("hi90",math.inf) <= 0
    known = []
    for frame in slices["qualifications"]:
        result,p = source[frame["key"]]["result"],frame["forecasts"]["active"]
        for color,actual,key in (("red",result.red_points,"rm"),("blue",result.blue_points,"bm")):
            if actual is not None and p["rv" if color == "red" else "bv"] > 0:
                known.append((frame["key"],color,p[key]))
    threshold = float(np.quantile([v for _,_,v in known],.9)) if known else None
    top = {(key,color) for key,color,value in known if value >= threshold} if known else set()
    metrics["fixed_reference_top_decile"] = {"threshold":threshold,
        "bias":{v:bias(slices["qualifications"],source,v,top) for v in ("age_score","active","bootstrap")}}
    return {k:v for k,v in {**run,"metrics":metrics,"checks":checks,"study_passed":all(checks.values()),
        "status":"complete","release_passed":False,"serving_eligible":False,"policy":POLICY,
        "limitations":["Examined historical development seasons; 2027 is the clean test",
            "Conditional on source state/win decisions and original nuisance score-coefficient trajectory",
            "Endpoint fits use replayed prefix features, not prospective captured features",
            "Bootstrap endpoint fits condition on earlier-season nuisance fits; no current/future season prior",
            "2023 has provisional zero endpoints; cached correction timing limitations remain",
            "Passing this study does not clear full recipe, RP, held-out network or phase 7 checks"]}.items() if k not in ("frames","source_report")}
