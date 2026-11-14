# Historical virtual-clock simulation. Its journal is never live prospective evidence.
from __future__ import annotations

import copy
from bisect import bisect_right
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
from time import perf_counter

from alembic.migration import MigrationContext
from alembic.operations import Operations
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings import calibrations as layers, state_fitting as state
from app.services.ratings.fast_innovations import PackedInnovations
from app.services.ratings.research_filter import ScaleFreeParams
from app.services.ratings.research_fit import SeasonInputs, Pull, fit_likelihood
from app.services.ratings.research_models import EpaClone, start_multipliers
from app.services.ratings.research_data import ResearchSeason
from app.services.ratings.research_eval import fit_scale, paired_cluster_bootstrap
from app.services.ratings.replay import Season
from app.services.ratings.research_rehearsal import write_settings
from app.services.ratings.research_win import fit_score_calibration, side_arrays, walk_forward_win, WinRecipe, _pooled_temperatures, _pooled_weights
from app.services.ratings.state_bootstrap import build_bootstrap, validate_bootstrap
from app.services.ratings.state_inputs import digest
from app.services.ratings.strength import curve_for_anchors, points_to_rating

RECIPE = "total-weekly-rehearsal-v1"
POLICY = {"state_max_evals":400,"state_restarts":1,"fit_wall_seconds":3000,"minimum_fit_matches":150,
          "job_hour_utc":9,"publication_hour_utc":10,"minimum_rating_matches":8,"minimum_rating_teams":20,
          "anchor_weekly_fraction":.05,"median_rating_step":1.,"maximum_rating_step":5.,
          "safety_days":14,"minimum_safety_matches":150,"early_brier_tolerance":.001,
          "hindsight_brier_tolerance":.001,"compiled":True}


def read_object(folder, sha):
    if not isinstance(sha,str) or not re.fullmatch(r"[0-9a-f]{64}",sha):
        raise ValueError("invalid rehearsal object reference")
    path = Path(folder)/f"{sha}.json"
    if path.resolve().parent != Path(folder).resolve():
        raise ValueError("rehearsal object leaves its folder")
    value = json.loads(path.read_text())
    if digest(value) != sha:
        raise ValueError("rehearsal object hash mismatch")
    return value


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError("rehearsal pointer cannot be a symlink")
    with temporary.open("w") as out:
        json.dump(value,out,sort_keys=True,allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary,path)


def journal_hashes(root, names):
    result = {}
    for name in names:
        read_object(root/"journals",name)
        result[name] = hashlib.sha256((root/"journals"/f"{name}.json").read_bytes()).hexdigest()
    return result


def make_bootstrap(year, reports, tables):
    if year not in range(2023,2027):
        raise ValueError("weekly rehearsal needs examined 2023–2026 and an earlier season")
    fits,runs,hashes = {},[],{}
    for earlier in range(2022,year):
        for variant in ("hindsight_mle","e3_noise_count_zstatic"):
            path = Path(reports)/"runs"/f"{variant}-{earlier}.json"
            value = json.loads(path.read_text())
            if value["season"] != earlier or value["variant"] != variant:
                raise ValueError("rehearsal bootstrap source season mismatch")
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            if variant == "hindsight_mle":
                fits[str(earlier)] = {"mle":value["plan"][-1]["params"]}
            else:
                runs.append(value)
    scores,wins,years = [],[],[]
    recipe = WinRecipe(base="logistic",correction="logistic",past=True)
    for earlier in range(2023,year):
        path = Path(tables)/f"{earlier}.npz"
        with np.load(path,allow_pickle=False) as raw:
            table = {k:raw[k] for k in raw.files}
        scores.append(fit_score_calibration(side_arrays(table),terms=True).coef)
        _,_,fit = walk_forward_win(table,recipe,wins)
        wins.append(fit)
        years.append(earlier)
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    fitted = layers.identity()
    if wins:
        temps = _pooled_temperatures(wins)
        fitted.update(score_coef=np.mean(scores,axis=0).tolist(),temperatures=temps.by_context.tolist(),
                      pooled_temperature=temps.pooled,weights=_pooled_weights(wins,recipe).tolist())
    artifact = build_bootstrap(year,fits,runs,hashes,layer_params=fitted,layer_years=years)
    validate_bootstrap(artifact,year)
    return artifact


def prepare_database(path):
    engine = create_engine(f"sqlite:///{path}")
    if not path.exists():
        models.Base.metadata.create_all(engine)
        folder = Path(__file__).resolve().parents[3]/"alembic/versions"
        with engine.begin() as conn:
            for name in ("rating_rp_fit_evidence","rating_rp_predictions","rating_state_predictions","match_prediction_log",
                         "rating_gate_scores","rating_active_bundle","rating_calibrations"):
                models.Base.metadata.tables[name].drop(conn)
            for filename in ("20261113_0031_rating_settings_log.py","20261113_0032_state_predictions.py","20261114_0033_rp_predictions.py"):
                spec = importlib.util.spec_from_file_location("rehearsal_migration",folder/filename)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
    return engine


def ingest(db, records, year):
    present = {r.match_key:r for r in db.query(models.Match).join(models.Event).filter(models.Event.year == year)}
    sides = {(r.match_key,r.alliance):r for r in db.query(models.MatchAllianceResult).filter_by(season_year=year)}
    events = {key for (key,) in db.query(models.Event.event_key)}
    for record in records:
        r = record["result"]
        card = r.card
        if card.match_key in present:
            stored = present[card.match_key]
            if (stored.event_key,stored.actual_time,stored.comp_level) != (card.event_key,card.time,card.comp_level):
                raise ValueError("scratch match differs from frozen input")
            for color,teams,score,points in (("red",card.red,r.red_score,r.red_points),("blue",card.blue,r.blue_score,r.blue_points)):
                old = sides.get((card.match_key,color))
                if old is None or old.updated_at is None or (tuple(old.team_keys),old.total_score,old.score_without_fouls,
                        layers.utc(old.updated_at).timestamp()) != (teams,score,points,record["posted"]):
                    raise ValueError("scratch result differs from frozen input")
            continue
        if card.event_key not in events:
            db.add(models.Event(event_key=card.event_key,name=card.event_key,year=year))
            events.add(card.event_key)
        db.add(models.Match(match_key=card.match_key,event_key=card.event_key,comp_level=card.comp_level,
            actual_time=card.time,time=card.time,set_number=1,match_number=1))
        for color,teams,score,points in (("red",card.red,r.red_score,r.red_points),("blue",card.blue,r.blue_score,r.blue_points)):
            db.add(models.MatchAllianceResult(match_key=card.match_key,event_key=card.event_key,season_year=year,
                alliance=color,team_keys=list(teams),total_score=score,score_without_fouls=points,
                updated_at=datetime.fromtimestamp(record["posted"],timezone.utc)))
    db.commit()


def version(db, identifier):
    row = db.get(models.RatingCalibration,identifier)
    if row.recipe == state.BOOTSTRAP_RECIPE:
        p = row.params["artifact"]
        return state.validate_params(p["params"]),p["layers"],digest(row.params)
    return state.validate_params(row.params["state"]),row.params["layers"],digest(row.params)


def forecast(model, card, here, total, first):
    # predict() only writes its target event and latest pointers; clone those surfaces.
    shadow = copy.copy(model)
    shadow.blocks = model.blocks.copy()
    if card.event_key in model.blocks:
        shadow.blocks[card.event_key] = copy.deepcopy(model.blocks[card.event_key])
    shadow.latest = model.latest.copy()
    shadow.record = False
    return state.feature(shadow,card,shadow.predict(card),here,total,first)


def predicted(raw, fitted, identifier, sha):
    table = {k:np.array([v]) for k,v in raw.items()}
    table["y"] = np.array([.5])
    red,blue,p = layers.apply(table,fitted)
    return {"version":identifier,"params_sha256":sha,"raw":raw,"rm":float(red[0]),"bm":float(blue[0]),
            "rv":raw["rv"],"bv":raw["bv"],"p":float(p[0])}


class Replay:
    def __init__(self, data, context, bootstrap_params):
        self.data,self.context,self.params = data,context,bootstrap_params
        self.pending = sorted((r["posted"],r["result"].card.match_key,r) for r in data.records if r["posted"] is not None)
        self.applied,self.known,self.models = 0,[],{}
        self.here,self.total,self.starts = {},{},{}
        self.first = None
        self.last = (-1,"")
        self.chain = digest([])
        self.epa = self.new_epa()
        self.ordered,self.keys,self.snapshots = [],[],[]

    def new_epa(self):
        return EpaClone(start_multipliers(self.context["z_previous"],self.data.rookies,
            beta=self.params.beta,rookie=self.params.rookie),self.context["previous_unit"])

    def add_model(self, identifier, params):
        model = self.new_model(params)
        for record in self.ordered:
            model.observe(record["result"])
        self.models[identifier] = model
        self.snapshots = []

    def retain(self, identifiers):
        if set(self.models) != identifiers:
            self.models = {i:m for i,m in self.models.items() if i in identifiers}
            self.snapshots = []

    def new_model(self, params):
        from app.services.ratings.research_filter import ScaleFreeFilter
        return ScaleFreeFilter(params,z_previous=self.context["z_previous"],rookies=self.data.rookies,
                               previous_unit=self.context["previous_unit"])

    def advance(self, time):
        while self.applied < len(self.pending) and self.pending[self.applied][0] < time:
            record = self.pending[self.applied][2]
            r = record["result"]
            card = r.card
            self.known.append(record)
            self.chain = digest([self.chain,asdict(r),record["posted"]])
            self.first = min(self.first if self.first is not None else card.time,card.time)
            self.starts[card.event_key] = min(self.starts.get(card.event_key,card.time),card.time)
            for team in card.red+card.blue:
                self.total[team] = self.total.get(team,0)+1
                pair = (team,card.event_key)
                self.here[pair] = self.here.get(pair,0)+1
            key = (card.time,card.match_key)
            position = bisect_right(self.keys,key)
            self.keys.insert(position,key)
            self.ordered.insert(position,record)
            if key < self.last:
                self.snapshots = [s for s in self.snapshots if s["key"] < key]
                start = 0
                if self.snapshots:
                    snapshot = self.snapshots[-1]
                    self.models,self.epa = copy.deepcopy((snapshot["models"],snapshot["epa"]))
                    start = snapshot["count"]
                else:
                    self.models = {i:self.new_model(m.params) for i,m in self.models.items()}
                    self.epa = self.new_epa()
                for known in self.ordered[start:]:
                    for model in self.models.values():
                        model.observe(known["result"])
                    self.epa.observe(known["result"])
            else:
                for model in self.models.values():
                    model.observe(r)
                self.epa.observe(r)
            self.last = max(self.last,key)
            self.applied += 1
            if not self.snapshots or len(self.ordered)-self.snapshots[-1]["count"] >= 100:
                saved_models,saved_epa = copy.deepcopy((self.models,self.epa))
                self.snapshots.append({"key":self.last,"count":len(self.ordered),"models":saved_models,"epa":saved_epa})
                self.snapshots = self.snapshots[-8:]

    def capture(self, record, fitted, active, bootstrap, challenger, epa_scale):
        card = record["result"].card
        card = replace(card,event_start=min(self.starts.get(card.event_key,card.time),card.time))
        variants = {}
        for name,identifier in (("bootstrap",bootstrap),("active",active),("challenger",challenger)):
            raw = forecast(self.models[identifier],card,self.here,self.total,min(self.first or card.time,card.time))
            variants[name] = predicted(raw,fitted[identifier][1],identifier,fitted[identifier][2])
        epa = copy.copy(self.epa)
        epa.epa = self.epa.epa.copy()
        point = epa.predict(card)
        unit = epa.unit.value()
        x = (point.red_mean-point.blue_mean)/unit
        default = 1.702/(.42*max((point.red_mean+point.blue_mean)/(2*unit),1e-9))
        p = 1/(1+math.exp(-max(-30.,min(30.,(epa_scale or default)*x))))
        variants["epa"] = {"rm":point.red_mean,"bm":point.blue_mean,"p":p,"x":x}
        return {"key":card.match_key,"event":card.event_key,"time":card.time,"event_type":record["type"],
                "playoff":card.comp_level != "qm","first_here":min(self.here.get((t,card.event_key),0) for t in card.red+card.blue) == 0,
                "history_chain_sha256":self.chain,"history_matches":len(self.known),"forecasts":variants}


def rating_impact(old, new, played, anchors):
    teams = sorted(t for t,n in played.items() if n >= POLICY["minimum_rating_matches"])
    if len(teams) < POLICY["minimum_rating_teams"]:
        return {"n":len(teams),"passed":False,"reason":"insufficient rated teams"},anchors
    def points(model,t):
        return model.team_points(t,model.latest[t])
    raw = np.quantile([points(old,t) for t in teams],[.5,.99])
    if raw[0] <= 0 or raw[1] <= raw[0]:
        return {"n":len(teams),"passed":False,"reason":"invalid anchor population"},anchors
    updated = raw.tolist() if anchors is None else [(1-POLICY["anchor_weekly_fraction"])*a+POLICY["anchor_weekly_fraction"]*b for a,b in zip(anchors,raw)]
    curve = curve_for_anchors(*updated)
    change = [abs(points_to_rating(points(new,t),*curve)-points_to_rating(points(old,t),*curve)) for t in teams]
    median,maximum = float(np.median(change)),max(change)
    return {"n":len(teams),"median":median,"max":maximum,"anchors":updated,
            "passed":median < POLICY["median_rating_step"] and maximum <= POLICY["maximum_rating_step"]},updated


def row_loss(frame, source, variant, metric):
    r = source[frame["key"]]["result"]
    p = frame["forecasts"][variant]
    if r.red_points is None or r.blue_points is None:
        return None
    y = .5 if r.red_score == r.blue_score else float(r.red_score > r.blue_score)
    if metric in ("brier","log_loss"):
        if y == .5:
            return None
        q = min(1-1e-9,max(1e-9,p["p"]))
        return (q-y)**2 if metric == "brier" else -(y*math.log(q)+(1-y)*math.log(1-q))
    errors = [(r.red_points-p["rm"],p.get("rv")),(r.blue_points-p["bm"],p.get("bv"))]
    if metric == "mae":
        return sum(abs(e) for e,_ in errors)/2
    if any(v is None or v <= 0 for _,v in errors):
        return None
    if metric == "coverage":
        return sum(abs(e) <= 1.2816*math.sqrt(v) for e,v in errors)/2
    return sum(.5*(math.log(2*math.pi*v)+e*e/v) for e,v in errors)/2


def compare(frames, source, a="active", b="bootstrap"):
    output = {}
    for metric in ("brier","log_loss","mae","nll","coverage"):
        values = {name:{f["key"]:(f["event"],v) for f in frames if (v := row_loss(f,source,name,metric)) is not None} for name in (a,b)}
        output[metric] = paired_cluster_bootstrap(values[a],values[b],seed=7)
        for name in (a,b):
            output[metric][name] = sum(v[1] for v in values[name].values())/len(values[name]) if values[name] else None
    return output


def safety(frames, source, active, now):
    selected = [f for f in frames if now-POLICY["safety_days"]*86400 <= f["time"] < now and
                source[f["key"]]["posted"] is not None and source[f["key"]]["posted"] < now and
                f["forecasts"]["active"]["version"] == active]
    comparison = compare(selected,source)["brier"]
    return {"rollback":comparison.get("n",0) >= POLICY["minimum_safety_matches"] and comparison.get("lo90",-math.inf) > 0,
            "comparison":comparison}


def make_inputs(data, records, context):
    results = sorted((r["result"] for r in records),key=lambda r:(r.card.time,r.card.match_key))
    starts = {}
    for r in results:
        starts[r.card.event_key] = min(starts.get(r.card.event_key,r.card.time),r.card.time)
    results = [replace(r,card=replace(r.card,event_start=starts[r.card.event_key])) for r in results]
    first = results[0].card.time if results else 0
    return SeasonInputs(ResearchSeason(Season(data.year,results,set(),{})),[(r.card.time-first)//604800 for r in results],
                        [r.card.time for r in results],context["z_previous"],set(data.rookies),context["previous_unit"],[])


def fit_job(db, year, cutoff, code_sha):
    engines,checks = [],{}
    def factory(inputs, start):
        packed = PackedInnovations(inputs)
        checks["start"] = packed.verify(start)
        engines.append(packed)
        return packed
    started = perf_counter()
    before = {r.id:digest(r.params) for r in db.query(models.RatingCalibration)}
    result = state.fit_week(db,year,cutoff,code_sha,max_evals=POLICY["state_max_evals"],
                            innovations_factory=factory if POLICY["compiled"] else None)
    if result.get("id") and result["status"] != "already_fitted":
        row = db.get(models.RatingCalibration,result["id"])
        if engines:
            checks["fitted"] = engines[0].verify(ScaleFreeParams(**row.params["state"]))
        row.fit_metrics = {**row.fit_metrics,"rehearsal_parity":checks,"rehearsal_seconds":perf_counter()-started}
    for identifier,sha in before.items():
        if digest(db.get(models.RatingCalibration,identifier).params) != sha:
            raise ValueError("fitting rewrote an existing calibration")
    db.commit()
    return result


def rehearse(root, data, previous, artifact, protocol_sha, code_sha, *, max_weeks=None, progress=None):
    root = Path(root)
    root.mkdir(parents=True,exist_ok=True)
    for child in ("journals","checkpoints","supplements","reports"):
        if (root/child).is_symlink():
            raise ValueError("rehearsal object folder cannot be a symlink")
    validate_bootstrap(artifact,data.year)
    if artifact["layers"]["blend"] != 1:
        raise ValueError("rehearsal needs a full candidate link; no synthetic production blend")
    if (root/"scratch.sqlite").is_symlink():
        raise ValueError("rehearsal database cannot be a symlink")
    identity = {"schema":RECIPE,"season":data.year,"protocol_sha256":protocol_sha,"data_sha256":data.source_sha256,
                "previous_sha256":previous.source_sha256,"artifact_sha256":digest(artifact)}
    manifest = root/"manifest.json"
    if manifest.exists():
        if json.loads(manifest.read_text()) != identity:
            raise ValueError("rehearsal inputs or protocol changed; use a new directory")
    else:
        atomic_json(manifest,identity)
    head_path = root/"head.json"
    checkpoint = read_object(root/"checkpoints",json.loads(head_path.read_text())["sha256"]) if head_path.exists() else None
    journals = checkpoint["journals"] if checkpoint else []
    journal_hashes(root,journals)
    frames = [f for sha in journals for f in read_object(root/"journals",sha)["forecasts"]]
    jobs = checkpoint["jobs"] if checkpoint else []
    engine = prepare_database(root/"scratch.sqlite")
    try:
        with Session(engine) as db:
            if checkpoint is None:
                present = {t for (t,) in db.query(models.Team.team_key)}
                teams = sorted({t for collection in (data,previous) for rec in collection.records for t in rec["result"].card.red+rec["result"].card.blue}|data.rookies|previous.rookies)
                for team in teams:
                    if team in present:
                        continue
                    year = data.year if team in data.rookies else data.year-1 if team in previous.rookies else None
                    db.add(models.Team(team_key=team,team_number=int(team[3:].rstrip("abcdefghijklmnopqrstuvwxyz")),rookie_year=year))
                db.commit()
                boundary = datetime(data.year,1,1,tzinfo=timezone.utc).timestamp()
                ingest(db,[r for r in previous.records if r["posted"] is not None and r["posted"] < boundary],data.year-1)
            bootstrap = state.install_bootstrap(db,artifact,code_sha)
            db.commit()
            active = checkpoint["active"] if checkpoint else bootstrap.id
            latest = checkpoint["latest"] if checkpoint else bootstrap.id
            anchors = checkpoint["anchors"] if checkpoint else None
            epa_scale = checkpoint["epa_scale"] if checkpoint else None
            if checkpoint:
                for identifier,sha in checkpoint["versions"].items():
                    if digest(db.get(models.RatingCalibration,int(identifier)).params) != sha:
                        raise ValueError("checkpoint calibration changed")
            context = bootstrap.params["context"]
            replay = Replay(data,context,ScaleFreeParams(**artifact["params"]))
            fitted_cache = {}
            def saved_version(identifier):
                if identifier not in fitted_cache:
                    fitted_cache[identifier] = version(db,identifier)
                return fitted_cache[identifier]
            first = layers.monday(datetime.fromtimestamp(data.records[0]["result"].card.time,timezone.utc))
            final = layers.monday(datetime.fromtimestamp(data.records[-1]["result"].card.time,timezone.utc))+timedelta(days=7)
            week = datetime.fromisoformat(checkpoint["next_week"]) if checkpoint else first
            replay.advance(week.timestamp())
            if checkpoint and replay.chain != checkpoint["history_chain_sha256"]:
                raise ValueError("checkpoint history changed")
            for identifier in {bootstrap.id,active,latest}:
                replay.add_model(identifier,saved_version(identifier)[0])
            source = {r["result"].card.match_key:r for r in data.records}
            completed = 0
            while week <= final and (max_weeks is None or completed < max_weeks):
                before = journal_hashes(root,journals)
                end = week+timedelta(days=7)
                events = [(int((week+timedelta(hours=POLICY["job_hour_utc"])).timestamp()),0,None),
                          (int((week+timedelta(hours=POLICY["publication_hour_utc"])).timestamp()),1,None)]
                events += [(r["result"].card.time,2,r) for r in data.records if week.timestamp() <= r["result"].card.time < end.timestamp()]
                # A forecast immediately before a start cannot see a simultaneous publication.
                events.sort(key=lambda e:(e[0],0 if e[1] == 2 else e[1]+1,e[2]["result"].card.match_key if e[2] else ""))
                pending,job,week_frames = None,{},[]
                for time,kind,record in events:
                    replay.advance(time)
                    if kind == 0:
                        decision = safety(frames+week_frames,source,active,time)
                        job = {"cutoff":week.isoformat(),"safety":decision,"active_before":active}
                        if decision["rollback"]:
                            active = bootstrap.id
                            job["fit"] = {"status":"skipped_after_rollback"}
                        else:
                            ingest(db,replay.known,data.year)
                            job["fit"] = fit_job(db,data.year,week,code_sha)
                            identifier = job["fit"].get("id")
                            if identifier:
                                row = db.get(models.RatingCalibration,identifier)
                                job["numerical_status"] = row.status
                                job["fit_errors"] = row.fit_metrics.get("errors",[])
                                job["fit_seconds"] = row.fit_metrics.get("rehearsal_seconds")
                                job["parity"] = row.fit_metrics.get("rehearsal_parity")
                                if row.status == "candidate":
                                    pending = identifier
                    elif kind == 1:
                        if pending is not None:
                            latest = pending
                        if latest not in replay.models:
                            replay.add_model(latest,saved_version(latest)[0])
                        proposed = pending if pending is not None else active
                        impact,anchors = rating_impact(replay.models[active],replay.models[proposed],replay.total,anchors)
                        job["impact"] = impact
                        budget = job.get("fit_seconds") is None or job["fit_seconds"] <= (POLICY["publication_hour_utc"]-POLICY["job_hour_utc"])*3600
                        job["applied"] = pending is not None and impact["passed"] and budget
                        job["publication_budget_passed"] = budget
                        if job["applied"]:
                            active = pending
                        job["active_after"] = active
                        decided = [f for f in frames if source[f["key"]]["posted"] is not None and source[f["key"]]["posted"] < week.timestamp()
                                   and source[f["key"]]["result"].red_points is not None and source[f["key"]]["result"].blue_points is not None
                                   and source[f["key"]]["result"].red_score != source[f["key"]]["result"].blue_score]
                        if len(decided) >= 150:
                            fitted = fit_scale(np.array([f["forecasts"]["epa"]["x"] for f in decided]),
                                np.array([float(source[f["key"]]["result"].red_score > source[f["key"]]["result"].blue_score) for f in decided]))
                            epa_scale = fitted if fitted > 0 else None
                        replay.retain({bootstrap.id,active,latest})
                    else:
                        fitted = {identifier:saved_version(identifier) for identifier in {bootstrap.id,active,latest}}
                        week_frames.append(replay.capture(record,fitted,active,bootstrap.id,latest,epa_scale))
                replay.advance(end.timestamp())
                if before != journal_hashes(root,journals):
                    raise ValueError("a fit or transition rewrote saved forecasts")
                journal = {"schema":RECIPE,"kind":"historical_virtual_forecasts","week":week.isoformat(),"forecasts":week_frames}
                path = write_settings(root/"journals",journal)
                journals.append(path.stem)
                frames.extend(week_frames)
                jobs.append(job)
                payload = {"schema":RECIPE,"identity":identity,"next_week":end.isoformat(),"active":active,"latest":latest,
                    "anchors":anchors,"epa_scale":epa_scale,"journals":journals,"jobs":jobs,"history_chain_sha256":replay.chain,
                    "versions":{str(r.id):digest(r.params) for r in db.query(models.RatingCalibration)},"history_preserved":True}
                saved = write_settings(root/"checkpoints",payload)
                atomic_json(head_path,{"sha256":saved.stem})
                if progress:
                    progress({"season":data.year,"cutoff":week.isoformat(),"forecasts":len(week_frames),"fit":job.get("fit"),
                              "fit_seconds":job.get("fit_seconds"),"applied":job.get("applied"),"checkpoint":saved.stem})
                week,completed = end,completed+1
            return {"status":"complete" if week > final else "incomplete","identity":identity,"frames":frames,"jobs":jobs,
                    "checkpoint":json.loads(head_path.read_text())["sha256"],"bootstrap_id":bootstrap.id,"context":context,
                    "history_preserved":True,"serving_eligible":False}
    finally:
        engine.dispose()


def hindsight(root, data, run, artifact):
    path = root/"hindsight.json"
    if path.exists():
        return read_object(root/"supplements",json.loads(path.read_text())["sha256"])
    before = journal_hashes(root,read_object(root/"checkpoints",run["checkpoint"])["journals"])
    inputs = make_inputs(data,data.records,run["context"])
    start = ScaleFreeParams(**artifact["params"])
    packed = PackedInnovations(inputs) if POLICY["compiled"] else None
    checks = {"start":packed.verify(start)} if packed else {}
    pull = Pull(artifact["pull"]["centre"],artifact["pull"]["width"])
    fitted = fit_likelihood(inputs,start,state.NAMES,None,pull=pull,max_evals=POLICY["state_max_evals"],restarts=1,
                            max_seconds=POLICY["fit_wall_seconds"],innovations_fn=packed,zero_terms=())
    if packed:
        checks["fitted"] = packed.verify(fitted["params"])
    errors = []
    if not fitted["converged"]:
        errors.append("hindsight optimizer did not converge")
    fitted_layers = artifact["layers"]
    try:
        state.validate_params(asdict(fitted["params"]))
        _,table,_,_ = state.replay(inputs,fitted["params"])
        fitted_layers = state.fit_layers(table,artifact["layers"])
        layers.validate_params(fitted_layers,artifact["layers"])
    except (ValueError,np.linalg.LinAlgError) as exc:
        errors.append(str(exc))
    payload = {"kind":"whole_season_hindsight_diagnostic_not_prospective","state":asdict(fitted["params"]),"layers":fitted_layers,
        "fit":{k:v for k,v in fitted.items() if k != "params"},"parity":checks,"errors":errors,"forecasts":[]}
    if not errors:
        replay = Replay(data,run["context"],start)
        replay.add_model(-1,fitted["params"])
        for record in data.records:
            card = record["result"].card
            replay.advance(card.time)
            card = replace(card,event_start=min(replay.starts.get(card.event_key,card.time),card.time))
            raw = forecast(replay.models[-1],card,replay.here,replay.total,min(replay.first or card.time,card.time))
            payload["forecasts"].append({"key":card.match_key,"prediction":predicted(raw,fitted_layers,-1,digest(asdict(fitted["params"])))})
    if before != journal_hashes(root,list(before)):
        raise ValueError("hindsight analysis rewrote the original forecast journal")
    saved = write_settings(root/"supplements",payload)
    atomic_json(path,{"sha256":saved.stem})
    return payload


def summarize(root, data, run, artifact, *, include_hindsight=True):
    frames = copy.deepcopy(run["frames"])
    source = {r["result"].card.match_key:r for r in data.records}
    starts = {}
    for frame in frames:
        starts[frame["event"]] = min(starts.get(frame["event"],frame["time"]),frame["time"])
    first = min(starts.values()) if starts else 0
    newest = set(sorted(starts,key=lambda k:(starts[k],k))[-max(1,math.ceil(len(starts)*.2)):])
    slices = {"all":lambda f:True,"early_14_days":lambda f:f["time"] < first+14*86400,
              "week4_on":lambda f:f["time"] >= first+21*86400,"newest_events":lambda f:f["event"] in newest,
              "championships":lambda f:f["event_type"] in (2,3,4,5),"first_here":lambda f:f["first_here"]}
    metrics = {name:{"active_minus_bootstrap":compare([f for f in frames if choose(f)],source),
                     "active_minus_epa":compare([f for f in frames if choose(f)],source,b="epa")} for name,choose in slices.items()}
    oracle = None
    if include_hindsight and run["status"] == "complete":
        oracle = hindsight(Path(root),data,run,artifact)
        if not oracle["errors"]:
            lookup = {f["key"]:f["prediction"] for f in oracle["forecasts"]}
            for f in frames:
                f["forecasts"]["hindsight"] = lookup[f["key"]]
            for name,choose in slices.items():
                metrics[name]["active_minus_hindsight"] = compare([f for f in frames if choose(f)],source,b="hindsight")
    all_brier = metrics["all"]["active_minus_bootstrap"]["brier"]
    early = metrics["early_14_days"]["active_minus_bootstrap"]["brier"]
    late = metrics["week4_on"].get("active_minus_hindsight",{}).get("brier",{})
    applied = [j for j in run["jobs"] if j.get("applied")]
    checks = {"full_season":run["status"] == "complete" and len(frames) == len(data.records),
        "beats_frozen_bootstrap":all_brier.get("hi90",math.inf) < 0,
        "early_not_worse":early.get("hi90",math.inf) <= POLICY["early_brier_tolerance"],
        "week4_within_hindsight":late.get("hi90",math.inf) <= POLICY["hindsight_brier_tolerance"],
        "not_worse_than_epa":metrics["all"]["active_minus_epa"]["brier"].get("hi90",math.inf) <= 0,
        "rating_steps":all(j["impact"]["passed"] for j in applied),"history_preserved":run["history_preserved"],
        "rollback_restored":all(not j["safety"]["rollback"] or j["active_after"] == run["bootstrap_id"] for j in run["jobs"])}
    return {k:v for k,v in {**run,"schema":RECIPE,"kind":"historical_virtual_rehearsal","metrics":metrics,"checks":checks,
        "weekly_updates":len(applied),"rollbacks":sum(j["safety"]["rollback"] for j in run["jobs"]),
        "numerical_rejections":sum(j.get("numerical_status") == "rejected" for j in run["jobs"]),
        "impact_refusals":sum(j.get("numerical_status") == "candidate" and not j["impact"]["passed"] for j in run["jobs"]),
        "data_coverage":{"history_matches":len(data.records),"updates_available":sum(r["posted"] is not None for r in data.records),"skipped":data.skipped},
        "recipe_promotions":0,"recipe_demotions":0,"hindsight_errors":oracle["errors"] if oracle else ["not run"],
        "release_passed":False,"release_blockers":["clean 2027","held-out production network / late-event win gap",
            "RP acceptance and serving integration","production state activation remains disabled",
            "2022 has no earlier-season bootstrap in this cache"],
        "limitations":["Virtual-clock files are historical simulation, not live prospective capture",
            "Weekly state fitting reuses the existing service; offline application adds impact and baseline rollback guards",
            "Settings publish Monday 10:00 UTC, after the 09:00 job with a one-hour publication budget",
            "Calibration training replays refitted prefix states; evaluation uses the original saved forecasts",
            "Corrections in final cached labels lack historical correction-publication timestamps",
            "All historical seasons have been examined; no fresh blind accuracy claim",
            "Private tracked anchors are damped 5% weekly and shared within each impact check; serving scale integration is separate"]}.items()
            if k not in ("frames","context")}
