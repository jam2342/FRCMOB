# Immutable first forecasts are the only training evidence for prospective RP fits.
from __future__ import annotations

from dataclasses import asdict
import copy
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path

from app.core.config import settings
from app.db import models
from app.services.ratings import calibrations
from app.services.ratings.component_spec import Component, ComponentSpec, BonusRp, bonus_flags, get_spec, number
from app.services.ratings.flag_rp import FlagRpParams
from app.services.ratings.flag_rp_inputs import database_inputs, replay_flags
from app.services.ratings.replay import MatchCard
from app.services.ratings.research_eval import paired_cluster_bootstrap
from app.services.ratings.rp_fitting import calibrated
from app.services.ratings import rp_weekly as weekly
from app.services.ratings.state_inputs import digest, season_boundary


def implementation_sha256():
    folder = Path(__file__).resolve().parent
    return digest({"weekly":weekly.implementation_sha256(),**{name:hashlib.sha256((folder/name).read_bytes()).hexdigest()
                   for name in ("rp_predictions.py","flag_rp_inputs.py","state_inputs.py")}})


def stored_spec(params):
    raw = params["spec"]
    spec = ComponentSpec(**{**raw,"components":tuple(Component(**{**c,"source":tuple(c["source"])}) for c in raw["components"]),
                           "bonus_rps":tuple(BonusRp(**r) for r in raw["bonus_rps"])})
    if spec.fingerprint != params["spec_sha256"]:
        raise ValueError("RP spec provenance mismatch")
    return spec


def validate_version(version, *, current=True):
    p = version.params
    if version.recipe != weekly.RECIPE or p["schema"] != weekly.RECIPE or p["kind"] not in ("bootstrap","fitted"):
        raise ValueError("unsupported RP calibration version")
    spec = stored_spec(p)
    if spec.season != version.season or p["state_params"] != asdict(FlagRpParams()) or p["policy"] != weekly.POLICY:
        raise ValueError("RP version changed state or policy")
    if current and p["implementation_sha256"] != implementation_sha256():
        raise ValueError("RP version belongs to another implementation")
    if set(p["corrections"]) != {r.name for r in spec.bonus_rps}:
        raise ValueError("RP correction names differ from spec")
    for c in p["corrections"].values():
        weekly.validate_correction(c)
    return spec


def apply(p,base,correction):
    return p if correction == weekly.identity() else calibrated(p,base,correction)


def bootstrap(db, spec, now):
    rows = db.query(models.RatingCalibration).filter_by(season=spec.season,recipe=weekly.RECIPE).order_by(models.RatingCalibration.version.desc()).all()
    impl = implementation_sha256()
    found = next((v for v in rows if v.params.get("kind") == "bootstrap" and v.params.get("spec_sha256") == spec.fingerprint
                  and v.params.get("implementation_sha256") == impl and v.status != "rejected"),None)
    if found:
        return found
    params = {"schema":weekly.RECIPE,"kind":"bootstrap","spec":asdict(spec),"spec_sha256":spec.fingerprint,
        "implementation_sha256":impl,"state_params":asdict(FlagRpParams()),"policy":copy.deepcopy(weekly.POLICY),
        "corrections":{r.name:weekly.identity() for r in spec.bonus_rps}}
    row = models.RatingCalibration(season=spec.season,recipe=weekly.RECIPE,version=max((v.version for v in rows),default=0)+1,
        params=params,fitted_through=datetime.fromtimestamp(season_boundary(spec.season),timezone.utc),matches=0,
        status="candidate",code_sha=impl,created_at=now,fit_metrics={"serving_eligible":False,"code_identity":"source_sha256"})
    db.add(row)
    db.flush()
    return row


def current_inputs(db, spec, now):
    rows,skipped = database_inputs(db,spec,now.timestamp())
    stored = {(r.match_key.lower(),r.alliance):r for r in db.query(models.MatchAllianceResult).filter_by(season_year=spec.season)}
    eligible,versions = [], []
    for card,flags in rows:
        sides = [stored[(card.match_key,c)] for c in ("red","blue")]
        if card.time <= 0 or any(r.updated_at is None or calibrations.utc(r.updated_at) > now for r in sides):
            continue
        eligible.append((card,flags))
        versions.append((card.match_key,[calibrations.utc(r.updated_at).isoformat() for r in sides]))
    return eligible,digest({"rows":[(asdict(c),f) for c,f in eligible],"versions":versions}),skipped


def capture(db, season, event_keys, *, event_types, now=None):
    if not settings.rating_rp_enabled or not settings.rating_prediction_log_enabled:
        return {"status":"disabled"}
    fixed_now = now
    now = calibrations.utc(now or datetime.now(timezone.utc))
    try:
        spec = get_spec(season)
    except ValueError as exc:
        return {"status":"undeclared_season","reason":str(exc)}
    if not spec.bonus_rps:
        return {"status":"no_declared_bonus_flags"}
    bundle = calibrations.ensure_bundle(db,season)
    if bundle.frozen or db.get(models.FrozenSeason,season):
        return {"status":"season_frozen"}
    allowed = [e.event_key for e in db.query(models.Event).filter(models.Event.event_key.in_(event_keys),models.Event.year == season)
               if type(event_types.get(e.event_key)) is int and event_types[e.event_key] in range(6)]
    if not allowed:
        return {"status":"no_official_events"}
    base = bootstrap(db,spec,now)
    versions = db.query(models.RatingCalibration).filter_by(season=season,recipe=weekly.RECIPE,status="candidate").order_by(models.RatingCalibration.version.desc()).all()
    versions = [base]+[v for v in versions if v.params.get("bootstrap_id") == base.id and
                       max(calibrations.utc(v.created_at),calibrations.utc(v.fitted_through)) <= now][:2]
    rows,data_sha,skipped = current_inputs(db,spec,now)
    model = replay_flags(spec,rows)
    teams = {}
    for t in db.query(models.MatchTeam).filter(models.MatchTeam.event_key.in_(allowed)):
        teams.setdefault(t.match_key,{}).setdefault(t.alliance,[]).append(t.team_key.lower())
    completed = {r.match_key for r in db.query(models.MatchAllianceResult).filter(models.MatchAllianceResult.event_key.in_(allowed),models.MatchAllianceResult.total_score >= 0)}
    logs = {r.match_key:r for r in db.query(models.MatchPredictionLog).filter(models.MatchPredictionLog.event_key.in_(allowed))}
    added = 0
    for match in db.query(models.Match).filter(models.Match.event_key.in_(allowed),models.Match.comp_level == "qm").order_by(models.Match.match_key).with_for_update().populate_existing():
        start = match.predicted_time or match.time
        sides,source = teams.get(match.match_key,{}),logs.get(match.match_key)
        if match.actual_time is not None or match.match_key in completed or not start or start <= now.timestamp():
            continue
        if set(sides) != {"red","blue"} or any(len(t) != 3 for t in sides.values()) or len(set(sides["red"]+sides["blue"])) != 6:
            continue
        if source is None or source.season != season or source.event_key != match.event_key or source.backfilled or source.locked_at is not None or not 0 <= (now-calibrations.utc(source.final_at)).total_seconds() <= 60:
            continue
        if source.features.get("schema") != calibrations.SCHEMA or source.features.get("scheduled_start") != start or any(sorted(source.features.get(f"{c}_teams",[])) != sorted(sides[c]) for c in ("red","blue")):
            continue
        card = MatchCard(match.match_key,match.event_key,start,"qm",start,tuple(sides["red"]),tuple(sides["blue"]))
        raw = model.forecast(card)
        captured_at = calibrations.utc(fixed_now or datetime.now(timezone.utc))
        if start <= captured_at.timestamp() or (captured_at-calibrations.utc(source.final_at)).total_seconds() > 60:
            continue
        served = source.final.get("bonus_rp_chances") or {}
        for version in versions:
            validate_version(version)
            if max(calibrations.utc(version.created_at),calibrations.utc(version.fitted_through)) > now or db.get(models.RatingRpPrediction,(version.id,match.match_key)):
                continue
            corrected = {c:{rp:apply(r["probability"],r["base_rate"],version.params["corrections"][rp]) for rp,r in raw[c].items()} for c in ("red","blue")}
            comparator = {c:{rp:(p if (p := number(served.get(c,{}).get(rp))) is not None and 0 <= p <= 1 else None) for rp in raw[c]} for c in ("red","blue")}
            payload = {"raw":raw,"corrected":corrected,"served":comparator,"teams":sides,"event_type":event_types[match.event_key],
                "bootstrap_id":base.id,"data_sha256":data_sha,"calibration_sha256":digest(version.params),
                "source_capture_at":calibrations.utc(source.final_at).isoformat(),"source_features_sha256":digest(source.features),
                "source_bundle_version":source.bundle_version}
            digest(payload)
            db.add(models.RatingRpPrediction(calibration_id=version.id,match_key=match.match_key,event_key=match.event_key,
                season=season,captured_at=captured_at,expected_start=start,prediction=payload))
            added += 1
    db.flush()
    return {"status":"captured","forecasts":added,"versions":len(versions),"skipped_inputs":skipped}


def labelled_rows(db, version, through):
    through = calibrations.utc(through)
    spec = validate_version(version,current=False)
    matches = {m.match_key:m for m in db.query(models.Match).join(models.Event).filter(models.Event.year == version.season)}
    sides = {(r.match_key,r.alliance):r for r in db.query(models.MatchAllianceResult).filter_by(season_year=version.season)}
    rows = []
    for saved in db.query(models.RatingRpPrediction).filter_by(calibration_id=version.id):
        match = matches.get(saved.match_key)
        if match is None or saved.season != version.season or saved.event_key != match.event_key or match.comp_level != "qm" or not match.actual_time or not calibrations.utc(saved.captured_at).timestamp() < match.actual_time < through.timestamp():
            continue
        p = saved.prediction
        if p["calibration_sha256"] != digest(version.params):
            raise ValueError("RP forecast calibration provenance mismatch")
        if p["event_type"] not in range(6) or max(calibrations.utc(version.created_at),calibrations.utc(version.fitted_through)) > calibrations.utc(saved.captured_at):
            continue
        actual_sides = [sides.get((saved.match_key,c)) for c in ("red","blue")]
        if any(r is None or r.total_score is None or r.total_score < 0 or sorted(str(t).lower() for t in r.team_keys) != sorted(p["teams"][c])
               for c,r in zip(("red","blue"),actual_sides)):
            continue
        for color in ("red","blue"):
            result = sides.get((saved.match_key,color))
            if result is None or result.total_score is None or result.total_score < 0 or result.dq_team_keys or result.updated_at is None or calibrations.utc(result.updated_at) >= through:
                continue
            if sorted(str(t).lower() for t in result.team_keys) != sorted(p["teams"][color]):
                continue
            flags = bonus_flags(spec,result.raw_breakdown)
            for name,y in flags.items():
                if y is None:
                    continue
                raw = p["raw"][color][name]
                rows.append({"key":f"{saved.match_key}/{color}/{name}","event":saved.event_key,"rp":name,"y":y,
                    "p":raw["probability"],"base":raw["base_rate"],"corrected":p["corrected"][color][name],
                    "served":p["served"][color][name],"time":match.actual_time,"label_updated_at":calibrations.utc(result.updated_at).isoformat()})
    return sorted(rows,key=lambda r:r["key"])


def score(db, version, *, through):
    rows = labelled_rows(db,version,through)
    def loss(r,name,metric):
        p = r[name]
        if metric == "brier":
            return (p-r["y"])**2
        p = min(1-1e-9,max(1e-9,p))
        return -(r["y"]*math.log(p)+(1-r["y"])*math.log(1-p))
    output = {}
    for rp in ["pooled",*[r.name for r in stored_spec(version.params).bonus_rps]]:
        chosen = [r for r in rows if rp == "pooled" or r["rp"] == rp]
        output[rp] = {}
        for baseline in ("p","served"):
            selected = [r for r in chosen if r[baseline] is not None]
            output[rp]["raw" if baseline == "p" else baseline] = {m:paired_cluster_bootstrap(
                {r["key"]:(r["event"],loss(r,"corrected",m)) for r in selected},
                {r["key"]:(r["event"],loss(r,baseline,m)) for r in selected},seed=7) for m in ("brier","log_loss")}
    return {"kind":"prospective_first_RP_forecast","labels":len(rows),"comparisons":output,"serving_eligible":False,
            "limitation":"Served snapshots may use cached state; same raw/corrected forecast capture is the paired correction comparison"}


def fit_week(db, season, through, code_sha, *, now=None):
    if not settings.rating_rp_enabled or not settings.rating_prediction_log_enabled:
        return {"status":"disabled"}
    through = calibrations.utc(through)
    now = calibrations.utc(now or datetime.now(timezone.utc))
    if through != calibrations.monday(through) or through > now:
        raise ValueError("RP cutoff must be an elapsed Monday 00:00 UTC")
    try:
        spec = get_spec(season)
    except ValueError as exc:
        return {"status":"undeclared_season","reason":str(exc)}
    if not spec.bonus_rps:
        return {"status":"no_declared_bonus_flags"}
    bundle = calibrations.ensure_bundle(db,season)
    if bundle.frozen or db.get(models.FrozenSeason,season):
        return {"status":"season_frozen"}
    base = bootstrap(db,spec,now)
    versions = db.query(models.RatingCalibration).filter_by(season=season,recipe=weekly.RECIPE).order_by(models.RatingCalibration.version.desc()).all()
    existing = next((v for v in versions if v.params.get("bootstrap_id") == base.id and calibrations.utc(v.fitted_through) == through),None)
    if existing:
        return {"status":"already_fitted","id":existing.id}
    earlier = [v for v in versions if v.params.get("bootstrap_id") == base.id and v.status == "candidate"]
    if any(calibrations.utc(v.fitted_through) >= through for v in earlier):
        raise ValueError("RP weekly cutoffs must advance")
    previous = earlier[0] if earlier else base
    validate_version(previous)
    rows = labelled_rows(db,base,through)
    names = [r.name for r in spec.bonus_rps]
    if not rows:
        return {"status":"insufficient_captured_labels"}
    corrections,counts = weekly.fit_roles(rows,names,previous.params["corrections"])
    if not any(c["fitted"] for c in counts.values()):
        return {"status":"insufficient_captured_labels","counts":counts}
    params = {**base.params,"kind":"fitted","bootstrap_id":base.id,"corrections":corrections,
              "training_sha256":digest(rows),"previous_id":previous.id}
    if digest(labelled_rows(db,base,through)) != params["training_sha256"]:
        raise ValueError("RP training labels changed during fitting")
    row = models.RatingCalibration(season=season,recipe=weekly.RECIPE,version=max(v.version for v in versions)+1,
        params=params,fitted_through=through,matches=len({r["key"].split("/")[0] for r in rows}),status="candidate",
        code_sha=code_sha,created_at=now,fit_metrics={"counts":counts,"labels":len(rows),"serving_eligible":False,
            "release_blockers":["prospective RP gates","serving integration","full rehearsal"]})
    db.add(row)
    db.flush()
    db.add(models.RatingRpFitEvidence(calibration_id=row.id,evidence={"bootstrap_id":base.id,
        "training_sha256":params["training_sha256"],
        "labels":[[r["key"],r["y"],r["time"],r["label_updated_at"]] for r in rows]}))
    db.flush()
    return {"status":"candidate","id":row.id,"counts":counts}


def audited_training_rows(db, calibration_id):
    evidence = db.get(models.RatingRpFitEvidence,calibration_id).evidence
    saved = {r.match_key:r for r in db.query(models.RatingRpPrediction).filter_by(calibration_id=evidence["bootstrap_id"])}
    rows = []
    for key,y,time,updated in evidence["labels"]:
        match,color,rp = key.split("/")
        record = saved[match]
        p = record.prediction
        raw = p["raw"][color][rp]
        rows.append({"key":key,"event":record.event_key,"rp":rp,"y":y,"p":raw["probability"],"base":raw["base_rate"],
            "corrected":p["corrected"][color][rp],"served":p["served"][color][rp],"time":time,"label_updated_at":updated})
    if digest(rows) != evidence["training_sha256"]:
        raise ValueError("RP training audit provenance mismatch")
    return rows
