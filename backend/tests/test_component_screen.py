import copy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json

import numpy as np
import pytest

from app.services.ratings.component_filter import ComponentFilter
from app.services.ratings.component_spec import Component, ComponentSpec
from app.services.ratings import component_screen as screen
from app.services.ratings.replay import MatchCard, MatchResult
from app.services.ratings.research_filter import ScaleFreeFilter, ScaleFreeParams
from app.services.ratings.research_rehearsal import write_settings
from scripts.evaluate_component_tiers import evaluate, protocol


def result(i):
    teams = [f"frc{t+1}" for t in range(9)]
    card = MatchCard(f"2024test_qm{i+1}","event1" if i < 8 else "event2",100+i,"qm",100,
                     tuple(teams[i%3:i%3+3]),tuple(teams[5:8]))
    red,blue = 50.+i*5,100.-i
    return MatchResult(card,int(red),int(blue),red,blue,red_components={"total_points":red},blue_components={"total_points":blue})


@pytest.mark.parametrize("noise_count",[0.,.2])
def test_one_component_matches_existing_total_filter_including_event_transfer(noise_count):
    params = ScaleFreeParams(margin_cov=False,noise_count=noise_count)
    args = {"z_previous":{"frc1":1.},"rookies":{"frc6"},"previous_unit":20.}
    model = ComponentFilter(screen.specs(2024)["A"],params,**args)
    oracle = ScaleFreeFilter(params,**args)
    for i in range(16):
        r = result(i)
        old = copy.deepcopy(oracle).predict(r.card)
        assert asdict(model.predict(r.card)) == pytest.approx(asdict(old))
        model.observe(r,{"red":{"total":r.red_points},"blue":{"total":r.blue_points}})
        oracle.observe(r)


def test_constant_noise_alliance_partition_closes_to_total_with_signed_residual():
    params = ScaleFreeParams(margin_cov=False)
    split = ComponentFilter(screen.specs(2024)["B"],params)
    total = ComponentFilter(screen.specs(2024)["A"],params)
    for i in range(16):
        r = result(i)
        assert asdict(split.predict(r.card)) == pytest.approx(asdict(total.predict(r.card)))
        parts = {c:{"part1":y*.3,"part2":y*.7+2,"other":-2.} for c,y in (("red",r.red_points),("blue",r.blue_points))}
        split.observe(r,parts)
        total.observe(r,{"red":{"total":r.red_points},"blue":{"total":r.blue_points}})
    assert split.fractions()["other"] < 0
    assert sum(split.fractions().values()) == pytest.approx(1.)


def test_upcoming_forecasts_are_read_only_and_unit_scaling_preserves_win_signal():
    params = ScaleFreeParams(margin_cov=False,noise_count=.2)
    a = ComponentFilter(screen.specs(2024)["B"],params,previous_unit=20.)
    b = ComponentFilter(screen.specs(2024)["B"],params,previous_unit=200.)
    for i in range(12):
        r = result(i)
        scaled = replace(r,red_points=r.red_points*10,blue_points=r.blue_points*10)
        pa,pb = a.predict(r.card),b.predict(r.card)
        assert pb.red_mean == pytest.approx(pa.red_mean*10)
        assert pb.red_var == pytest.approx(pa.red_var*100)
        a.observe(r,{c:{"part1":v*.4,"part2":v*.6,"other":0.} for c,v in (("red",r.red_points),("blue",r.blue_points))})
        b.observe(scaled,{c:{"part1":v*4,"part2":v*6,"other":0.} for c,v in (("red",r.red_points),("blue",r.blue_points))})
    before = copy.deepcopy(a)
    r = result(20)
    for _ in range(3):
        a.predict(replace(r.card,event_key="future"))
    assert a.latest == before.latest and a.population == before.population
    for name in a.names:
        for key,block in a.blocks[name].items():
            np.testing.assert_array_equal(block.mean,before.blocks[name][key].mean)
            np.testing.assert_array_equal(block.cov,before.blocks[name][key].cov)


def cache(tmp_path,year=2024):
    folder = tmp_path/str(year)
    folder.mkdir(exist_ok=True)
    start = int(datetime(year,3,1,tzinfo=timezone.utc).timestamp())
    matches = []
    for i in range(8):
        time = start+i*7*86400
        matches.append({"key":f"{year}test_qm{i+1}","comp_level":"qm","actual_time":time,"post_result_time":time+10,
            "alliances":{c:{"score":100+i,"team_keys":[f"frc{j}" for j in teams]} for c,teams in (("red",(1,2,3)),("blue",(4,5,6)))},
            "score_breakdown":{c:{"autoPoints":20,"teleopPoints":80+i,"teleopTotalNotePoints":70+i,
                                 "endGameTotalStagePoints":10,"foulPoints":0,"adjustPoints":0} for c in ("red","blue")}})
    raw = {"event":{"key":f"{year}test","event_type":0},"matches":matches,"teams":[]}
    path = folder/f"{year}test.json"
    path.write_text(json.dumps(raw))
    return path,raw


def test_unknown_flags_missing_fields_dq_and_actual_times_are_not_zero_imputed(tmp_path):
    path,raw = cache(tmp_path)
    raw["matches"][0]["actual_time"] = None
    raw["matches"][0]["time"] = 100
    raw["matches"][1]["score_breakdown"]["red"]["autoPoints"] = True
    raw["matches"][2]["alliances"]["blue"]["dq_team_keys"] = ["frc4"]
    path.write_text(json.dumps(raw))
    data = screen.load(tmp_path,2024)
    assert data.skipped["missing_actual_time"] == 1
    assert all(v is None for v in data.records[0]["projections"]["B"]["red"].values())
    assert data.records[0]["result"].red_points == 101.
    assert data.records[1]["result"].blue_points is None
    assert not data.records[1]["closure"]["C"]["blue"]


def test_publication_cutoffs_and_future_result_mutations_cannot_change_past(tmp_path,monkeypatch):
    monkeypatch.setitem(screen.POLICY,"bootstrap_draws",20)
    path,raw = cache(tmp_path)
    raw["matches"][0]["post_result_time"] = raw["matches"][2]["actual_time"]
    path.write_text(json.dumps(raw))
    data = screen.load(tmp_path,2024)
    params = ScaleFreeParams(margin_cov=False,noise_count=.2)
    before = screen.evaluate(data,params)
    raw["matches"][-1]["score_breakdown"]["red"]["autoPoints"] += 50
    raw["matches"][-1]["alliances"]["red"]["score"] += 50
    path.write_text(json.dumps(raw))
    after = screen.evaluate(screen.load(tmp_path,2024),params)
    assert before["forecast_sha256"] == after["forecast_sha256"]
    assert before["weekly_checks"][:-1] == after["weekly_checks"][:-1]
    assert all(v is None for v in before["first_eligible_cutoff"].values())


def test_protocol_drift_is_blocked_before_cache_read(tmp_path):
    frozen = write_settings(tmp_path,protocol())
    raw = json.loads(frozen.read_text())
    raw["policy"]["minimum_matches"] = 1
    wrong = write_settings(tmp_path,raw)
    with pytest.raises(ValueError,match="protocol"):
        evaluate(tmp_path/"missing",tmp_path/"missing",2026,wrong)
    with pytest.raises(ValueError,match="examined"):
        evaluate(tmp_path/"missing",tmp_path/"missing",2027,frozen)


def test_missing_or_overlapping_component_payloads_fail_closure(tmp_path):
    path,raw = cache(tmp_path)
    raw["matches"][0]["score_breakdown"]["red"]["teleopPoints"] += 10
    raw["matches"][1]["score_breakdown"]["red"].pop("adjustPoints")
    path.write_text(json.dumps(raw))
    data = screen.load(tmp_path,2024)
    assert not data.records[0]["closure"]["B"]["red"]
    assert data.records[0]["projections"]["B"]["red"]["other"] == -10.
    assert not data.records[1]["closure"]["B"]["red"]


def test_unsupported_component_filter_params_fail_closed():
    with pytest.raises(ValueError,match="supports counting"):
        ComponentFilter(screen.specs(2024)["B"],ScaleFreeParams())


def test_numeric_robot_observations_update_the_named_robots():
    spec = ComponentSpec(2024,"C",1,(Component("endgame",("r1","r2","r3"),level="robot"),))
    model = ComponentFilter(spec,ScaleFreeParams(margin_cov=False))
    r = result(0)
    model.observe(r,{"red":{"endgame":[20.,0.,0.],"other":30.},"blue":{"endgame":[0.,0.,0.],"other":100.}})
    block = model.blocks["endgame"][r.card.event_key]
    assert block.mean[block.index[r.card.red[0]]] > block.mean[block.index[r.card.red[1]]]
