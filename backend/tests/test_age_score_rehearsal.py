import copy
from datetime import datetime, timedelta, timezone
from functools import partial
import json

import numpy as np
import pytest

from app.services.ratings import age_score_rehearsal as age, calibrations as layers, gradual_rehearsal as g, probability_rehearsal as probability, weekly_rehearsal as w
from app.services.ratings.research_rehearsal import write_settings
from app.services.ratings.research_win import side_arrays
from tests import test_gradual_rehearsal as shared
from tests.test_probability_rehearsal import fixtures
from tests.test_weekly_rehearsal import artifact, data


def table():
    frames,source = fixtures()
    return probability.training_table(frames,source,frames[-1]["time"]+120)


@pytest.fixture
def source_run(tmp_path,monkeypatch):
    shared.goals.__wrapped__(monkeypatch)
    calls = []
    def safety(*args):
        calls.append(True)
        return {"rollback":len(calls)==4,"comparison":{}}
    monkeypatch.setattr(w,"safety",safety)
    current = data(2024,4)
    root = tmp_path/"source"
    run = w.rehearse(root,current,data(2023,1),artifact(),"a"*64,"test",
        fit_callback=partial(g.fit_job,calibration_prior="bootstrap"))
    report = w.summarize(root,current,run,artifact(),include_hindsight=False)
    report.update(application="gradual",calibration_prior="bootstrap")
    report["checks"]["active_parent"] = True
    write_settings(root/"reports",report)
    return root,current,run


def test_interpolated_intercepts_keep_effective_bounds_and_ignore_other_contexts():
    t = table()
    t["min_here"][:] = 0
    t["season_frac"] = np.linspace(0,.3,len(t["y"]))
    t["playoff"][-1] = 1
    sides = side_arrays(t)
    x = age.design(t,sides)
    assert np.allclose(x.sum(axis=1),sides["ctx"]==0)
    old,new = np.array([-.6,.1]),np.array([-.45,-.05])
    age.validate(new,old)
    assert np.max(np.abs(x@(new-old))) <= layers.SCORE_STEP+1e-12
    assert np.all(np.abs(x@new) <= age.POLICY["intercept_limit"])
    assert x[sides["ctx"]==3].sum()==0
    with pytest.raises(ValueError,match="weekly"):
        age.validate([-.44,-.05],old)
    t["season_frac"][0] = np.nan
    with pytest.raises(ValueError,match="season age"):
        age.design(t,sides)


def test_two_endpoint_fit_learns_opposite_periods_and_keeps_fixed_prior():
    t = table()
    t["min_here"][:] = 0
    t["season_frac"][:len(t["y"])//2] = 0
    t["season_frac"][len(t["y"])//2:] = .3
    for key,mean in (("ra","rm"),("ba","bm")):
        t[key] = t[mean]+np.where(t["season_frac"]==0,-.4,.3)*t["u"]
    nuisance = layers.identity()
    prior = [0.,0.]
    unrestricted = age.fit(t,nuisance,prior)
    assert unrestricted[0]<-.3 and unrestricted[1]>.2
    bounded = age.fit(t,nuisance,prior,[0.,0.])
    np.testing.assert_allclose(bounded,[-.15,.15],atol=1e-9)
    repeated = age.fit(t,nuisance,prior,unrestricted)
    np.testing.assert_allclose(repeated,unrestricted,atol=1e-8)
    assert prior==[0.,0.] and nuisance==layers.identity()


def test_prediction_preserves_wins_raw_nonfirst_and_full_source():
    frames,_ = fixtures()
    for frame in frames:
        frame["forecasts"]["active"] = copy.deepcopy(frame["forecasts"]["bootstrap"])
    original = copy.deepcopy(frames)
    source_versions={1:{"artifact":{"layers":layers.identity()}}}
    payload={"endpoints":[-.4,.2]}
    projected=age.predict(frames,{1:payload},source_versions)
    for f,before in zip(projected,original):
        p,base=f["forecasts"]["age_score"],before["forecasts"]["bootstrap"]
        assert p["p"]==base["p"] and p["raw"]==base["raw"]
        assert p["params_sha256"]==w.digest(payload)
        if base["raw"]["min_here"]>0:
            assert (p["rm"],p["bm"])==(base["rm"],base["bm"])
        else:
            assert p["rm"]-base["rm"]==pytest.approx(p["bm"]-base["bm"])
    assert frames==original


def test_bootstrap_uses_only_actual_earlier_season_tables(tmp_path):
    t=table()
    for year in (2023,2024):
        earlier={k:v.copy() for k,v in t.items()}
        earlier["key"]=np.array([str(k).replace('2024',str(year)) for k in t["key"]])
        earlier["time"] += int(datetime(year,3,3,tzinfo=timezone.utc).timestamp()-datetime(2024,3,3,tzinfo=timezone.utc).timestamp())
        np.savez(tmp_path/f"{year}.npz",**earlier)
    (tmp_path/'2025.npz').write_text('future table must never be opened')
    prior=age.bootstrap(2025,tmp_path)
    assert set(prior["source_tables"])=={'2023','2024'} and not prior["provisional"]
    assert age.bootstrap(2023,tmp_path)=={"endpoints":[0.,0.],"source_tables":{},"provisional":True}
    with pytest.raises(ValueError,match="examined"):
        age.bootstrap(2027,tmp_path)
    np.savez(tmp_path/'2023.npz',**t)
    with pytest.raises(ValueError,match="another season"):
        age.bootstrap(2024,tmp_path)


def test_real_replay_resume_rollback_bounds_and_source_integrity(tmp_path,source_run,monkeypatch):
    root,current,original = source_run
    before=age.hashes(root)
    prior={"endpoints":[0.,0.],"source_tables":{},"provisional":True}
    output=tmp_path/'output'
    result=age.rehearse(output,root,current,prior,'p'*64,'test')
    assert result['history_preserved'] and before==age.hashes(root)
    frames,report,_,_=age.source_run(root)
    assert len(result['frames'])==len(frames)
    rollback=next(j for j in report['jobs'] if j['safety']['rollback'])
    after=[f for f in result['frames'] if f['time']>datetime.fromisoformat(rollback['cutoff']).timestamp()+9*3600]
    assert after and after[0]['forecasts']['age_score']['source_version']==report['bootstrap_id']
    summary=age.summarize(result,current)
    assert summary['checks']['wins_unchanged'] and summary['checks']['nonfirst_scores_unchanged']
    assert not summary['release_passed']
    hashes=age.hashes(output)
    monkeypatch.setattr(age,'fit',lambda *a:pytest.fail('completed resume refitted'))
    again=age.rehearse(output,root,current,prior,'p'*64,'equivalent-merge-commit')
    assert again==result and hashes==age.hashes(output)
    changed=copy.deepcopy(prior)
    changed['endpoints'][0]=.1
    with pytest.raises(ValueError,match='source, prior or protocol changed'):
        age.rehearse(output,root,current,changed,'p'*64,'test')


def test_publication_boundary_is_strict_even_for_hash_valid_source(tmp_path,source_run):
    root,_,_=source_run
    _,report,_,_=age.source_run(root)
    job=next(j for j in report['jobs'] if j['applied'])
    checkpoint=w.read_object(root/'checkpoints',report['checkpoint'])
    for old in list(checkpoint['journals']):
        journal=w.read_object(root/'journals',old)
        matches=[f for f in journal['forecasts'] if f['forecasts']['active']['version']==job['fit']['id']]
        if matches:
            matches[0]['time']=(datetime.fromisoformat(job['cutoff'])+timedelta(hours=10)).timestamp()
            new=write_settings(root/'journals',journal).stem
            checkpoint['journals'][checkpoint['journals'].index(old)]=new
            break
    else:
        pytest.fail('fixture lacks a post-publication match')
    cp=write_settings(root/'checkpoints',checkpoint)
    w.atomic_json(root/'head.json',{'sha256':cp.stem})
    report['checkpoint']=cp.stem
    write_settings(root/'reports',report)
    with pytest.raises(ValueError,match='unavailable version'):
        age.source_run(root)


def test_checkpoint_provenance_and_source_tampering_fail_closed(tmp_path,source_run):
    root,current,_=source_run
    prior={"endpoints":[0.,0.],"source_tables":{},"provisional":True}
    output=tmp_path/'output'
    result=age.rehearse(output,root,current,prior,'p'*64,'test')
    version=next(iter(result['versions'].values()))
    path=output/'versions'/f'{version}.json'
    value=json.loads(path.read_text())
    value['endpoints'][0]+=.01
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='hash mismatch'):
        age.rehearse(output,root,current,prior,'p'*64,'test')
    journal=next((root/'journals').glob('*.json'))
    value=json.loads(journal.read_text())
    value['kind']='tampered'
    journal.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='hash mismatch'):
        age.source_run(root)


def test_source_prior_mismatch_and_overlapping_output_are_rejected_before_writes(tmp_path,source_run):
    root,current,_=source_run
    before=age.hashes(root)
    prior={"endpoints":[0.,0.],"source_tables":{},"provisional":True}
    for output in (root,root/'nested',root.parent):
        with pytest.raises(ValueError,match='overlap'):
            age.rehearse(output,root,current,prior,'p'*64,'test')
    wrong={**prior,'source_tables':{'2023':'a'*64}}
    output=tmp_path/'mismatch'
    with pytest.raises(ValueError,match='prior tables differ'):
        age.rehearse(output,root,current,wrong,'p'*64,'test')
    assert not output.exists() and before==age.hashes(root)
