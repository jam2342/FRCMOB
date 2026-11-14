import copy
from datetime import datetime, timezone
import json

import numpy as np
import pytest

from app.services.ratings import calibrations as layers, probability_rehearsal as p, state_fitting as state, weekly_rehearsal as w
from app.services.ratings.replay import MatchCard, MatchResult
from app.services.ratings.research_rehearsal import write_settings


def fixtures():
    start = int(datetime(2024,3,3,tzinfo=timezone.utc).timestamp())
    frames,source = [],{}
    for week in range(3):
        for i in range(160):
            key = f"2024e{week}_qm{i+1}"
            event = f"2024e{week}"
            at = start+week*7*86400+120*i
            raw = dict(rm=110.,bm=100.,rv=400.,bv=400.,mv=800.,u=35.,best_r=1.2,best_b=1.1,
                       worst_r=.8,worst_b=.7,sd_r=.2,sd_b=.2,min_here=i,min_before=week*10,
                       season_frac=week/17,playoff=0.,red_fouls=0.,blue_fouls=0.,p_live=.5)
            baseline = w.predicted(raw,layers.identity(),1,"a"*64)
            frames.append(dict(key=key,event=event,time=at,event_type=0,first_here=i==0,
                forecasts={"bootstrap":baseline,"epa":{"rm":110.,"bm":100.,"p":.55}}))
            card = MatchCard(key,event,at,"qm",start,tuple(f"frc{x}" for x in range(1,4)),tuple(f"frc{x}" for x in range(4,7)))
            red,blue = (120,80) if i%5 else (80,120)
            source[key] = {"result":MatchResult(card,red,blue,red,blue),"posted":at+60,"type":0}
    return frames,source


def run(root, frames=None, source=None, **kwargs):
    default_frames,default_source = fixtures()
    return p.rehearse(root,frames or default_frames,source or default_source,layers.identity(),
                      {"season":2024,"protocol":"test"},**kwargs)


@pytest.fixture
def fitted(monkeypatch):
    calls = []
    def fit(table,centre,cutoff):
        if not table:
            return None,{"status":"insufficient_matches"}
        calls.append(cutoff)
        params = copy.deepcopy(centre)
        params["weights"][0] = min(1.,params["weights"][0]+.1)
        return params,{"status":"candidate"}
    monkeypatch.setattr(p,"fit",fit)
    return calls


def test_training_excludes_exact_cutoff_delayed_ties_and_dq():
    frames,source = fixtures()
    cutoff = frames[159]["time"]
    source[frames[0]["key"]]["posted"] = cutoff
    source[frames[1]["key"]]["posted"] = None
    r = source[frames[2]["key"]]["result"]
    source[frames[2]["key"]]["result"] = MatchResult(r.card,100,100,100,100)
    source[frames[3]["key"]]["result"] = MatchResult(source[frames[3]["key"]]["result"].card,
        red_score=80,blue_score=120,red_points=None,blue_points=100)
    table = p.training_table(frames,source,cutoff)
    assert len(table["y"]) == 155
    assert np.all(table["time"] < cutoff) and np.all(table["posted"] < cutoff)
    assert table["rm"].tolist() == [110.]*155
    changed = copy.deepcopy(source)
    changed[frames[0]["key"]]["result"] = MatchResult(r.card,999,0,999,0)
    assert {k:v.tolist() for k,v in table.items()} == {k:v.tolist() for k,v in p.training_table(frames,changed,cutoff).items()}


def test_actual_fit_is_bounded_and_preserves_scores_and_full_fit_behavior():
    frames,source = fixtures()
    cutoff = frames[160]["time"]
    table = p.training_table(frames,source,cutoff)
    initial = layers.identity()
    params,evidence = p.fit(table,initial,cutoff)
    assert evidence["status"] == "candidate"
    layers.validate_params(params,initial)
    assert params["score_coef"] == initial["score_coef"] and params["blend"] == initial["blend"]
    assert params["weights"][0] > 0
    full = state.fit_layers(table,initial)
    assert {k:v for k,v in full.items() if k != "score_coef"} == {k:v for k,v in params.items() if k != "score_coef"}
    assert full["score_coef"] == state.bounded_score_fit(table,initial).coef.tolist()
    out = p.predict(frames,params,"version")
    assert frames == fixtures()[0]
    for f in out:
        for key in ("raw","rm","bm","rv","bv","params_sha256"):
            assert f["forecasts"]["active"][key] == f["forecasts"]["bootstrap"][key]
    assert out[0]["forecasts"]["active"]["p"] != frames[0]["forecasts"]["bootstrap"]["p"]


def test_resume_and_publication_boundary(tmp_path,fitted):
    frames,source = fixtures()
    monday = layers.monday(datetime.fromtimestamp(frames[160]["time"],timezone.utc)).timestamp()+7*86400
    publication = monday+10*3600
    for i,at in enumerate((monday+9*3600-1,monday+9*3600,publication-1,publication,publication+1)):
        f = copy.deepcopy(frames[160+i])
        f["key"] = f"2024clock_qm{i}"
        f["time"] = at
        source[f["key"]] = {**source[frames[160+i]["key"]],"posted":at+60}
        frames.append(f)
    partial = run(tmp_path,frames,source,max_weeks=1)
    assert partial["status"] == "incomplete"
    result = run(tmp_path,frames,source)
    assert len(fitted) == len(set(fitted))
    bootstrap = result["bootstrap"]
    clock = {f["time"]:f["forecasts"]["active"]["version"] for f in result["frames"] if f["key"].startswith("2024clock")}
    # A same-time start is forecast before the job/publication.
    assert clock[publication-1] == clock[publication] == clock[monday+9*3600]
    assert clock[publication+1] != clock[publication]
    first = result["frames"][0]
    assert first["forecasts"]["active"]["version"] == bootstrap
    assert first["forecasts"]["active"]["p"] == first["forecasts"]["bootstrap"]["p"]
    head = (tmp_path/"head.json").read_bytes()
    count = len(fitted)
    again = run(tmp_path,frames,source)
    assert again == result and len(fitted) == count and (tmp_path/"head.json").read_bytes() == head
    report = p.summarize(result,source)
    assert report["checks"]["state_and_scores_unchanged"] and report["maximum_rating_movement"] == 0
    assert not report["release_passed"] and not report["serving_eligible"]


def test_tamper_changed_identity_and_partial_cannot_pass(tmp_path,fitted):
    result = run(tmp_path,max_weeks=1)
    report = p.summarize(result,fixtures()[1])
    assert not report["checks"]["full_season"] and not report["development_checks_passed"]
    with pytest.raises(ValueError,match="inputs/protocol"):
        p.rehearse(tmp_path,*fixtures(),layers.identity(),{"season":2024,"protocol":"changed"})
    cp = w.read_object(tmp_path/"checkpoints",result["checkpoint"])
    path = tmp_path/"journals"/f"{cp['journals'][0]}.json"
    journal = json.loads(path.read_text())
    journal["forecasts"][0]["forecasts"]["active"]["p"] = .999
    path.write_text(json.dumps(journal))
    with pytest.raises(ValueError,match="hash mismatch"):
        run(tmp_path)


def test_rollback_restores_baseline_at_job_time(tmp_path,fitted,monkeypatch):
    calls = []
    def safety(frames,source,active,now):
        calls.append(active)
        return {"rollback":len(calls)==3}
    monkeypatch.setattr(w,"safety",safety)
    result = run(tmp_path)
    job = result["jobs"][2]
    assert job["safety"]["rollback"] and job["active_after"] == result["bootstrap"]
    assert not job["applied"] and job["fit"]["status"] == "skipped_after_rollback"
    assert p.summarize(result,fixtures()[1])["checks"]["rollback_restored"]


def test_fit_failure_and_source_journals_are_verified(tmp_path,monkeypatch):
    frames,source = fixtures()
    monkeypatch.setattr(state,"fit_win_layers",lambda *a:(_ for _ in ()).throw(ValueError("did not converge")))
    params,evidence = p.fit(p.training_table(frames,source,frames[160]["time"]),layers.identity(),frames[160]["time"])
    assert params is None and evidence["status"] == "rejected" and evidence["errors"] == ["did not converge"]
    rejected = run(tmp_path/"rejected")
    assert all(not j["applied"] for j in rejected["jobs"])
    assert all(j["active_after"] == rejected["bootstrap"] for j in rejected["jobs"])
    artifact = {"test":"artifact"}
    data = type("Data",(),{"year":2024,"source_sha256":"data","records":list(source.values())})()
    # These fixtures have the same clock and keys as their result cards.
    identity = {"schema":w.RECIPE,"season":2024,"data_sha256":"data","artifact_sha256":p.digest(artifact)}
    w.atomic_json(tmp_path/"manifest.json",identity)
    journal = write_settings(tmp_path/"journals",{"forecasts":frames})
    cp = write_settings(tmp_path/"checkpoints",{"identity":identity,"history_preserved":True,"journals":[journal.stem]})
    w.atomic_json(tmp_path/"head.json",{"sha256":cp.stem})
    loaded,lineage = p.source_run(tmp_path,data,artifact)
    assert loaded == frames and lineage["source_checkpoint"] == cp.stem
    with pytest.raises(ValueError,match="inputs do not match"):
        p.source_run(tmp_path,data,{"changed":True})
