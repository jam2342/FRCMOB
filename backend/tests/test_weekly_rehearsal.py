import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import random

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings import weekly_rehearsal as w, calibrations, state_fitting as state
from app.services.ratings.component_screen import Inputs
from app.services.ratings.replay import MatchCard, MatchResult
from app.services.ratings.research_filter import ScaleFreeFilter, ScaleFreeParams
from app.services.ratings.state_bootstrap import build_bootstrap
from app.services.ratings.state_inputs import digest

PARAMS = ScaleFreeParams(prior=.6,match=.07,event=.15,noise=.6,beta=.7,rookie=.6,noise_count=.2,margin_cov=False)


def data(year,weeks=3):
    rng = random.Random(year)
    records = []
    start = int(datetime(year,3,5,tzinfo=timezone.utc).timestamp())
    for week in range(weeks):
        for i in range(160):
            teams = rng.sample([f"frc{t}" for t in range(1,37)],6)
            time = start+7*86400*week+120*i
            event = f"{year}event{week}"
            card = MatchCard(f"{event}_qm{i+1}",event,time,"qm",start+7*86400*week,tuple(teams[:3]),tuple(teams[3:]))
            red,blue = max(0,int(rng.gauss(100,15))),max(0,int(rng.gauss(100,15)))
            records.append({"result":MatchResult(card,red,blue,red,blue,red_components={"total_points":red},blue_components={"total_points":blue}),
                            "posted":time+60,"type":0})
    return Inputs(year,records,set(),digest([asdict(r["result"]) for r in records]),{})


def artifact():
    return build_bootstrap(2024,{"2023":{"mle":asdict(PARAMS)}},
        [{"season":2023,"plan":[{"params":asdict(PARAMS)}]}],{"fixture":"a"*64})


@pytest.fixture
def fake_fitter(monkeypatch):
    calls = []
    def fit(db,year,cutoff,code_sha):
        existing = db.query(models.RatingCalibration).filter_by(recipe=state.RECIPE,fitted_through=cutoff).first()
        if existing:
            return {"status":"already_fitted","id":existing.id}
        bootstrap = state.latest(db,year,state.BOOTSTRAP_RECIPE)
        inputs,_,_ = state.inputs_for(db,year,cutoff.timestamp(),bootstrap.params["context"],updated_before=cutoff.timestamp())
        if len(inputs.times) < 150:
            return {"status":"insufficient_actual_matches"}
        calls.append(cutoff)
        previous = state.latest(db,year,state.RECIPE)
        p = copy.deepcopy(previous.params["state"] if previous else bootstrap.params["artifact"]["params"])
        p["noise"] *= 1.01
        row = models.RatingCalibration(season=year,recipe=state.RECIPE,version=len(calls),params={"state":p,"layers":calibrations.identity(),
            "bootstrap_id":bootstrap.id,"bootstrap_sha256":digest(bootstrap.params)},fitted_through=cutoff,matches=len(inputs.times),
            code_sha=code_sha,status="candidate",fit_metrics={"rehearsal_seconds":.01})
        db.add(row)
        db.commit()
        return {"status":"candidate","id":row.id}
    monkeypatch.setattr(w,"fit_job",fit)
    return calls


def run(root,**kwargs):
    return w.rehearse(root,data(2024),data(2023,1),artifact(),"p"*64,"test",**kwargs)


def test_weekly_resume_does_not_repeat_completed_fits_or_change_forecasts(tmp_path,fake_fitter):
    partial = run(tmp_path/"resume",max_weeks=2)
    assert partial["status"] == "incomplete"
    before = w.journal_hashes(tmp_path/"resume",w.read_object(tmp_path/"resume/checkpoints",partial["checkpoint"])["journals"])
    completed = run(tmp_path/"resume")
    calls = len(fake_fitter)
    again = run(tmp_path/"resume")
    assert completed["status"] == "complete" and len(completed["frames"]) == 480
    assert again["frames"] == completed["frames"] and len(fake_fitter) == calls
    assert before == w.journal_hashes(tmp_path/"resume",list(before))
    engine = w.prepare_database(tmp_path/"resume/scratch.sqlite")
    with Session(engine) as db:
        assert calibrations.active_bundle(db,2024).calibration_id is None
        with pytest.raises(DatabaseError,match="immutable"):
            db.execute(text("UPDATE rating_calibrations SET params='{}'"))
        db.rollback()
    engine.dispose()


def test_committed_fit_survives_checkpoint_write_failure(tmp_path,fake_fitter,monkeypatch):
    original = w.atomic_json
    count = 0
    def fail(path,value):
        nonlocal count
        if path.name == "head.json":
            count += 1
            if count == 2:
                raise OSError("simulated interrupted checkpoint")
        return original(path,value)
    monkeypatch.setattr(w,"atomic_json",fail)
    with pytest.raises(OSError,match="interrupted"):
        run(tmp_path/"interrupted")
    assert len(fake_fitter) == 1
    monkeypatch.setattr(w,"atomic_json",original)
    resumed = run(tmp_path/"interrupted")
    assert resumed["status"] == "complete"
    assert len(fake_fitter) == len(set(fake_fitter))


def test_tampered_journal_and_changed_inputs_block_resume(tmp_path,fake_fitter):
    partial = run(tmp_path/"tamper",max_weeks=1)
    head = w.read_object(tmp_path/"tamper/checkpoints",partial["checkpoint"])
    path = tmp_path/"tamper/journals"/f"{head['journals'][0]}.json"
    value = json.loads(path.read_text())
    value["forecasts"][0]["forecasts"]["active"]["p"] = .99
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match="hash mismatch"):
        run(tmp_path/"tamper")
    run(tmp_path/"identity",max_weeks=1)
    changed = data(2024)
    changed.source_sha256 = "changed"
    with pytest.raises(ValueError,match="inputs or protocol"):
        w.rehearse(tmp_path/"identity",changed,data(2023,1),artifact(),"p"*64,"test")


def test_read_only_forecast_matches_deepcopy_without_changing_recorded_model():
    model = ScaleFreeFilter(PARAMS,record=True)
    records = data(2024,1).records
    for rec in records[:15]:
        model.observe(rec["result"])
    before = copy.deepcopy(model)
    card = records[16]["result"].card
    expected = copy.deepcopy(model).predict(card)
    actual = w.forecast(model,card,{}, {},records[0]["result"].card.time)
    assert actual["rm"] == expected.red_mean and actual["rv"] == expected.red_var
    assert model.latest == before.latest and model.detail == before.detail
    for key,block in model.blocks.items():
        assert block.mean.tolist() == before.blocks[key].mean.tolist()
        assert block.cov.tolist() == before.blocks[key].cov.tolist()


def test_safety_uses_only_published_original_forecasts():
    records = data(2024,1).records
    source = {r["result"].card.match_key:r for r in records}
    frames = []
    for rec in records:
        r = rec["result"]
        y = float(r.red_score > r.blue_score)
        prediction = {"rm":100.,"bm":100.,"rv":100.,"bv":100.}
        frames.append({"key":r.card.match_key,"event":r.card.event_key,"time":r.card.time,
            "forecasts":{"active":{**prediction,"version":2,"p":.99 if not y else .01},
                         "bootstrap":{**prediction,"version":1,"p":.01 if not y else .99}}})
    now = records[-1]["posted"]+1
    assert w.safety(frames,source,2,now)["rollback"]
    for rec in records:
        rec["posted"] = now
    assert not w.safety(frames,source,2,now)["rollback"]


def test_partial_run_cannot_claim_full_rehearsal_or_hindsight(tmp_path,fake_fitter):
    partial = run(tmp_path/"partial",max_weeks=1)
    report = w.summarize(tmp_path/"partial",data(2024),partial,artifact(),include_hindsight=False)
    assert not report["checks"]["full_season"] and not report["release_passed"]
    assert not report["checks"]["week4_within_hindsight"]
