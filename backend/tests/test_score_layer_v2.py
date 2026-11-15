from dataclasses import asdict
from functools import partial

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings import calibrations as layers, gradual_rehearsal as g, state_fitting as state, weekly_rehearsal as w
from app.services.ratings.replay import MatchCard
from app.services.ratings.state_bootstrap import build_bootstrap
from app.services.ratings.score_positivity import censored_mean
from tests.test_age_score_rehearsal import table
from tests.test_weekly_rehearsal import PARAMS, data


def v2_table(new_r, new_b):
    t = table()
    n = len(t["y"])
    t["min_here"][:] = 0
    t["new_r"],t["new_b"] = np.resize(np.asarray(new_r,float),n),np.resize(np.asarray(new_b,float),n)
    return t


def test_v2_schema_bounds_steps_and_no_schema_switching():
    base = layers.identity(v2=True)
    layers.validate_params(base)
    layers.validate_params({**base,"debut_intercept":-1.2})
    for bad,match in (({"debut_intercept":-1.3},"hard bounds"),({"debut_intercept":True},"hard bounds"),({"positive":1},"boolean")):
        with pytest.raises(ValueError,match=match):
            layers.validate_params({**base,**bad})
    with pytest.raises(ValueError,match="weekly step"):
        layers.validate_params({**base,"debut_intercept":-.2},base)
    with pytest.raises(ValueError,match="positive changed"):
        layers.validate_params({**base,"positive":False},base)
    with pytest.raises(ValueError,match="cannot change"):
        layers.validate_params(base,layers.identity())
    with pytest.raises(ValueError,match="unsupported"):
        layers.validate_params({**layers.identity(),"debut_intercept":0.})


def test_apply_interpolates_first_match_intercepts_by_debut_share_and_keeps_wins():
    t = v2_table([0,1,2,3],[3,0,1,2])
    t["playoff"][-1] = 1
    t["new_r"][-1] = 2  # a playoff backup debut is ignored
    v1 = {**layers.identity(),"score_coef":[-.1]+[0.]*10}
    v2 = {**layers.identity(v2=True,positive=False),"score_coef":[-.1]+[0.]*10,"debut_intercept":-.7}
    r1,b1,p1 = layers.apply(t,v1)
    r2,b2,p2 = layers.apply(t,v2)
    np.testing.assert_array_equal(p1,p2)
    first = (t["playoff"] == 0) & (t["min_here"] <= 0)
    np.testing.assert_allclose(r2-r1,np.where(first,t["u"]*t["new_r"]/3*(-.6),0),atol=1e-12)
    np.testing.assert_allclose(b2-b1,np.where(first,t["u"]*t["new_b"]/3*(-.6),0),atol=1e-12)
    positive = {**v2,"positive":True}
    rp,bp,pp = layers.apply(t,positive)
    np.testing.assert_array_equal(pp,p2)
    assert np.all(rp > 0) and np.all(rp >= r2-1e-9)
    assert rp[0] == pytest.approx(censored_mean(r2[0],t["rv"][0]))
    t["new_b"][0] = 4
    with pytest.raises(ValueError,match="between 0 and 3"):
        layers.apply(t,v2)


def test_feature_counts_season_new_teams_from_the_counts_it_is_given():
    class Block:
        index = {f"frc{i}":i for i in range(6)}
        mean,cov = np.ones(6),np.eye(6)
    class Model:
        blocks = {"e":Block()}
        class unit:
            @staticmethod
            def value():
                return 30.
    class P:
        red_mean,blue_mean,red_var,blue_var = 3.,3.,1.,1.
    card = MatchCard("e_qm1","e",100,"qm",100,("frc0","frc1","frc2"),("frc3","frc4","frc5"))
    row = state.feature(Model(),card,P(),{},{"frc0":2,"frc4":1,"frc5":3},100)
    assert (row["new_r"],row["new_b"]) == (2.,1.)


def test_v2_fit_separates_debuts_within_bounds_and_refuses_mixed_schemas():
    n = len(table()["y"])
    t = v2_table(np.where(np.arange(n) % 2,3,0),np.where(np.arange(n) % 2,3,0))
    for key,mean,new in (("ra","rm","new_r"),("ba","bm","new_b")):
        t[key] = t[mean]+np.where(t[new]==3,-.5,.2)*t["u"]
    centre = layers.identity(v2=True)
    fitted = state.fit_layers(t,centre,prior=centre)
    layers.validate_params(fitted,centre)
    assert fitted["schema"] == layers.SCHEMA_V2 and fitted["positive"] is True
    np.testing.assert_allclose([fitted["score_coef"][0],fitted["debut_intercept"]],[.15,-.15],atol=1e-9)
    loose = state.bounded_score_fit(t,{**centre,"score_coef":[.15]+[0.]*10,"debut_intercept":-.45},prior=centre)
    assert loose[11] < loose[0]
    with pytest.raises(ValueError,match="different schemas"):
        state.bounded_score_fit(t,centre,prior=layers.identity())


def test_earlier_season_prior_needs_every_lineup_and_has_twelve_terms():
    t = table()
    cards = {str(k):MatchCard(str(k),"e",int(s),"qm",int(s),("frc1","frc2","frc3"),("frc4","frc5","frc6")) for k,s in zip(t["key"],t["time"])}
    prior = w.debut_prior(t,cards)
    assert prior.shape == (12,) and np.isfinite(prior).all()
    with pytest.raises(ValueError,match="lineups are missing"):
        w.debut_prior(t,{})
    with pytest.raises(ValueError,match="lineups"):
        w.make_bootstrap(2024,"r","t",score_layer="debut-positive")


def test_rehearsal_with_v2_layers_and_early_fast_state_serves_positive_scores(tmp_path,monkeypatch):
    monkeypatch.setitem(w.POLICY,"compiled",False)
    def fit(inputs,start,names,cutoff,**kwargs):
        return {"params":start.__class__(**{**asdict(start),"noise":start.noise*.98}),"converged":True,"evals":1,"runs":1}
    monkeypatch.setattr(g,"fit_likelihood",fit)
    artifact = build_bootstrap(2024,{"2023":{"mle":asdict(PARAMS)}},[{"season":2023,"plan":[{"params":asdict(PARAMS)}]}],
                               {"fixture":"a"*64},layer_params=layers.identity(v2=True))
    run = w.rehearse(tmp_path/"r",data(2024,4),data(2023,1),artifact,"p"*64,"test",
                     fit_callback=partial(g.fit_job,calibration_prior="bootstrap",schedule=g.EARLY_FAST))
    assert run["status"] == "complete"
    for frame in run["frames"]:
        p = frame["forecasts"]["active"]
        assert {"new_r","new_b"} <= set(p["raw"]) and p["rm"] > 0 and p["bm"] > 0
    applied = [j["fit"]["id"] for j in run["jobs"] if j.get("applied")]
    assert applied
    engine = w.prepare_database(tmp_path/"r"/"scratch.sqlite")
    with Session(engine) as db:
        for identifier in applied:
            fitted = db.get(models.RatingCalibration,identifier).params["layers"]
            assert fitted["schema"] == layers.SCHEMA_V2 and fitted["positive"] is True
    engine.dispose()
