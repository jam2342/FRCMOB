import copy
from dataclasses import replace
from functools import partial
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings import state_fitting as state, calibrations as layers, gradual_rehearsal as g, weekly_rehearsal as w
from tests.test_probability_rehearsal import fixtures
from tests import test_gradual_rehearsal as shared
from tests.test_weekly_rehearsal import data, artifact


def table():
    frames,source = fixtures()
    from app.services.ratings.probability_rehearsal import training_table
    return training_table(frames,source,frames[-1]["time"]+120)


@pytest.fixture
def goals(monkeypatch):
    return shared.goals.__wrapped__(monkeypatch)


def test_logistic_start_does_not_replace_prior_or_recount_same_data():
    x = np.ones((300,1))
    y = np.r_[np.ones(210),np.zeros(90)]
    args = (x,y,np.zeros(300),np.zeros(1),np.array([16.]),np.array([-2.]),np.array([2.]))
    a = state.bounded_logistic(*args,initial=np.array([-1.7]))
    b = state.bounded_logistic(*args,initial=np.array([1.7]))
    np.testing.assert_allclose(a,b,atol=1e-8)
    assert abs(300*(1/(1+np.exp(-a[0]))-.7)+16*a[0]) < 1e-6
    repeated = state.bounded_logistic(*args,initial=a)
    np.testing.assert_allclose(a,repeated,atol=1e-8)
    rolling = state.bounded_logistic(x,y,np.zeros(300),a,np.array([16.]),np.array([-2.]),np.array([2.]))
    assert rolling[0] > a[0]+.05


def test_default_layers_match_explicit_identical_prior():
    initial = layers.identity()
    legacy = state.fit_layers(table(),initial)
    explicit = state.fit_layers(table(),initial,prior=copy.deepcopy(initial))
    for name in ("score_coef","temperatures","weights","pooled_temperature"):
        np.testing.assert_allclose(legacy[name],explicit[name],atol=1e-7)
    assert legacy["blend"] == explicit["blend"] and initial == layers.identity()


def test_bounds_follow_active_version_when_prior_is_farther_away():
    active = layers.identity()
    active.update(score_coef=[.6]*4+[0.]*7,weights=[.6]*12,temperatures=[2.]*4,pooled_temperature=2.)
    prior = layers.identity()
    fitted = state.fit_layers(table(),active,prior=prior)
    layers.validate_params(fitted,active)
    assert np.min(fitted["score_coef"][:4]) >= .45-1e-12
    assert np.min(fitted["weights"]) >= .35-1e-12
    assert min(fitted["temperatures"]) >= 2/1.5-1e-12
    assert prior == layers.identity()


def test_fixed_prior_survives_active_updates_rollback_and_resume(tmp_path,goals,monkeypatch):
    priors = []
    original = state.fit_layers
    def capture(table,centre,*,prior=None):
        priors.append((copy.deepcopy(centre),copy.deepcopy(prior)))
        return original(table,centre,prior=prior)
    monkeypatch.setattr(state,"fit_layers",capture)
    calls = []
    def safety(frames,source,active,now):
        calls.append(active)
        return {"rollback":len(calls)==4,"comparison":{}}
    monkeypatch.setattr(w,"safety",safety)
    callback = partial(g.fit_job,calibration_prior="bootstrap")
    def run():
        return w.rehearse(tmp_path,data(2024,5),data(2023,1),artifact(),"p"*64,"test",fit_callback=callback)
    result = run()
    initial = artifact()["layers"]
    assert len(priors) >= 3 and all(prior == initial for _,prior in priors)
    assert any(centre != prior for centre,prior in priors)
    assert result["jobs"][3]["active_after"] == result["bootstrap_id"]
    assert result["jobs"][4]["parent_active"] == result["bootstrap_id"]
    engine = w.prepare_database(tmp_path/"scratch.sqlite")
    with Session(engine) as db:
        rows = db.query(models.RatingCalibration).filter_by(recipe=g.RECIPES["bootstrap"]).all()
        assert len(rows) == len(priors)
        for row in rows:
            assert row.params["calibration_prior_sha256"] == w.digest(initial)
            assert row.params["calibration_prior"] == "bootstrap"
            parent = db.get(models.RatingCalibration,row.params["parent_active"])
            _,previous,_ = w.version(db,parent.id)
            layers.validate_params(row.params["layers"],previous)
        assert state.layers.active_bundle(db,2024).calibration_id is None
        assert state.latest(db,2024,g.RECIPE) is None
    engine.dispose()
    count = len(goals)
    hashes = w.journal_hashes(tmp_path,w.read_object(tmp_path/"checkpoints",result["checkpoint"])["journals"])
    again = run()
    assert result["frames"] == again["frames"] and len(goals) == count
    assert hashes == w.journal_hashes(tmp_path,list(hashes))


def test_calibration_prior_is_part_of_frozen_protocol(monkeypatch):
    path = Path(__file__).resolve().parents[1]/"scripts/rehearse_rating_weekly.py"
    spec = importlib.util.spec_from_file_location("fixed_prior_protocol",path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,"version",lambda name:"test")
    previous = module.protocol("gradual","previous")
    fixed = module.protocol("gradual","bootstrap")
    assert w.digest(previous) != w.digest(fixed)
    assert fixed["calibration_prior"] == "bootstrap"
    with pytest.raises(ValueError,match="gradual"):
        module.protocol("direct","bootstrap")


def test_first_score_bias_uses_only_known_first_qualification_sides():
    frames,source = fixtures()
    chosen = copy.deepcopy(frames[:4])
    for frame in chosen:
        frame.update(first_here=True,playoff=False)
        frame["forecasts"]["active"] = {"rm":100.,"bm":100.,"rv":100.,"bv":100.}
        r = source[frame["key"]]["result"]
        source[frame["key"]]["result"] = replace(r,red_points=110,blue_points=110)
    chosen[1]["playoff"] = True
    chosen[2]["first_here"] = False
    r = source[chosen[3]["key"]]["result"]
    source[chosen[3]["key"]]["result"] = replace(r,red_points=None,blue_points=130)
    assert g.first_score_bias(chosen,source) == {"sides":3,"mean_bias_pct":100*50/300}
    assert g.first_score_bias([],source) == {"sides":0,"mean_bias_pct":None}
