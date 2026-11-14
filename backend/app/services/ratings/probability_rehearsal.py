# Historical probability-only updates on immutable total-state forecasts.
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import time

import numpy as np

from app.services.ratings import calibrations as layers, state_fitting as state, weekly_rehearsal as weekly
from app.services.ratings.research_rehearsal import write_settings
from app.services.ratings.state_inputs import digest

RECIPE = "fixed-state-weekly-probability-v1"
POLICY = {"minimum_fit_matches":150,"job_hour_utc":9,"publication_hour_utc":10,
          "fit_budget_seconds":3600,"recent_sanity_tolerance":.001,"early_tolerance":.001,
          "first_match_tolerance":.001,"safety_days":14,"minimum_safety_matches":150,
          "score_updates":False,"state_updates":False,"recipe_selection":False}


def source_run(root, data, artifact):
    root = Path(root)
    identity = json.loads((root/"manifest.json").read_text())
    if identity["schema"] != weekly.RECIPE or identity["season"] != data.year or identity["data_sha256"] != data.source_sha256 or identity["artifact_sha256"] != digest(artifact):
        raise ValueError("source rehearsal inputs do not match")
    sha = json.loads((root/"head.json").read_text())["sha256"]
    checkpoint = weekly.read_object(root/"checkpoints",sha)
    if checkpoint["identity"] != identity or not checkpoint["history_preserved"]:
        raise ValueError("source checkpoint identity/history mismatch")
    hashes = weekly.journal_hashes(root,checkpoint["journals"])
    frames = [f for name in checkpoint["journals"] for f in weekly.read_object(root/"journals",name)["forecasts"]]
    expected = {r["result"].card.match_key:r for r in data.records}
    if len(frames) != len(expected) or {f["key"] for f in frames} != expected.keys():
        raise ValueError("source needs a complete season of unique first forecasts")
    for f in frames:
        card = expected[f["key"]]["result"].card
        if (f["time"],f["event"]) != (card.time,card.event_key):
            raise ValueError("source forecast does not match actual match")
    return frames,{"source_identity":identity,"source_checkpoint":sha,"source_journals":hashes}


def training_table(frames, source, cutoff):
    rows = []
    for f in frames:
        record = source[f["key"]]
        r = record["result"]
        if f["time"] >= cutoff or record["posted"] is None or record["posted"] >= cutoff:
            continue
        if r.red_points is None or r.blue_points is None or r.red_score == r.blue_score:
            continue
        rows.append({**f["forecasts"]["bootstrap"]["raw"],"key":f["key"],"event":f["event"],"time":f["time"],
                     "posted":record["posted"],"ra":r.red_points,"ba":r.blue_points,"y":float(r.red_score > r.blue_score)})
    rows.sort(key=lambda r:(r["time"],r["key"]))
    return {k:np.array([r[k] for r in rows]) for k in rows[0]} if rows else {}


def predict(frames, params, version, *, use_bootstrap=False):
    if not frames:
        return []
    raw = [f["forecasts"]["bootstrap"]["raw"] for f in frames]
    table = {k:np.array([r[k] for r in raw]) for k in raw[0]}
    table["y"] = np.full(len(frames),.5)
    probabilities = [f["forecasts"]["bootstrap"]["p"] for f in frames] if use_bootstrap else layers.apply(table,params)[2]
    out = copy.deepcopy(frames)
    for f,p in zip(out,probabilities):
        baseline = f["forecasts"]["bootstrap"]
        f["forecasts"] = {"bootstrap":baseline,"epa":f["forecasts"]["epa"],
                          "active":{**copy.deepcopy(baseline),"p":float(p),"version":version}}
    return out


def fit(table, centre, cutoff):
    if not table or len(table["y"]) < POLICY["minimum_fit_matches"]:
        return None,{"status":"insufficient_matches","matches":len(table.get("y",[]))}
    started = time.monotonic()
    errors = []
    fitted = None
    recent_change = None
    try:
        fitted = state.fit_win_layers(table,centre)
        layers.validate_params(fitted,centre)
        if fitted["score_coef"] != centre["score_coef"] or fitted["blend"] != centre["blend"]:
            raise ValueError("probability-only fit changed frozen score/blend settings")
        recent = layers.subset(table,table["time"] >= cutoff-POLICY["safety_days"]*86400)
        if len(recent["y"]):
            a,b = layers.apply(recent,fitted)[2],layers.apply(recent,centre)[2]
            recent_change = float(np.mean((a-recent["y"])**2-(b-recent["y"])**2))
            if recent_change > POLICY["recent_sanity_tolerance"]:
                errors.append("recent in-sample Brier sanity check failed")
    except (ValueError,np.linalg.LinAlgError) as exc:
        errors.append(str(exc))
    elapsed = time.monotonic()-started
    if elapsed > POLICY["fit_budget_seconds"]:
        errors.append("publication budget exceeded")
    evidence = {"status":"rejected" if errors else "candidate","matches":len(table["y"]),"errors":errors,
                "training_sha256":digest({k:v.tolist() for k,v in table.items()}),"recent_brier_change":recent_change}
    return (None if errors else fitted),evidence


def rehearse(root, frames, source, initial, identity, *, max_weeks=None, progress=None):
    root = Path(root)
    root.mkdir(parents=True,exist_ok=True)
    for name in ("journals","checkpoints","versions","reports"):
        if (root/name).is_symlink():
            raise ValueError("probability rehearsal object folder cannot be a symlink")
    manifest = root/"manifest.json"
    if manifest.exists():
        if json.loads(manifest.read_text()) != identity:
            raise ValueError("probability rehearsal inputs/protocol changed")
    else:
        weekly.atomic_json(manifest,identity)
    layers.validate_params(initial)
    if initial["blend"] != 1:
        raise ValueError("probability rehearsal requires the full candidate link")
    bootstrap = write_settings(root/"versions",{"kind":"bootstrap","params":initial}).stem
    head = root/"head.json"
    cp = weekly.read_object(root/"checkpoints",json.loads(head.read_text())["sha256"]) if head.exists() else None
    if cp and cp["identity"] != identity:
        raise ValueError("probability checkpoint identity mismatch")
    journals = cp["journals"] if cp else []
    weekly.journal_hashes(root,journals)
    saved = [f for sha in journals for f in weekly.read_object(root/"journals",sha)["forecasts"]]
    jobs = cp["jobs"] if cp else []
    versions = cp["versions"] if cp else [bootstrap]
    fitted = {sha:weekly.read_object(root/"versions",sha)["params"] for sha in versions}
    active = cp["active"] if cp else bootstrap
    if active not in fitted or bootstrap not in fitted:
        raise ValueError("probability checkpoint has unknown active version")
    first = layers.monday(datetime.fromtimestamp(min(f["time"] for f in frames),timezone.utc))
    final = layers.monday(datetime.fromtimestamp(max(f["time"] for f in frames),timezone.utc))+timedelta(days=7)
    week = datetime.fromisoformat(cp["next_week"]) if cp else first
    completed = 0
    while week <= final and (max_weeks is None or completed < max_weeks):
        before = weekly.journal_hashes(root,journals)
        end = week+timedelta(days=7)
        job_time = (week+timedelta(hours=POLICY["job_hour_utc"])).timestamp()
        publication = (week+timedelta(hours=POLICY["publication_hour_utc"])).timestamp()
        selected = sorted((f for f in frames if week.timestamp() <= f["time"] < end.timestamp()),key=lambda f:(f["time"],f["key"]))
        early = predict([f for f in selected if f["time"] <= job_time],fitted[active],active,use_bootstrap=active==bootstrap)
        decision = weekly.safety(saved+early,source,active,job_time)
        job = {"cutoff":week.isoformat(),"active_before":active,"safety":decision}
        candidate = None
        if decision["rollback"]:
            active = bootstrap
            job["fit"] = {"status":"skipped_after_rollback"}
        else:
            table = training_table(frames,source,week.timestamp())
            candidate,job["fit"] = fit(table,fitted[active],week.timestamp())
        middle = predict([f for f in selected if job_time < f["time"] <= publication],fitted[active],active,use_bootstrap=active==bootstrap)
        if candidate is not None:
            active = write_settings(root/"versions",{"kind":"weekly_fit","cutoff":week.isoformat(),"parent":active,
                "params":candidate,"evidence":job["fit"]}).stem
            fitted[active] = candidate
            versions.append(active)
        job["applied"] = candidate is not None
        job["active_after"] = active
        late = predict([f for f in selected if f["time"] > publication],fitted[active],active,use_bootstrap=active==bootstrap)
        week_frames = early+middle+late
        path = write_settings(root/"journals",{"schema":RECIPE,"week":week.isoformat(),"forecasts":week_frames})
        if before != weekly.journal_hashes(root,journals):
            raise ValueError("probability fitting rewrote saved forecasts")
        journals.append(path.stem)
        saved.extend(week_frames)
        jobs.append(job)
        cp = {"identity":identity,"next_week":end.isoformat(),"active":active,"bootstrap":bootstrap,
              "versions":versions,"journals":journals,"jobs":jobs}
        checkpoint = write_settings(root/"checkpoints",cp)
        weekly.atomic_json(head,{"sha256":checkpoint.stem})
        if progress:
            progress({"season":identity["season"],"week":week.isoformat(),"forecasts":len(week_frames),
                      "fit":job["fit"]["status"],"applied":job["applied"],"rollback":decision["rollback"]})
        week,completed = end,completed+1
    return {"status":"complete" if week > final else "incomplete","identity":identity,"frames":saved,"jobs":jobs,
            "checkpoint":json.loads(head.read_text())["sha256"],"bootstrap":bootstrap}


def summarize(run, source):
    frames = run["frames"]
    starts = {}
    for f in frames:
        starts[f["event"]] = min(starts.get(f["event"],f["time"]),f["time"])
    first = min(starts.values()) if starts else 0
    newest = set(sorted(starts,key=lambda k:(starts[k],k))[-max(1,math.ceil(len(starts)*.2)):])
    slices = {"all":frames,"early_14_days":[f for f in frames if f["time"] < first+14*86400],
              "week4_on":[f for f in frames if f["time"] >= first+21*86400],
              "newest_events":[f for f in frames if f["event"] in newest],
              "championships":[f for f in frames if f["event_type"] in (2,3,4,5)],
              "first_here":[f for f in frames if f["first_here"]]}
    metrics = {name:{"active_minus_bootstrap":weekly.compare(rows,source),"active_minus_epa":weekly.compare(rows,source,b="epa")}
               for name,rows in slices.items()}
    unchanged = all({k:v for k,v in f["forecasts"]["active"].items() if k not in ("p","version")} ==
                    {k:v for k,v in f["forecasts"]["bootstrap"].items() if k not in ("p","version")} for f in frames)
    def upper(name):
        return metrics[name]["active_minus_bootstrap"]["brier"].get("hi90",math.inf)
    checks = {"full_season":run["status"] == "complete" and len(frames) == len(source),
              "beats_bootstrap":upper("all") < 0,"early_not_worse":upper("early_14_days") <= POLICY["early_tolerance"],
              "newest_not_worse":upper("newest_events") <= 0,"first_matches_not_worse":upper("first_here") <= POLICY["first_match_tolerance"],
              "not_worse_than_epa":metrics["all"]["active_minus_epa"]["brier"].get("hi90",math.inf) <= 0,
              "state_and_scores_unchanged":unchanged,"rollback_restored":all(not j["safety"]["rollback"] or
                  j["active_after"] == run["bootstrap"] for j in run["jobs"])}
    return {k:v for k,v in {**run,"schema":RECIPE,"kind":"historical_probability_only_rehearsal","metrics":metrics,
            "checks":checks,"development_checks_passed":all(checks.values()),"weekly_updates":sum(j["applied"] for j in run["jobs"]),
            "rollbacks":sum(j["safety"]["rollback"] for j in run["jobs"]),"maximum_rating_movement":0.,
            "release_passed":False,"serving_eligible":False,"release_blockers":["clean 2027","held-out production network / late-event gap",
            "first-match score calibration","full recipe selection, RP and serving integration","missing 2022 earlier-season bootstrap"],
            "limitations":["Examined historical development data, not prospective production evidence",
                "Team state still learns from results under fixed bootstrap parameters; state hyperparameters are not refitted",
                "Training uses original saved bootstrap features with results published strictly before Monday cutoff",
                "Publication is Monday 10:00 UTC; simultaneous forecasts use the preceding version",
                "Final cached label corrections lack their original correction-publication times",
                "One predeclared weekly probability recipe; no recipe-selection claim"]}.items() if k != "frames"}
