from dataclasses import replace
from functools import partial
import math

import pytest
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings import gradual_rehearsal as g, state_fitting as state, weekly_rehearsal as w
from tests.test_weekly_rehearsal import PARAMS, artifact, data

DAY = 86400


def test_default_schedule_is_the_previous_rule_and_early_fast_is_frozen():
    first = 1_000_000
    assert g.schedule_limits(None,first+27*DAY,first) == (math.log(1.5),None,True)
    assert g.schedule_limits(None,first+28*DAY,first) == (math.log(1.5),None,False)
    assert g.schedule_limits(g.EARLY_FAST,first+27*DAY,first) == (math.log(3.),[3.,15.],True)
    assert g.schedule_limits(g.EARLY_FAST,first+28*DAY,first) == (math.log(1.5),None,True)


def test_weekly_pull_step_widens_only_the_weekly_bound():
    a = artifact()
    narrow,wide = state.WeeklyPull(a,PARAMS),state.WeeklyPull(a,PARAMS,math.log(3.))
    for name in state.NAMES:
        lo,hi = narrow.bounds(name)
        assert hi-lo <= 2*math.log(1.5)+1e-12
        wlo,whi = wide.bounds(name)
        assert wlo <= lo and whi >= hi and whi-wlo <= 2*math.log(3.)+1e-12


def test_rating_impact_applies_the_limits_it_is_given():
    class Model:
        def __init__(self, scale):
            self.scale,self.latest = scale,{f"frc{i}":"e" for i in range(40)}
        def team_points(self, team, event):
            return self.scale*(10+int(team[3:]))
    played = {f"frc{i}":10 for i in range(40)}
    default,_ = w.rating_impact(Model(1.),Model(1.3),played,None)
    loose,_ = w.rating_impact(Model(1.),Model(1.3),played,None,[1e9,1e9])
    assert default["limits"] == [w.POLICY["median_rating_step"],w.POLICY["maximum_rating_step"]] and loose["limits"] == [1e9,1e9]
    assert default["median"] == loose["median"] and loose["passed"]
    assert default["passed"] == (default["median"] < 1. and default["max"] <= 5.)


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setitem(w.POLICY,"compiled",False)
    def fit(inputs,start,names,cutoff,**kwargs):
        calls.append({"cutoff":cutoff,"first":inputs.times[0],"names":tuple(names),"pull":kwargs["pull"]})
        return {"params":replace(start,noise=start.noise*.98,beta=start.beta*1.01),"converged":True,"evals":1,"runs":1}
    monkeypatch.setattr(g,"fit_likelihood",fit)
    return calls


def test_schedule_records_its_limits_and_publication_uses_them(tmp_path,recorded):
    current = data(2024,6)
    run = w.rehearse(tmp_path/"r",current,data(2023,1),artifact(),"p"*64,"test",
        fit_callback=partial(g.fit_job,calibration_prior="bootstrap",schedule=g.EARLY_FAST))
    assert recorded and any(c["cutoff"] >= c["first"]+28*DAY for c in recorded)
    for call in recorded:
        early = call["cutoff"] < call["first"]+28*DAY
        assert {"beta","rookie"} <= set(call["names"])
        assert call["pull"].step == (math.log(3.) if early else math.log(1.5))
    engine = w.prepare_database(tmp_path/"r"/"scratch.sqlite")
    with Session(engine) as db:
        rows = {r.id:r for r in db.query(models.RatingCalibration).filter_by(recipe=g.EARLY_FAST["recipe"])}
    engine.dispose()
    assert rows and all(r.params["schedule"] == g.EARLY_FAST for r in rows.values())
    published = [j for j in run["jobs"] if j.get("fit",{}).get("id") in rows and j["numerical_status"] == "candidate"]
    assert published
    for job in published:
        limits = rows[job["fit"]["id"]].params["impact_limits"]
        assert job["impact"]["limits"] == (limits or [w.POLICY["median_rating_step"],w.POLICY["maximum_rating_step"]])


def test_schedule_requires_the_fixed_calibration_prior(tmp_path,recorded):
    with pytest.raises(ValueError,match="fixed calibration prior"):
        w.rehearse(tmp_path/"r",data(2024),data(2023,1),artifact(),"p"*64,"test",
                   fit_callback=partial(g.fit_job,calibration_prior="previous",schedule=g.EARLY_FAST))
