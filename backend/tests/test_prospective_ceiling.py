import copy
from datetime import datetime

import numpy as np
import pytest

from app.services.ratings import age_score_rehearsal as age, calibrations as layers, prospective_ceiling as ceiling, weekly_rehearsal as w
from tests.test_age_score_rehearsal import source_run, table  # noqa: F401


def test_in_effect_switches_strictly_after_publication():
    path = [{"publication":None,"layers":"boot"},{"publication":100,"layers":"a"},{"publication":200,"layers":"b"}]
    assert ceiling.in_effect(path,100)["layers"] == "boot"
    assert ceiling.in_effect(path,101)["layers"] == "a"
    assert ceiling.in_effect(path,201)["layers"] == "b"


def test_weekly_layer_fit_keeps_the_active_layers_when_the_sanity_check_fails(monkeypatch):
    t = table()
    centre = layers.identity()
    fitted,errors = ceiling.fit_week(t,centre,centre,float(t["time"].max())+1)
    assert errors == [] and fitted != centre
    layers.validate_params(fitted,centre)
    worse = copy.deepcopy(fitted)
    monkeypatch.setattr(ceiling.state,"fit_layers",lambda *a,**k:worse)
    monkeypatch.setattr(ceiling.layers,"apply",lambda tt,p:(None,None,np.where(p is worse,1-tt["y"],tt["y"])))
    kept,errors = ceiling.fit_week(t,centre,centre,float(t["time"].max())+1)
    assert kept is centre and errors == ["last-two-week Brier sanity check failed"]


def test_layer_path_uses_only_earlier_posted_results_and_reproduces_frames(source_run):  # noqa: F811
    root,current,_ = source_run
    before = age.hashes(root)
    frames,report,versions,_ = age.source_run(root)
    params = versions[report["bootstrap_id"]]["artifact"]["params"]
    path = ceiling.layer_path(root,current.year,report,versions,params)
    assert age.hashes(root) == before and len(path) > 1
    jobs = {datetime.fromisoformat(j["cutoff"]).timestamp() for j in report["jobs"]}
    for entry in path[1:]:
        assert entry["cutoff"] in jobs and entry["publication"]-entry["cutoff"] == w.POLICY["publication_hour_utc"]*3600
        assert entry["matches"] >= ceiling.POLICY["minimum_fit_matches"]
    # With the bootstrap state as "hindsight" and the source's own raw features, the ceiling
    # before its first publication is the bootstrap forecast itself.
    oracle = {"forecasts":[{"key":f["key"],"prediction":f["forecasts"]["bootstrap"]} for f in frames]}
    out = ceiling.forecasts(oracle,frames,path)
    early = [f for f in frames if f["time"] <= path[1]["publication"]]
    assert early and all(out[f["key"]]["p"] == pytest.approx(f["forecasts"]["bootstrap"]["p"],abs=1e-12) for f in early)
    source = {r["result"].card.match_key:r for r in current.records}
    summary = ceiling.summarize(frames,source,oracle,path)
    assert set(summary["checks"]) == {"week4_within_ceiling","week4_within_old_ceiling"}
    assert all("layers" not in e and "layers_sha256" in e for e in summary["path"])
