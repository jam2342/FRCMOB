import copy
import math

import numpy as np
import pytest

from app.services.ratings import score_positivity as positivity
from app.services.ratings.replay import MatchCard, MatchResult


def test_censored_mean_matches_numerical_integration_and_never_lowers_a_forecast():
    for mean,var in ((180.,1600.),(40.,900.),(0.,400.),(-22.,900.),(-100.,25.),(-29.,1.)):
        sd = math.sqrt(var)
        y = np.linspace(0,max(mean,0)+12*sd,400001)
        density = np.exp(-(y-mean)**2/(2*var))/(sd*math.sqrt(2*math.pi))
        expected = float(np.sum(y*density)*(y[1]-y[0]))
        value = positivity.censored_mean(mean,var)
        assert value > 0 and value >= mean
        if expected > 1e-12:
            assert value == pytest.approx(expected,rel=1e-4)
    assert positivity.censored_mean(180.,1600.)-180. < 1e-3  # 4.5 sd up: effectively unchanged
    for bad in ((1.,0.),(math.nan,1.),(1.,-1.)):
        with pytest.raises(ValueError,match="positive variance"):
            positivity.censored_mean(*bad)


def test_far_tail_series_is_continuous_and_positive():
    left,right = positivity.censored_mean(-8.0000001,1.),positivity.censored_mean(-7.9999999,1.)
    assert left > 0 and right > 0 and left == pytest.approx(right,rel=1e-3)
    assert 0 < positivity.censored_mean(-29.9,1.) < 1e-190
    with pytest.raises(ValueError,match="30 sd"):
        positivity.censored_mean(-31.,1.)


def frames_and_source(n=120):
    rng = np.random.default_rng(3)
    frames,source = [],{}
    for i in range(n):
        key,event = f"2026e{i % 6}_qm{i}",f"2026e{i % 6}"
        card = MatchCard(key,event,1_770_000_000+600*i,"qm",1_770_000_000,("frc1","frc2","frc3"),("frc4","frc5","frc6"))
        mean = float(rng.uniform(-20,200)) if i % 4 else -10.
        actual = max(0,int(mean+rng.normal(0,30)))
        frames.append({"key":key,"event":event,"time":card.time,"playoff":0.,"first_here":i < 6,
                       "forecasts":{"active":{"rm":mean,"bm":120.,"rv":900.,"bv":900.,"p":.6}}})
        source[key] = {"result":MatchResult(card,actual,120,actual,120)}
    return frames,source


def test_apply_keeps_the_base_and_wins_and_compare_reports_the_gate():
    frames,source = frames_and_source()
    original = copy.deepcopy(frames)
    projected = positivity.apply(frames,"active")
    assert frames == original
    assert all(f["forecasts"]["active"] == o["forecasts"]["active"] for f,o in zip(projected,original))
    result = positivity.compare(projected,source,"active")
    checks = positivity.checks(result)
    assert result["negative"]["base"] > 0 and result["negative"]["positive"] == 0
    assert checks["all_positive"] and checks["wins_unchanged"] and checks["top_decile_unmoved"]
    assert result["metrics"]["base_negative"]["positive_minus_base_mae"]["mean_diff"] < 0
    projected[0]["forecasts"]["active_positive"]["p"] = .7
    assert not positivity.checks(positivity.compare(projected,source,"active"))["wins_unchanged"]


def test_a_lowered_or_nonpositive_forecast_fails_and_mismatched_sides_raise():
    frames,source = frames_and_source()
    projected = positivity.apply(frames,"active")
    projected[1]["forecasts"]["active_positive"]["rm"] = 0.
    assert not positivity.checks(positivity.compare(projected,source,"active"))["all_positive"]
    del source[frames[2]["key"]]["result"]
    source[frames[2]["key"]]["result"] = MatchResult(source[frames[3]["key"]]["result"].card,None,None,None,None)
    projected = positivity.apply(frames,"active")
    projected[2]["forecasts"]["active"]["rm"] = 50.
    result = positivity.compare(projected,source,"active")
    assert result["metrics"]["qualifications"]["base"]["sides"] == 2*len(frames)-2
