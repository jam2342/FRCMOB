from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.ratings import rp_weekly as weekly
from app.services.ratings.rp_fitting import PublishedSeason
from tests.test_rp_predictions import SPEC
from tests.test_rp_fitting import cache_season
from scripts.evaluate_rp_weekly import evaluate


def test_weekly_cutoffs_use_publication_strictly_and_future_labels_cannot_change_past():
    first = datetime(2027,3,1,tzinfo=timezone.utc)
    rows,matches = [],[]
    for week in range(4):
        time = int((first+timedelta(days=7*week,hours=1)).timestamp())
        for i in range(100):
            key = f"2027test_qm{100*week+i+1}"
            # First-week results are published exactly at next Monday's cutoff.
            posted = int((first+timedelta(days=7)).timestamp()) if week == 0 else time+10
            matches.append((SimpleNamespace(match_key=key),{},posted,0))
            for color in ("red","blue"):
                rows.append({"key":f"{key}/{color}/a","event":"2027test","rp":"a","p":.9,"base":.5,
                             "y":i%5 != 0,"time":time})
    data = PublishedSeason(SPEC,matches,"fixture",{})
    probabilities,timeline = weekly.walk_forward(data,rows)
    assert timeline[0]["counts"]["a"]["n"] == timeline[1]["counts"]["a"]["n"] == 0
    assert timeline[2]["counts"]["a"]["n"] == 400
    assert timeline[2]["counts"]["b"]["n"] == 0
    altered = [{**r,"y":not r["y"]} if r["time"] >= int((first+timedelta(days=14)).timestamp()) else r for r in rows]
    changed,later = weekly.walk_forward(data,altered)
    assert timeline[:3] == later[:3]
    for r in rows:
        if r["time"] < int((first+timedelta(days=21)).timestamp()):
            assert probabilities[r["key"]] == changed[r["key"]]


def test_weekly_cli_reports_cold_start_and_requires_2026_parity_inputs(tmp_path):
    cache_season(tmp_path,2024)
    report = evaluate(tmp_path,2024)
    assert not report["serving_eligible"]
    assert report["comparisons"]["all"]["weekly_minus_raw"]["pooled"]["brier"]["mean_diff"] == pytest.approx(0,abs=1e-12)
    assert all(not c["fitted"] for t in report["timeline"] for c in t["counts"].values())


def test_reversion_cannot_silently_break_movement_limit():
    previous = {"a":{**weekly.identity(),"intercept":1.}}
    with pytest.raises(ValueError,match="movement"):
        weekly.fit_roles([],["a"],previous)
