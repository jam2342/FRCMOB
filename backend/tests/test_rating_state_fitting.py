from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import random

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.core.config import settings
from app.db import models
from app.services.ratings import calibrations, state_fitting as state, state_predictions as forecasts
from app.services.ratings.research_filter import ScaleFreeParams
from app.services.ratings.research_fit import fit_likelihood
from app.services.ratings.state_bootstrap import build_bootstrap, validate_bootstrap
from app.services.ratings.state_inputs import inputs_for, load_results, digest, results_digest
from tests import test_rating_calibrations as shared

CUTOFF = datetime(2027,3,1,tzinfo=timezone.utc)
PARAMS = ScaleFreeParams(prior=.6, match=.07, event=.15, noise=.6, beta=.7, rookie=.6, noise_count=.2, margin_cov=False)


@pytest.fixture
def state_on(monkeypatch):
    for name in ("rating_state_fit_enabled", "rating_autotune", "rating_prediction_log_enabled"):
        monkeypatch.setattr(settings, name, True)


@pytest.fixture
def state_db(db_engine, db_session):
    return shared.migrated.__wrapped__(db_engine, db_session)


def artifact():
    mle = asdict(PARAMS)
    mle["noise_count"] = 0.0
    return build_bootstrap(2027, {str(y):{"mle":mle} for y in (2025,2026)},
                           [{"season":2026,"plan":[{"params":asdict(PARAMS)}]}], {"fixture":"a"*64})


def seed(db, year, *, n=160, start=None):
    rng = random.Random(year)
    when = start or datetime(year,2,1,tzinfo=timezone.utc)
    event = f"{year}test"
    if not db.get(models.Event,event):
        db.add(models.Event(event_key=event,name="Test",year=year))
    for i in range(1,13):
        if not db.get(models.Team,f"frc{i}"):
            db.add(models.Team(team_key=f"frc{i}",team_number=i))
    db.flush()
    for i in range(n):
        key = f"{event}_qm{i+1}"
        teams = rng.sample([f"frc{i}" for i in range(1,13)],6)
        moment = int((when+timedelta(seconds=120*i)).timestamp())
        db.add(models.Match(match_key=key,event_key=event,comp_level="qm",match_number=i+1,set_number=1,time=moment,actual_time=moment))
        for color, keys in (("red",teams[:3]),("blue",teams[3:])):
            score = max(0,int(sum(20+3*int(t[3:]) for t in keys)+rng.gauss(0,14)))
            db.add(models.MatchAllianceResult(match_key=key,event_key=event,season_year=year,alliance=color,
                team_keys=keys,total_score=score,score_without_fouls=score))
    db.commit()


def test_bootstrap_rejects_future_and_wrong_season():
    data = artifact()
    validate_bootstrap(data,2027)
    data["source_seasons"].append(2027)
    with pytest.raises(ValueError,match="earlier"):
        validate_bootstrap(data,2027)
    with pytest.raises(ValueError,match="another"):
        validate_bootstrap(artifact(),2026)


def test_bootstrap_is_immutable_idempotent_and_prior_is_average(state_db):
    seed(state_db,2026,n=30)
    first = state.install_bootstrap(state_db,artifact(),"test")
    assert state.install_bootstrap(state_db,artifact(),"test").id == first.id
    assert first.params["context"]["previous_unit"] > 0
    assert first.params["artifact"]["prior_params"]["match"] == 1e-6
    assert first.params["artifact"]["prior_params"]["event"] == 1e-6
    state_db.commit()
    with pytest.raises(DatabaseError,match="immutable"):
        state_db.execute(text("UPDATE rating_calibrations SET params='{}'"))
    state_db.rollback()


def test_actual_playing_time_is_required_and_future_labels_do_not_reach_fit(db_session):
    db = db_session
    seed(db,2026,n=30)
    seed(db,2027,n=160)
    bootstrap = state.install_bootstrap(db,artifact(),"test")
    key = "2027late_qm1"
    db.add(models.Event(event_key="2027late",name="Late",year=2027))
    db.add(models.Match(match_key=key,event_key="2027late",comp_level="qm",set_number=1,match_number=1,
        actual_time=int((CUTOFF+timedelta(days=2)).timestamp()),time=int((CUTOFF-timedelta(days=20)).timestamp())))
    for color,teams in (("red",["frc1","frc2","frc3"]),("blue",["frc4","frc5","frc6"])):
        db.add(models.MatchAllianceResult(match_key=key,event_key="2027late",season_year=2027,alliance=color,
            team_keys=teams,total_score=100,score_without_fouls=100))
    db.commit()
    a, sha, _ = inputs_for(db,2027,CUTOFF.timestamp(),bootstrap.params["context"])
    db.query(models.MatchAllianceResult).filter_by(match_key=key).update({"total_score":999,"score_without_fouls":999})
    db.flush()
    b, sha2, _ = inputs_for(db,2027,CUTOFF.timestamp(),bootstrap.params["context"])
    assert sha == sha2 and len(a.times) == len(b.times) == 160
    pull = state.WeeklyPull(artifact(),PARAMS)
    fit_a = fit_likelihood(a,PARAMS,("noise",),int(CUTOFF.timestamp()),pull=pull,max_evals=60,restarts=1)
    fit_b = fit_likelihood(b,PARAMS,("noise",),int(CUTOFF.timestamp()),pull=pull,max_evals=60,restarts=1)
    assert asdict(fit_a["params"]) == asdict(fit_b["params"])
    later, _, _ = inputs_for(db,2027,(CUTOFF+timedelta(days=3)).timestamp(),bootstrap.params["context"])
    leaky = fit_likelihood(later,PARAMS,("noise",),None,pull=pull,max_evals=60,restarts=1)
    assert leaky["params"].noise != fit_a["params"].noise
    db.get(models.Match,"2027test_qm1").actual_time = None
    db.flush()
    assert len(load_results(db,2027,CUTOFF.timestamp())[0]) == 159


def test_state_fit_budget_failure_is_rejected_and_cutoff_is_idempotent(db_session,state_on):
    db = db_session
    seed(db,2026,n=30)
    seed(db,2027)
    state.install_bootstrap(db,artifact(),"test")
    out = state.fit_week(db,2027,CUTOFF,"test",max_evals=1)
    assert out["status"] == "rejected"
    assert "optimizer" in " ".join(out["errors"])
    row = db.get(models.RatingCalibration,out["id"])
    assert not row.fit_metrics["serving_eligible"]
    assert state.fit_week(db,2027,CUTOFF,"test")["id"] == row.id
    with pytest.raises(ValueError,match="state candidates"):
        calibrations.activate(db,row.id,now=CUTOFF+timedelta(days=1))
    with pytest.raises(ValueError,match="Monday"):
        state.fit_week(db,2027,CUTOFF+timedelta(days=1),"test")


def test_weekly_search_bounds_and_disabled_switch(db_session,monkeypatch):
    pull = state.WeeklyPull(artifact(),PARAMS)
    for name in state.NAMES:
        lo, hi = pull.bounds(name)
        assert np.exp(lo) >= getattr(PARAMS,name)/1.5-1e-12
        assert np.exp(hi) <= getattr(PARAMS,name)*1.5+1e-12
    monkeypatch.setattr(settings,"rating_state_fit_enabled",False)
    assert state.weekly_job(db_session,2027,now=CUTOFF,code_sha="test")["status"] == "disabled"


def test_replay_without_table_has_identical_beliefs(db_session):
    seed(db_session,2026,n=30)
    seed(db_session,2027)
    bootstrap = state.install_bootstrap(db_session,artifact(),"test")
    inputs, _, _ = inputs_for(db_session,2027,CUTOFF.timestamp(),bootstrap.params["context"])
    a, table, ha, ta = state.replay(inputs,PARAMS)
    b, empty, hb, tb = state.replay(inputs,PARAMS,record=False)
    assert len(table["y"]) == 160 and not empty and ha == hb and ta == tb
    for team in a.latest:
        assert a.belief_units(team) == b.belief_units(team)


def prepare_capture(db):
    seed(db,2026,n=30)
    seed(db,2027)
    bootstrap = state.install_bootstrap(db,artifact(),"test")
    version = models.RatingCalibration(season=2027,recipe=state.RECIPE,version=1,
        params={"schema":state.RECIPE,"bootstrap_id":bootstrap.id,"state":asdict(PARAMS),"layers":calibrations.identity()},
        fitted_through=CUTOFF,matches=160,code_sha="test",status="candidate",created_at=CUTOFF)
    db.add(version)
    db.flush()
    now = CUTOFF+timedelta(hours=9)
    key = "2027test_qm999"
    match = models.Match(match_key=key,event_key="2027test",comp_level="qm",set_number=1,match_number=999,time=int((now+timedelta(hours=1)).timestamp()))
    db.add(match)
    for color,keys in (("red",["frc1","frc2","frc3"]),("blue",["frc4","frc5","frc6"])):
        for team in keys:
            db.add(models.MatchTeam(match_key=key,event_key="2027test",alliance=color,team_key=team))
    results, _ = load_results(db,2027,now.timestamp())
    base = {"rm":150.,"bm":130.,"rv":400.,"bv":400.,"p_live":.55}
    log = models.MatchPredictionLog(match_key=key,event_key="2027test",season=2027,first_shown_at=now,
        first={"available":True,"red_win_prob":.55},final_at=now,final={"available":True,"red_win_prob":.55},
        features={"schema":calibrations.SCHEMA,"results_sha256":results_digest(results),"red_teams":["frc1","frc2","frc3"],
                  "blue_teams":["frc4","frc5","frc6"],"scheduled_start":match.time,"event_start":int(datetime(2027,2,1,tzinfo=timezone.utc).timestamp()),"row":base},bundle_version=0,backfilled=False)
    db.add(log)
    db.commit()
    return version, match, log, now


def test_prospective_forecasts_are_immutable_and_score_without_replay(state_db,state_on):
    db = state_db
    version, match, source, now = prepare_capture(db)
    result = forecasts.capture(db,2027,["2027test"],now=now)
    assert result["forecasts"] == 2
    assert forecasts.capture(db,2027,["2027test"],now=now+timedelta(minutes=1))["forecasts"] == 0
    row = db.get(models.RatingStatePrediction,(version.id,match.match_key))
    saved = digest(row.prediction)
    original_baseline = row.prediction["production"]["p_live"]
    source.features = {**source.features,"row":{**source.features["row"],"p_live":.99}}
    match.actual_time = int((now+timedelta(hours=1)).timestamp())
    for color,score,teams in (("red",180,["frc1","frc2","frc3"]),("blue",120,["frc4","frc5","frc6"])):
        db.add(models.MatchAllianceResult(match_key=match.match_key,event_key="2027test",season_year=2027,alliance=color,
            team_keys=teams,total_score=score,score_without_fouls=score))
    db.commit()
    report = forecasts.score(db,version,through=now+timedelta(hours=2))
    assert report["n"] == 1 and not report["serving_eligible"]
    assert report["comparison"]["brier"]["n"] == 1
    assert digest(row.prediction) == saved and row.prediction["production"]["p_live"] == original_baseline
    with pytest.raises(DatabaseError,match="immutable"):
        db.execute(text("UPDATE rating_state_predictions SET prediction='{}'"))
    db.rollback()
    with pytest.raises(DatabaseError,match="immutable"):
        db.execute(text("DELETE FROM rating_state_predictions"))
    db.rollback()
    match = db.get(models.Match,match.match_key)
    match.actual_time = int(now.timestamp())
    db.flush()
    assert forecasts.score(db,version,through=now+timedelta(hours=2))["n"] == 0


def test_stale_baseline_and_backfills_cannot_validate_state(db_session,state_on):
    db = db_session
    version, match, source, now = prepare_capture(db)
    original = source.features
    source.features = {**original,"results_sha256":"stale"}
    db.flush()
    assert forecasts.capture(db,2027,["2027test"],now=now)["forecasts"] == 0
    source.features = original
    source.backfilled = True
    db.flush()
    assert forecasts.capture(db,2027,["2027test"],now=now)["forecasts"] == 0
    source.backfilled = False
    source.features = {**original,"red_teams":["frc7","frc2","frc3"]}
    db.flush()
    assert forecasts.capture(db,2027,["2027test"],now=now)["forecasts"] == 0
    source.features = original
    match.actual_time = int((now-timedelta(seconds=1)).timestamp())
    db.flush()
    assert forecasts.capture(db,2027,["2027test"],now=now)["forecasts"] == 0


@pytest.mark.parametrize("change",["freeze","inputs"])
def test_fit_rechecks_freeze_and_input_snapshot_before_publication(db_session,state_on,monkeypatch,change):
    db = db_session
    seed(db,2026,n=30)
    seed(db,2027)
    state.install_bootstrap(db,artifact(),"test")
    original = state.fit_likelihood
    def during_fit(*args,**kwargs):
        result = original(*args,**kwargs)
        if change == "freeze":
            calibrations.ensure_bundle(db,2027).frozen = True
        else:
            db.query(models.MatchAllianceResult).filter_by(match_key="2027test_qm1",alliance="red").update({"score_without_fouls":999})
        db.commit()
        return result
    monkeypatch.setattr(state,"fit_likelihood",during_fit)
    result = state.fit_week(db,2027,CUTOFF,"test",max_evals=1)
    assert result["status"] == ("frozen_during_fit" if change == "freeze" else "inputs_changed_during_fit")
    assert not db.query(models.RatingCalibration).filter_by(recipe=state.RECIPE).count()


def test_bounded_layer_fits_respect_existing_hard_and_step_limits():
    from tests.test_rating_calibrations import live_table

    table = live_table()
    centre = calibrations.identity()
    params = state.fit_layers(table,centre)
    calibrations.validate_params(params,centre)
    assert not np.array_equal(params["score_coef"],centre["score_coef"])
    assert not np.array_equal(params["weights"],centre["weights"])


def test_bounded_logistic_matches_unconstrained_optimum_when_inside_bounds():
    from app.services.ratings.research_win import fit_correction, sigmoid

    rng = np.random.default_rng(42)
    x = rng.normal(size=(300,3))
    y = (rng.random(300)<sigmoid(x@np.array([.2,-.1,.3]))).astype(float)
    expected = fit_correction(x,y,np.zeros(300))
    actual = state.bounded_logistic(x,y,np.zeros(300),np.zeros(3),np.full(3,16),np.full(3,-10),np.full(3,10))
    np.testing.assert_allclose(actual,expected,atol=1e-6)
    lo,hi = np.full(3,-.01),np.full(3,.01)
    boxed = state.bounded_logistic(x,y,np.zeros(300),np.zeros(3),np.full(3,16),lo,hi)
    gradient = x.T@(sigmoid(x@boxed)-y)+16*boxed
    assert np.max(np.abs(boxed-np.clip(boxed-gradient,lo,hi))) < 1e-6


def test_state_optimizer_has_a_real_wall_time_budget(db_session,monkeypatch):
    from app.services.ratings import research_fit

    seed(db_session,2026,n=30)
    seed(db_session,2027)
    bootstrap = state.install_bootstrap(db_session,artifact(),"test")
    inputs, _, _ = inputs_for(db_session,2027,CUTOFF.timestamp(),bootstrap.params["context"])
    clock = iter([0.,3601.])
    monkeypatch.setattr(research_fit.time,"monotonic",lambda:next(clock))
    with pytest.raises(TimeoutError,match="wall-time"):
        fit_likelihood(inputs,PARAMS,("noise",),int(CUTOFF.timestamp()),max_seconds=3000)


def test_a_later_version_cannot_warm_start_an_earlier_cutoff(db_session):
    db = db_session
    seed(db,2026,n=30)
    seed(db,2027)
    bootstrap = state.install_bootstrap(db,artifact(),"test")
    db.add(models.RatingCalibration(season=2027,recipe=state.RECIPE,version=1,
        params={"state":asdict(PARAMS),"layers":calibrations.identity(),"bootstrap_id":bootstrap.id},
        fitted_through=CUTOFF+timedelta(days=7),matches=160,code_sha="test",status="candidate"))
    db.commit()
    with pytest.raises(ValueError,match="later fit"):
        state.fit_week(db,2027,CUTOFF,"test",max_evals=1)


def test_disqualified_observations_do_not_break_same_information_check(db_session):
    from app.services.ratings.replay import load_season

    db = db_session
    seed(db,2027)
    for key,colors in (("2027test_qm1",["red"]),("2027test_qm2",["red","blue"])):
        for color in colors:
            row = db.get(models.MatchAllianceResult,(key,color))
            row.total_score, row.score_without_fouls, row.dq_team_keys = 0, 0, row.team_keys[:1]
    db.flush()
    strict, skipped = load_results(db,2027,CUTOFF.timestamp())
    served_inputs = load_season(db,2027,evaluation_split=False).results
    assert results_digest(strict) == results_digest(served_inputs)
    assert len(strict) == 160 and skipped["no_eligible_observations"] == 1
    assert strict[0].red_points is None


def test_export_fingerprint_ignores_unselected_future_seasons(tmp_path,monkeypatch):
    import json
    import sys
    import build_rating_state_bootstrap as export
    from tests.test_research_win import _table

    reports, tables, output = tmp_path/"reports", tmp_path/"tables", tmp_path/"output"
    (reports/"runs").mkdir(parents=True)
    tables.mkdir()
    mle = asdict(PARAMS)
    mle["noise_count"] = 0.0
    fits = {str(year):{"mle":mle} for year in (2025,2026)}
    (reports/"hindsight.json").write_text(json.dumps(fits))
    (reports/"runs/e3_noise_count_zstatic-2026.json").write_text(json.dumps({"season":2026,"plan":[{"params":asdict(PARAMS)}]}))
    for year in (2025,2026):
        np.savez(tables/f"{year}.npz",**_table(weeks=2,per_week=100))
    monkeypatch.setattr(sys,"argv",["export","--season","2027","--reports",str(reports),"--win-tables",str(tables),"--out",str(output)])
    export.main()
    before = {p.name:p.read_bytes() for p in output.glob("*.json")}
    fits["2028"] = {"mle":{**mle,"prior":999}}
    (reports/"hindsight.json").write_text(json.dumps(fits))
    (reports/"runs/e3_noise_count_zstatic-2028.json").write_text("unreadable future data must not be opened")
    export.main()
    assert before == {p.name:p.read_bytes() for p in output.glob("*.json")}
