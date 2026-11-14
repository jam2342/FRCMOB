from dataclasses import asdict, replace
from datetime import datetime, timezone
import math

import pytest
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings import gradual_rehearsal as g, weekly_rehearsal as w, state_fitting as state
from tests.test_weekly_rehearsal import PARAMS, artifact, data


@pytest.fixture
def goals(monkeypatch):
    calls = []
    monkeypatch.setitem(w.POLICY,"compiled",False)
    def fit(inputs,start,names,cutoff,**kwargs):
        calls.append((cutoff,start))
        return {"params":replace(start,noise=start.noise*.98),"converged":True,"evals":1,"runs":1}
    monkeypatch.setattr(g,"fit_likelihood",fit)
    return calls


def run(root, current=None, **kwargs):
    return w.rehearse(root,current or data(2024),data(2023,1),artifact(),"p"*64,"test",fit_callback=g.fit_job,**kwargs)


def test_log_steps_preserve_frozen_parameters_and_choose_largest_safe_descent():
    goal = replace(PARAMS,noise=PARAMS.noise*1.5)
    half = g.interpolate(PARAMS,goal,.5)
    assert half.noise == pytest.approx(math.sqrt(PARAMS.noise*goal.noise))
    assert half.beta == PARAMS.beta and half.rookie == PARAMS.rookie
    for fraction in (0,-1,1.01):
        with pytest.raises(ValueError,match="fraction"):
            g.interpolate(PARAMS,goal,fraction)
    def objective(p):
        return math.log(p.noise/goal.noise)**2
    def impact(p):
        passed = math.log(p.noise/PARAMS.noise) < .11
        return {"passed":passed}
    chosen,selection = g.choose_step(PARAMS,goal,objective,impact)
    assert selection["fraction"] == .25 and len(selection["trials"]) == 3
    assert chosen == g.interpolate(PARAMS,goal,.25)
    assert objective(chosen) < objective(PARAMS)
    observed = []
    chosen,selection = g.choose_step(PARAMS,goal,lambda p:0. if p == PARAMS else 1.,lambda p:observed.append(p))
    assert chosen is None and selection["fraction"] == 0 and observed == []


def test_layers_use_chosen_state_and_each_update_is_bounded_from_active(tmp_path,goals,monkeypatch):
    monkeypatch.setitem(g.POLICY,"fractions",[.5])
    tables = []
    original = state.fit_layers
    def capture(table,centre):
        tables.append(table["rv"].tolist())
        return original(table,centre)
    monkeypatch.setattr(state,"fit_layers",capture)
    result = run(tmp_path)
    assert result["status"] == "complete"
    applied = [j for j in result["jobs"] if j.get("applied")]
    assert applied and all(j["state_fraction"] == .5 and j["impact"]["passed"] for j in applied)
    engine = w.prepare_database(tmp_path/"scratch.sqlite")
    with Session(engine) as db:
        rows = db.query(models.RatingCalibration).filter_by(recipe=g.RECIPE).order_by(models.RatingCalibration.version).all()
        assert len(rows) == len(tables) == len(goals)
        for i,row in enumerate(rows):
            parent = db.get(models.RatingCalibration,row.params["parent_active"])
            start,previous_layers,_ = w.version(db,parent.id)
            selected = state.validate_params(row.params["state"])
            assert start == goals[i][1]
            assert selected.noise == pytest.approx(start.noise*math.sqrt(.98))
            assert selected.noise != row.params["goal_state"]["noise"]
            state.layers.validate_params(row.params["layers"],previous_layers)
            bootstrap = db.get(models.RatingCalibration,row.params["bootstrap_id"])
            inputs,_,_ = state.inputs_for(db,2024,state.layers.utc(row.fitted_through).timestamp(),bootstrap.params["context"],
                updated_before=state.layers.utc(row.fitted_through).timestamp())
            _,table,_,_ = state.replay(inputs,selected)
            assert table["rv"].tolist() == tables[i]
        assert state.layers.active_bundle(db,2024).calibration_id is None
    engine.dispose()


def test_publication_refusal_cannot_become_next_parent(tmp_path,goals,monkeypatch):
    original = w.rating_impact
    in_fit = False
    denied = []
    def impact(old,new,played,anchors):
        value,updated = original(old,new,played,anchors)
        if not in_fit and old.params != new.params and not denied:
            value["passed"] = False
            denied.append(True)
        return value,updated
    monkeypatch.setattr(w,"rating_impact",impact)
    def callback(*args):
        nonlocal in_fit
        in_fit = True
        try:
            return g.fit_job(*args)
        finally:
            in_fit = False
    result = w.rehearse(tmp_path,data(2024),data(2023,1),artifact(),"p"*64,"test",fit_callback=callback)
    candidates = [j for j in result["jobs"] if j.get("numerical_status") == "candidate"]
    assert len(candidates) >= 2 and not candidates[0]["applied"]
    assert candidates[0]["active_after"] == result["bootstrap_id"]
    assert candidates[1]["parent_active"] == result["bootstrap_id"]
    assert goals[0][1] == goals[1][1]
    assert denied


def test_rollback_restarts_from_bootstrap_and_skips_that_fit(tmp_path,goals,monkeypatch):
    calls = []
    def safety(frames,source,active,now):
        calls.append(active)
        return {"rollback":len(calls) == 3,"comparison":{}}
    monkeypatch.setattr(w,"safety",safety)
    result = run(tmp_path,current=data(2024,4))
    job = result["jobs"][2]
    assert job["active_before"] != result["bootstrap_id"]
    assert job["active_after"] == result["bootstrap_id"] and not job["applied"]
    assert job["fit"]["status"] == "skipped_after_rollback" and "rollback_impact_preview" in job
    assert result["jobs"][3]["parent_active"] == result["bootstrap_id"]
    assert goals[0][1] == goals[1][1]


def test_committed_steps_resume_without_refitting_or_rewriting_history(tmp_path,goals,monkeypatch):
    original = w.atomic_json
    failed = False
    def interrupt(path,value):
        nonlocal failed
        if path.name == "head.json" and goals and not failed:
            failed = True
            raise OSError("interrupted after committed gradual fit")
        return original(path,value)
    monkeypatch.setattr(w,"atomic_json",interrupt)
    with pytest.raises(OSError,match="committed gradual"):
        run(tmp_path)
    assert len(goals) == 1
    monkeypatch.setattr(w,"atomic_json",original)
    resumed = run(tmp_path)
    assert len(goals) == len({cutoff for cutoff,_ in goals})
    count = len(goals)
    hashes = w.journal_hashes(tmp_path,w.read_object(tmp_path/"checkpoints",resumed["checkpoint"])["journals"])
    again = run(tmp_path)
    assert resumed["frames"] == again["frames"] and len(goals) == count
    assert hashes == w.journal_hashes(tmp_path,list(hashes))


def test_gradual_version_stays_hidden_through_exact_publication(tmp_path,goals):
    current = data(2024)
    monday = int(datetime(2024,3,11,tzinfo=timezone.utc).timestamp())
    for label,hour in (("before",9.5),("exact",10.),("after",10.5)):
        r = current.records[0]["result"]
        at = monday+int(hour*3600)
        current.records.append({"result":replace(r,card=replace(r.card,match_key=f"2024clock_{label}",time=at)),"posted":at+60,"type":0})
    current.records.sort(key=lambda r:(r["result"].card.time,r["result"].card.match_key))
    current.source_sha256 = w.digest([asdict(r["result"]) for r in current.records])
    result = run(tmp_path,current=current)
    frames = {f["key"]:f for f in result["frames"]}
    assert frames["2024clock_before"]["forecasts"]["active"]["version"] == result["bootstrap_id"]
    assert frames["2024clock_exact"]["forecasts"]["active"]["version"] == result["bootstrap_id"]
    assert frames["2024clock_after"]["forecasts"]["active"]["version"] != result["bootstrap_id"]


def test_layer_failure_rejects_step_and_partial_cannot_pass(tmp_path,goals,monkeypatch):
    def fail(*args):
        raise ValueError("layer fit did not converge")
    monkeypatch.setattr(state,"fit_layers",fail)
    result = run(tmp_path,max_weeks=2)
    assert result["status"] == "incomplete"
    job = result["jobs"][-1]
    assert not job["applied"] and job["numerical_status"] == "rejected"
    assert job["state_fraction"] == 0 and job["fit_errors"] == ["layer fit did not converge"]
    assert job["active_after"] == result["bootstrap_id"]
    report = w.summarize(tmp_path,data(2024),result,artifact(),include_hindsight=False)
    assert not report["checks"]["full_season"] and not report["release_passed"]


@pytest.mark.parametrize("reason",["movement","convergence"])
def test_unusable_goal_cannot_be_published_or_fit_layers(tmp_path,goals,monkeypatch,reason):
    if reason == "movement":
        original = w.rating_impact
        def reject(*args):
            impact,anchors = original(*args)
            return {**impact,"passed":False},anchors
        monkeypatch.setattr(w,"rating_impact",reject)
    else:
        def unconverged(inputs,start,*args,**kwargs):
            return {"params":replace(start,noise=start.noise*.98),"converged":False}
        monkeypatch.setattr(g,"fit_likelihood",unconverged)
    def forbidden(*args):
        raise AssertionError("unusable goal reached layer fitting")
    monkeypatch.setattr(state,"fit_layers",forbidden)
    result = run(tmp_path,max_weeks=2)
    job = result["jobs"][-1]
    assert job["numerical_status"] == "rejected" and not job["applied"]
    assert job["state_fraction"] == 0 and job["active_after"] == result["bootstrap_id"]
    assert bool(job["selection"]) == (reason == "movement")
    assert job["goal_converged"] == (reason == "movement")
