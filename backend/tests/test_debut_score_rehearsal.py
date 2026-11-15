import copy
from datetime import datetime, timezone

import numpy as np
import pytest

from app.services.ratings import age_score_rehearsal as age, calibrations as layers, debut_score_rehearsal as debut, weekly_rehearsal as w
from app.services.ratings.replay import MatchCard
from app.services.ratings.research_win import side_arrays
from tests.test_age_score_rehearsal import source_run, table  # noqa: F401
from tests.test_probability_rehearsal import fixtures


def card(key, time, red, blue):
    return MatchCard(key,key.split("_")[0],time,"qm",time,tuple(red),tuple(blue))


def debut_table(new_r, new_b):
    t = table()
    n = len(t["y"])
    t["min_here"][:] = 0
    t["new_r"],t["new_b"] = np.resize(np.asarray(new_r,float),n),np.resize(np.asarray(new_b,float),n)
    return t


def test_debut_share_interpolates_inside_effective_bounds_and_ignores_other_contexts():
    t = debut_table([0,1,2,3],[3,0,1,2])
    t["playoff"][-1] = 1
    t["new_r"][-1] = 1  # a playoff backup's debut is allowed and ignored
    sides = side_arrays(t)
    x = debut.design(t,sides)
    assert np.allclose(x.sum(axis=1),sides["ctx"]==0) and x[sides["ctx"]==3].sum()==0
    new = np.where(sides["red"],t["new_r"][sides["row"]],t["new_b"][sides["row"]])
    first = sides["ctx"]==0
    np.testing.assert_allclose(x[first,1],new[first]/3)
    old,fitted = np.array([-.2,-.9]),np.array([-.05,-1.05])
    debut.validate(fitted,old)
    assert np.max(np.abs(x@(fitted-old))) <= layers.SCORE_STEP+1e-12
    assert np.all(np.abs(x@fitted) <= debut.POLICY["intercept_limit"])
    with pytest.raises(ValueError,match="weekly"):
        debut.validate([-.04,-1.05],old)
    t["min_here"][0] = 1
    t["new_r"][0] = 2
    with pytest.raises(ValueError,match="outside someone's first match"):
        debut.design(t,side_arrays(t))
    t["min_here"][0] = 0
    t["new_r"][0] = 4
    with pytest.raises(ValueError,match="between 0 and 3"):
        debut.design(t,side_arrays(t))


def test_season_new_counts_follow_replay_order_and_posting_time():
    start = int(datetime(2024,3,3,tzinfo=timezone.utc).timestamp())
    cards = [card("2024a_qm1",start,["frc1","frc2","frc3"],["frc4","frc5","frc6"]),
             card("2024a_qm2",start+600,["frc1","frc7","frc8"],["frc4","frc5","frc9"]),
             card("2024b_qm1",start+86400,["frc1","frc2","frc7"],["frc10","frc5","frc6"])]
    red,blue = debut.season_new(cards)
    assert red.tolist()==[3,2,0] and blue.tolist()==[3,1,1]
    with pytest.raises(ValueError,match="lineups differ"):
        debut.with_season_new({"key":np.array(["2024a_qm2","2024a_qm1","2024b_qm1"])},cards)

    class Data:
        records = [{"result":type("R",(),{"card":c})(),"posted":c.time+60} for c in cards]
    Data.records[0]["posted"] = start+90000   # posted after the third match started
    frames = [{"key":c.match_key,"time":c.time} for c in cards]
    assert debut.prospective_new(frames,Data) == {"2024a_qm1":(3,3),"2024a_qm2":(3,3),"2024b_qm1":(1,2)}


def test_two_endpoint_fit_separates_debuts_from_experienced_teams_and_keeps_prior():
    n = len(table()["y"])
    t = debut_table(np.where(np.arange(n) % 2,3,0),np.where(np.arange(n) % 2,3,0))
    for key,mean,new in (("ra","rm","new_r"),("ba","bm","new_b")):
        t[key] = t[mean]+np.where(t[new]==3,-.5,.2)*t["u"]
    nuisance,prior = layers.identity(),[0.,0.]
    unrestricted = debut.fit(t,nuisance,prior)
    assert unrestricted[0]>.1 and unrestricted[1]<-.35
    np.testing.assert_allclose(debut.fit(t,nuisance,prior,[0.,0.]),[.15,-.15],atol=1e-9)
    np.testing.assert_allclose(debut.fit(t,nuisance,prior,unrestricted),unrestricted,atol=1e-8)
    assert prior==[0.,0.] and nuisance==layers.identity()


def test_prediction_preserves_wins_raw_nonfirst_and_uses_side_debut_shares():
    frames,_ = fixtures()
    for frame in frames:
        frame["forecasts"]["active"] = copy.deepcopy(frame["forecasts"]["bootstrap"])
    original = copy.deepcopy(frames)
    new = {f["key"]:((3,0) if f["first_here"] else (0,0)) for f in frames}
    payload = {"endpoints":[.1,-.5]}
    projected = debut.predict(frames,{1:payload},{1:{"artifact":{"layers":layers.identity()}}},new)
    for f,before in zip(projected,original):
        p,base = f["forecasts"][debut.VARIANT],before["forecasts"]["bootstrap"]
        assert p["p"]==base["p"] and p["raw"]==base["raw"] and p["params_sha256"]==w.digest(payload)
        u = base["raw"]["u"]
        if base["raw"]["min_here"]>0:
            assert (p["rm"],p["bm"])==(base["rm"],base["bm"])
        else:
            assert p["rm"]-base["rm"]==pytest.approx(-.5*u) and p["bm"]-base["bm"]==pytest.approx(.1*u)
            assert p["season_new"]==[3,0]
    assert frames==original


def test_bootstrap_uses_only_earlier_season_tables_with_their_lineups(tmp_path):
    t = table()
    lineups = {}
    for year in (2023,2024):
        earlier = {k:v.copy() for k,v in t.items()}
        earlier["key"] = np.array([str(k).replace("2024",str(year)) for k in t["key"]])
        earlier["time"] += int(datetime(year,3,3,tzinfo=timezone.utc).timestamp()-datetime(2024,3,3,tzinfo=timezone.utc).timestamp())
        earlier["min_here"][:] = 0
        np.savez(tmp_path/f"{year}.npz",**earlier)
        lineups[year] = {str(k):card(str(k),int(s),[f"frc{i}" for i in range(3)],[f"frc{i}" for i in range(3,6)])
                         for k,s in zip(earlier["key"],earlier["time"])}
    (tmp_path/"2025.npz").write_text("future table must never be opened")
    asked = []
    prior = debut.bootstrap(2025,tmp_path,lambda y:asked.append(y) or lineups[y])
    assert asked==[2023,2024] and set(prior["source_tables"])=={"2023","2024"} and not prior["provisional"]
    assert debut.bootstrap(2023,tmp_path,lineups.get)=={"endpoints":[0.,0.],"source_tables":{},"provisional":True}
    with pytest.raises(ValueError,match="examined"):
        debut.bootstrap(2027,tmp_path,lineups.get)
    with pytest.raises(ValueError,match="lineups are missing"):
        debut.bootstrap(2024,tmp_path,lambda y:{})
    np.savez(tmp_path/"2023.npz",**t)
    with pytest.raises(ValueError,match="another season"):
        debut.bootstrap(2024,tmp_path,lineups.get)


def test_real_replay_resume_controls_and_source_integrity(tmp_path,source_run,monkeypatch):  # noqa: F811
    root,current,_ = source_run
    before = age.hashes(root)
    prior = {"endpoints":[0.,0.],"source_tables":{},"provisional":True}
    output = tmp_path/"output"
    result = debut.rehearse(output,root,current,prior,"p"*64,"test")
    assert result["history_preserved"] and before==age.hashes(root)
    assert len(result["fits"])>0 and all(f["fit"]["debut_share_sum"]>0 for f in result["fits"])
    expected = debut.prospective_new(result["frames"],current)
    assert all(tuple(f["forecasts"][debut.VARIANT]["season_new"])==expected[f["key"]] for f in result["frames"])
    summary = debut.summarize(result,current)
    assert summary["checks"]["wins_unchanged"] and summary["checks"]["nonfirst_scores_unchanged"]
    assert summary["checks"]["no_new_negative_scores"] and not summary["release_passed"]
    assert {"first_sides_all_new_early14","first_sides_experienced_after14"} <= set(summary["metrics"])
    hashes = age.hashes(output)
    monkeypatch.setattr(debut,"fit",lambda *a:pytest.fail("completed resume refitted"))
    again = debut.rehearse(output,root,current,prior,"p"*64,"equivalent-merge-commit")
    assert again==result and hashes==age.hashes(output)
    changed = copy.deepcopy(prior)
    changed["endpoints"][1] = -.1
    with pytest.raises(ValueError,match="source, prior or protocol changed"):
        debut.rehearse(output,root,current,changed,"p"*64,"test")


def test_new_negative_scores_fail_the_study_and_overlap_is_rejected(tmp_path,source_run):  # noqa: F811
    root,current,_ = source_run
    prior = {"endpoints":[0.,0.],"source_tables":{},"provisional":True}
    result = debut.rehearse(tmp_path/"output",root,current,prior,"p"*64,"test")
    first = next(f for f in result["frames"] if f["forecasts"][debut.VARIANT]["season_new"][0]>0)
    first["forecasts"][debut.VARIANT]["rm"] = -1.
    summary = debut.summarize(result,current)
    assert not summary["checks"]["no_new_negative_scores"] and not summary["study_passed"]
    assert summary["negative_scores"]["introduced"]==[[first["key"],"red"]]
    for output in (root,root/"nested",root.parent):
        with pytest.raises(ValueError,match="overlap"):
            debut.rehearse(output,root,current,prior,"p"*64,"test")
