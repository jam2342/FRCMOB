# Offline score-only challenger conditional on an immutable state/win rehearsal: the
# first-match intercept depends on how many of the alliance's teams have not played
# yet this season, not on season age. Same bounds, step, prior and checks as the
# age-score study, whose source checks this reuses.
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.ratings import age_score_rehearsal as age, calibrations as layers, state_fitting as state, weekly_rehearsal as weekly
from app.services.ratings.research_rehearsal import write_settings
from app.services.ratings.research_win import all_sides, context, fit_score_calibration, score_design, side_arrays
from app.services.ratings.state_inputs import digest

RECIPE = "first-score-season-debut-v1"
VARIANT = "debut_score"
POLICY = {**{k:v for k,v in age.POLICY.items() if k != "transition_days"},
          "debut_share":"season-new teams on the alliance / 3; season-new = no season result known before the forecast",
          "endpoints":["season-experienced alliance","all-season-new alliance"],
          "no_new_negative_scores":True}


def validate(value, previous=None):
    value = np.asarray(value,dtype=float)
    if value.shape != (2,) or not np.isfinite(value).all() or np.any(np.abs(value) > POLICY["intercept_limit"]+1e-12):
        raise ValueError("debut-score intercepts outside hard bounds")
    if previous is not None and np.any(np.abs(value-validate(previous)) > POLICY["intercept_step"]+1e-12):
        raise ValueError("debut-score intercept exceeds weekly step")
    return value


def season_new(cards):
    # Replay order: a team is new until a card it played appears earlier in the order.
    seen,red,blue = set(),[],[]
    for card in cards:
        red.append(sum(t not in seen for t in card.red))
        blue.append(sum(t not in seen for t in card.blue))
        seen.update(card.red+card.blue)
    return np.array(red,dtype=float),np.array(blue,dtype=float)


def with_season_new(table, cards):
    if [str(k) for k in table["key"]] != [c.match_key for c in cards]:
        raise ValueError("table rows and lineups differ")
    out = dict(table)
    out["new_r"],out["new_b"] = season_new(cards)
    return out


def design(table, sides):
    new = np.where(sides["red"],np.asarray(table["new_r"],dtype=float)[sides["row"]],np.asarray(table["new_b"],dtype=float)[sides["row"]])
    if not np.isfinite(new).all() or np.any((new < 0) | (new > 3)):
        raise ValueError("season-new counts must be known and between 0 and 3")
    first = sides["ctx"] == 0
    # A playoff backup can debut there; in qualifications a new team means a first match here.
    if np.any((new > 0) & np.isin(sides["ctx"],(1,2))):
        raise ValueError("a season-new team outside someone's first match here")
    share = new/3
    return np.column_stack([first*(1-share),first*share])


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


def bootstrap(year, tables, cards_for):
    # cards_for(season) -> {match key: card} from that season's cache, for lineups.
    if year not in range(POLICY["minimum_season"],POLICY["maximum_season"]+1):
        raise ValueError("debut-score study accepts examined 2023–2026 only")
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
            raise ValueError("earlier-season debut-score table contains another season")
        cards = cards_for(earlier)
        if any(str(k) not in cards for k in table["key"]):
            raise ValueError("earlier-season lineups are missing")
        table = with_season_new(table,[cards[str(k)] for k in table["key"]])
        nuisance = layers.identity()
        nuisance["score_coef"] = fit_score_calibration(side_arrays(table),terms=True).coef.tolist()
        fits.append(fit(table,nuisance,[0.,0.]))
    return {"endpoints":np.mean(fits,axis=0).tolist() if fits else [0.,0.],"source_tables":provenance,
            "provisional":not bool(fits)}


def prospective_new(frames, data):
    # Forecast time: a team is new until one of its results was posted before the start.
    pending = sorted((r["posted"],r["result"].card.match_key,r["result"].card) for r in data.records if r["posted"] is not None)
    cards = {r["result"].card.match_key:r["result"].card for r in data.records}
    seen,at,out = set(),0,{}
    for frame in sorted(frames,key=lambda f:(f["time"],f["key"])):
        while at < len(pending) and pending[at][0] < frame["time"]:
            seen.update(pending[at][2].red+pending[at][2].blue)
            at += 1
        card = cards[frame["key"]]
        out[frame["key"]] = (sum(t not in seen for t in card.red),sum(t not in seen for t in card.blue))
    return out


def predict(frames, versions, source_versions, new):
    out = copy.deepcopy(frames)
    for frame in out:
        source = frame["forecasts"]["active"]
        payload = versions[source["version"]]
        raw = {k:np.array([v]) for k,v in source["raw"].items()}
        raw["y"] = np.array([.5])
        raw["new_r"],raw["new_b"] = np.array([new[frame["key"]][0]]),np.array([new[frame["key"]][1]])
        sides = all_sides(raw,np.array([0]))
        sides["row"],sides["red"] = np.array([0,0]),np.array([True,False])
        params = source_versions[source["version"]]
        nuisance = params["artifact"]["layers"] if "artifact" in params else params["layers"]
        delta = raw["u"][0]*(design(raw,sides)@validate(payload["endpoints"])-(sides["ctx"] == 0)*nuisance["score_coef"][0])
        sha = digest(payload)
        frame["forecasts"][VARIANT] = {**copy.deepcopy(source),"rm":source["rm"]+float(delta[0]),
            "bm":source["bm"]+float(delta[1]),"season_new":list(new[frame["key"]]),
            "source_version":source["version"],"version":sha,"params_sha256":sha}
    return out


def rehearse(root, source_root, data, prior, protocol_sha, code_sha, *, progress=None):
    root,source_root = Path(root),Path(source_root)
    if root.is_symlink() or root.resolve().is_relative_to(source_root.resolve()) or source_root.resolve().is_relative_to(root.resolve()):
        raise ValueError("debut-score output must not overlap its source")
    before = age.hashes(source_root)
    frames,report,source_versions,report_sha = age.source_run(source_root)
    if report["identity"]["season"] != data.year or report["identity"]["data_sha256"] != data.source_sha256:
        raise ValueError("source season or cache changed")
    artifact = source_versions[report["bootstrap_id"]]["artifact"]
    expected = {str(y):artifact["source_sha256"][f"{y}.npz"] for y in artifact["layer_source_seasons"]}
    if prior["source_tables"] != expected:
        raise ValueError("debut-score prior tables differ from source earlier seasons")
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
            raise ValueError("debut-score output folder cannot be a symlink")
    head = root/"head.json"
    if head.is_symlink():
        raise ValueError("debut-score checkpoint pointer cannot be a symlink")
    checkpoint = weekly.read_object(root/"checkpoints",json.loads(head.read_text())["sha256"]) if head.exists() else None
    if checkpoint and checkpoint["identity"] != identity:
        raise ValueError("debut-score source, prior or protocol changed")
    run_code_sha = checkpoint["code_sha"] if checkpoint else code_sha
    saved = dict(checkpoint["versions"]) if checkpoint else {}
    fitted,metrics = {},[]
    bootstrap_id = report["bootstrap_id"]
    def store(identifier, metadata, calculate, previous=None):
        if str(identifier) in saved:
            payload = weekly.read_object(root/"versions",saved[str(identifier)])
            if any(payload[k] != v for k,v in metadata.items()):
                raise ValueError("debut-score saved fit provenance differs")
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
                table = with_season_new(table,[r.card for r in inputs.research.season.results])
                endpoints = fit(table,p["layers"],prior["endpoints"],parent["endpoints"])
                elapsed = perf_counter()-started
                budget = (weekly.POLICY["publication_hour_utc"]-weekly.POLICY["job_hour_utc"])*3600
                if elapsed+job["fit_seconds"] > budget:
                    raise ValueError("debut-score fit exceeds remaining publication budget")
                sides = side_arrays(table)
                return endpoints,{"seconds":elapsed,"first_sides":int(np.sum(sides["ctx"] == 0)),
                    "debut_share_sum":float(design(table,sides)[:,1].sum()),"data_sha256":fingerprint}
            payload = store(identifier,metadata,calculate,parent["endpoints"])
            metrics.append({"source_version":identifier,"cutoff":job["cutoff"],"endpoints":payload["endpoints"],"fit":payload["fit"]})
    finally:
        engine.dispose()
    if len(saved) != len(fitted):
        raise ValueError("checkpoint contains unexpected debut-score versions")
    projected = predict(frames,fitted,source_versions,prospective_new(frames,data))
    if before != age.hashes(source_root):
        raise ValueError("debut-score study changed source files")
    journal = write_settings(root/"journals",{"identity":identity,"forecasts":projected})
    return {"identity":identity,"code_sha":run_code_sha,"frames":projected,"source_report":report,"fits":metrics,"journal":journal.stem,
            "versions":saved,"history_preserved":True}


def negatives(frames, variant):
    return {(f["key"],color) for f in frames for color,key in (("red","rm"),("blue","bm")) if f["forecasts"][variant][key] < 0}


def summarize(run, data):
    frames,source = run["frames"],{r["result"].card.match_key:r for r in data.records}
    variants = (VARIANT,"active","bootstrap")
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
    metrics = {k:{"debut_minus_reference":weekly.compare(v,source,a=VARIANT,b="active"),
        "bias":{variant:age.bias(v,source,variant) for variant in variants}} for k,v in slices.items()}
    # Side-level debut slices (reported, not gated): how many of the side's own teams are season-new.
    groups = {"first_sides_all_new":lambda n:n == 3,"first_sides_partly_new":lambda n:0 < n < 3,"first_sides_experienced":lambda n:n == 0}
    for name,keep in groups.items():
        for period,chosen in (("",first_qual),("_early14",slices["first_early14"]),("_after14",slices["first_after14"])):
            selected = {(f["key"],color) for f in chosen for i,color in enumerate(("red","blue")) if keep(f["forecasts"][VARIANT]["season_new"][i])}
            metrics[name+period] = {"bias":{variant:age.bias(chosen,source,variant,selected) for variant in variants}}
    introduced = negatives(frames,VARIANT)-negatives(frames,"active")
    checks = {"wins_unchanged":all(f["forecasts"][VARIANT]["p"] == f["forecasts"]["active"]["p"] for f in frames),
        "nonfirst_scores_unchanged":all(all(f["forecasts"][VARIANT][k] == f["forecasts"]["active"][k] for k in ("rm","bm"))
            for f in frames if context({k:np.array([v]) for k,v in f["forecasts"]["active"]["raw"].items()})[0] != 0),
        "history_preserved":run["history_preserved"],"no_new_negative_scores":not introduced}
    for name in ("first_qualifications","first_early14","first_after14"):
        value = metrics[name]["bias"][VARIANT]["mean_bias_pct"]
        checks[name+"_bias"] = value is not None and abs(value) <= POLICY["bias_limit_pct"]
    for name in ("qualifications","first_qualifications"):
        checks[name+"_mae_not_worse"] = metrics[name]["debut_minus_reference"]["mae"].get("hi90",math.inf) <= 0
    known = []
    for frame in slices["qualifications"]:
        result,p = source[frame["key"]]["result"],frame["forecasts"]["active"]
        for color,actual,key in (("red",result.red_points,"rm"),("blue",result.blue_points,"bm")):
            if actual is not None and p["rv" if color == "red" else "bv"] > 0:
                known.append((frame["key"],color,p[key]))
    threshold = float(np.quantile([v for _,_,v in known],.9)) if known else None
    top = {(key,color) for key,color,value in known if value >= threshold} if known else set()
    metrics["fixed_reference_top_decile"] = {"threshold":threshold,
        "bias":{v:age.bias(slices["qualifications"],source,v,top) for v in variants}}
    return {k:v for k,v in {**run,"metrics":metrics,"checks":checks,"study_passed":all(checks.values()),
        "negative_scores":{"debut_score":len(negatives(frames,VARIANT)),"active":len(negatives(frames,"active")),
            "introduced":sorted(map(list,introduced))},
        "status":"complete","release_passed":False,"serving_eligible":False,"policy":POLICY,
        "limitations":["Examined historical development seasons; 2027 is the clean test",
            "The debut hypothesis came from a diagnosis of these same seasons; passing is not independent confirmation",
            "Conditional on source state/win decisions and original nuisance score-coefficient trajectory",
            "Endpoint fits use replayed prefix features and playing-order season-new counts, not prospective captured features",
            "Bootstrap endpoint fits condition on earlier-season nuisance fits; no current/future season prior",
            "2023 has provisional zero endpoints; cached correction timing limitations remain",
            "Nonfirst-context negative scores are inherited unchanged; positivity needs its own score-layer study",
            "Passing this study does not clear full recipe, RP, held-out network or phase 7 checks"]}.items() if k not in ("frames","source_report")}
