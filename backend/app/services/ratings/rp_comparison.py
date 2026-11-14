# Offline 2026 development comparison. No serving or activation code imports this.
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import asdict, replace
from itertools import groupby
import hashlib
import json
import math
from pathlib import Path

from app.services.ratings.component_spec import bonus_flags, get_spec, number
from app.services.ratings.constants import season_baseline
from app.services.ratings.flag_rp import FlagRpModel
from app.services.ratings.ranking_points import BONUS_RP_CALIBRATION, BONUS_RPS, bonus_chances, event_thresholds
from app.services.ratings.research_data import load_cached_season
from app.services.ratings.research_eval import paired_cluster_bootstrap
from app.services.ratings.state_inputs import digest
from app.services.ratings.team_model import SeasonTeamModel


def load_inputs(cache):
    spec = get_spec(2026)
    metadata, hashes = {}, {}
    for path in sorted((Path(cache)/"2026").glob("*.json")):
        data = path.read_bytes()
        payload = json.loads(data)
        if not payload["event"]["key"].lower().startswith("2026"):
            raise ValueError("comparison cache contains another season")
        hashes[path.name] = hashlib.sha256(data).hexdigest()
        for match in payload["matches"]:
            key = match["key"].lower()
            if key in metadata:
                raise ValueError("duplicate cached match")
            sides = match.get("alliances") or {}
            breakdown = match.get("score_breakdown") or {}
            teams = [str(t).lower() for c in ("red","blue") for t in sides.get(c,{}).get("team_keys",[])]
            valid = len(teams) == len(set(teams)) == 6 and all(len(sides.get(c,{}).get("team_keys",[])) == 3 for c in ("red","blue"))
            flags = {c:bonus_flags(spec,{} if sides.get(c,{}).get("dq_team_keys") else breakdown.get(c)) for c in ("red","blue")}
            when = number(match.get("actual_time"))
            posted = number(match.get("post_result_time"))
            metadata[key] = {"flags":flags,"actual_time":int(when) if when is not None and when > 0 else None,
                             "post_result_time":int(posted) if posted is not None and when is not None and posted > when > 0 else None,
                             "valid_lineup":valid}
    if not hashes:
        raise ValueError("missing 2026 comparison cache")
    research = load_cached_season(cache,2026,exclude_dq=False)
    return research,metadata,digest(hashes)


def read_priors(path):
    priors = {}
    for row in csv.reader(Path(path).read_text().splitlines()):
        if not row or not row[0].startswith("frc"):
            continue
        if len(row) != 3 or row[0].lower() in priors or number(float(row[1])) is None:
            raise ValueError("invalid or duplicate previous-season strength")
        priors[row[0].lower()] = float(row[1])
    if not priors:
        raise ValueError("missing previous-season strengths")
    return priors


def live_model(research, priors):
    baseline = season_baseline(2026)
    multipliers = {t:math.exp(baseline["prior_beta"]*z) for t,z in priors.items()}
    for team, year in research.rookie_year.items():
        if year == 2026:
            multipliers.setdefault(team,baseline["rookie_multiplier"])
    return SeasonTeamModel(dict(baseline["team_model_priors"]),baseline["team_model_params"],
        team_multipliers=multipliers,
        team_var_multipliers={t:baseline["prior_known_var_multiplier"] for t in priors},
        week_multipliers=list(baseline.get("week_multipliers") or []))


def parts(prediction, thresholds, *, calibrated=True):
    calibration = BONUS_RP_CALIBRATION[2026] if calibrated else {}
    return {color:bonus_chances(2026,thresholds,getattr(prediction,f"{color}_hub"),getattr(prediction,f"{color}_tower"),calibration)
            for color in ("red","blue")}


def reproduce(research, model):
    by_event = defaultdict(list)
    for result in research.season.results:
        by_event[result.card.event_key].extend((result.red_components,result.blue_components))
    thresholds = {e:event_thresholds(2026,c) for e,c in by_event.items()}
    out = {}
    for result in research.season.results:
        prediction = model.predict(result.card)
        if result.card.comp_level == "qm":
            out[result.card.match_key] = parts(prediction,thresholds[result.card.event_key])
        model.observe(result)
    return out


def served_parity(predictions, snapshots):
    gaps, missing, matches = [], 0, 0
    for path in sorted(Path(snapshots).glob("served_2026*.json")):
        saved = json.loads(path.read_text())
        for match in saved["matches"]:
            served = (match.get("prediction") or {}).get("bonus_rp_chances")
            if not served:
                continue
            rebuilt = predictions.get(match["match_key"].lower())
            matches += 1
            for color in ("red","blue"):
                for name,value in served.get(color,{}).items():
                    if rebuilt is None or name not in rebuilt[color]:
                        missing += 1
                    else:
                        gaps.append(abs(rebuilt[color][name]-value))
    # The API rounds each RP probability to three decimals.
    return {"matches":matches,"values":len(gaps),"missing_values":missing,
            "mean_abs_gap":sum(gaps)/len(gaps) if gaps else None,"max_abs_gap":max(gaps) if gaps else None,
            "tolerance":.00055,"passed":bool(gaps) and missing == 0 and max(gaps) <= .00055,
            "kind":"retrospective served-value reproduction; not pre-match evidence"}


def actual_results(research, metadata):
    skipped, selected = Counter(), []
    for result in research.season.results:
        meta = metadata[result.card.match_key]
        if meta["actual_time"] is None:
            skipped["missing_actual_time"] += 1
        elif not meta["valid_lineup"]:
            skipped["invalid_lineup"] += 1
        else:
            selected.append(replace(result,card=replace(result.card,time=meta["actual_time"])))
    selected.sort(key=lambda r:(r.card.time,r.card.match_key))
    starts = {}
    for result in selected:
        starts.setdefault(result.card.event_key,result.card.time)
    return [replace(r,card=replace(r.card,event_start=starts[r.card.event_key])) for r in selected],dict(skipped)


def replay_comparison(research, metadata, model, *, challenger=None):
    results, skipped = actual_results(research,metadata)
    candidate = FlagRpModel(get_spec(2026))
    if challenger is not None and challenger.spec != candidate.spec:
        raise ValueError("RP challenger must use the same component spec")
    lowest, forecasts, here = {}, {}, Counter()
    pending = sorted((metadata[r.card.match_key]["post_result_time"],r.card.match_key,r) for r in results
                     if metadata[r.card.match_key]["post_result_time"] is not None)
    applied = 0
    for _, group in groupby(results,key=lambda r:r.card.time):
        batch = list(group)
        while applied < len(pending) and pending[applied][0] < batch[0].card.time:
            result = pending[applied][2]
            card = result.card
            model.observe(result)
            candidate.observe(card,metadata[card.match_key]["flags"])
            if challenger is not None:
                challenger.observe(card,metadata[card.match_key]["flags"])
            for team in card.red+card.blue:
                here[(team,card.event_key)] += 1
            for comp in (result.red_components,result.blue_components):
                for rp,(_,_,source) in BONUS_RPS[2026].items():
                    value = number(comp.get(source))
                    if comp.get(f"{rp}_rp") and value is not None:
                        key = (card.event_key,rp)
                        lowest[key] = min(lowest.get(key,value),value)
            applied += 1
        for result in batch:
            card = result.card
            # Both models see only results posted strictly before this start.
            prediction = model.forecast(card.event_key,card.red,card.blue,card.event_start)
            if card.comp_level != "qm":
                continue
            thresholds = {rp:lowest.get((card.event_key,rp),rule[1]) for rp,rule in BONUS_RPS[2026].items()}
            flag = candidate.forecast(card)
            fitted = challenger.forecast(card) if challenger is not None else None
            live, raw = parts(prediction,thresholds),parts(prediction,thresholds,calibrated=False)
            official = research.events[card.event_key].get("event_type") in (0,1,2,3,4,5)
            for color in ("red","blue"):
                for name, estimate in flag[color].items():
                    y = metadata[card.match_key]["flags"][color][name]
                    if not official or type(y) is not bool or name not in live[color]:
                        continue
                    key = f"{card.match_key}/{color}/{name}"
                    forecasts[key] = {"event":card.event_key,"time":card.time,"rp":name,"y":y,
                        "flag":estimate["probability"],"live":live[color][name],"raw_threshold":raw[color][name],
                        "base":estimate["base_rate"],"threshold":thresholds[name],
                        "first_match":min(here[(t,card.event_key)] for t in card.red+card.blue) == 0,
                        "championship":research.events[card.event_key].get("event_type") in (2,3,4,5)}
                    if fitted is not None:
                        forecasts[key]["fitted_flag"] = fitted[color][name]["probability"]
    return forecasts,{"history_matches":len(results),"excluded_history":skipped,
        "missing_or_invalid_publication_time":len(results)-len(pending),"updates_available":len(pending),
        "updates_applied_before_last_start":applied,"candidate_settings_sha256":candidate.settings_sha256}


def summarize(forecasts):
    starts = {}
    for row in forecasts.values():
        starts[row["event"]] = min(starts.get(row["event"],row["time"]),row["time"])
    ordered = sorted(starts,key=lambda e:(starts[e],e))
    newest = set(ordered[-max(1,math.ceil(len(ordered)*.2)):])
    first = min(starts.values(),default=0)
    def loss(row,variant,metric):
        p,y = row[variant],row["y"]
        if metric == "brier":
            return (p-y)**2
        p = min(1-1e-9,max(1e-9,p))
        return -(y*math.log(p)+(1-y)*math.log(1-p))
    def summary(rows):
        if not rows:
            return {"n":0}
        values = {v:{metric:sum(loss(r,v,metric) for r in rows.values())/len(rows) for metric in ("brier","log_loss")}
                  for v in ("flag","live","raw_threshold","base")}
        compared = {}
        for variant in ("live","raw_threshold","base"):
            compared[variant] = {m:paired_cluster_bootstrap({k:(r["event"],loss(r,"flag",m)) for k,r in rows.items()},
                {k:(r["event"],loss(r,variant,m)) for k,r in rows.items()},seed=7) for m in ("brier","log_loss")}
        return {"n":len(rows),"earned_rate":sum(r["y"] for r in rows.values())/len(rows),"metrics":values,"flag_minus":compared}
    slices = {"all":lambda r:True,"first_7_days":lambda r:r["time"] < first+604800,
              "first_21_days":lambda r:r["time"] < first+3*604800,"newest_events":lambda r:r["event"] in newest,
              "first_match_here":lambda r:r["first_match"],"championships":lambda r:r["championship"]}
    groups = {"pooled":forecasts,**{rp:{k:r for k,r in forecasts.items() if r["rp"] == rp} for rp in BONUS_RPS[2026]}}
    return {name:{sl:summary({k:r for k,r in rows.items() if choose(r)}) for sl,choose in slices.items()} for name,rows in groups.items()}


def provenance(research, priors, source_sha):
    baseline = season_baseline(2026)
    return {"source_sha256":source_sha,"previous_strengths_sha256":digest(priors),"recipe_sha256":digest({
        "priors":baseline["team_model_priors"],"params":asdict(baseline["team_model_params"]),
        "prior_beta":baseline["prior_beta"],"rookie_multiplier":baseline["rookie_multiplier"],
        "prior_known_var_multiplier":baseline["prior_known_var_multiplier"],
        "thresholds":BONUS_RPS[2026],"calibration":BONUS_RP_CALIBRATION[2026]}),"cached_events":len(research.events)}
