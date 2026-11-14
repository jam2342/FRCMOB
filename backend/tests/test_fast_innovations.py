from dataclasses import replace
import random

import numpy as np
import pytest

from app.services.ratings.fast_innovations import PackedInnovations
from app.services.ratings.replay import MatchCard, MatchResult, Season
from app.services.ratings.research_data import ResearchSeason
from app.services.ratings.research_filter import ScaleFreeParams
from app.services.ratings.research_fit import SeasonInputs, innovations, fit_likelihood, Pull


def inputs(n=70):
    rng = random.Random(16)
    results = []
    for i in range(n):
        teams = rng.sample([f"frc{j}" for j in range(12)],6)
        # Revisit an older event after a newer one, including deferred results.
        card = MatchCard(f"match{i}",f"event{i%3}",i*100,"qm",0,tuple(teams[:3]),tuple(teams[3:]))
        red,blue = max(0.,rng.gauss(100,30)),max(0.,rng.gauss(100,30))
        if i%11 == 0:
            red = None
        results.append(MatchResult(card,100,80,red,blue,
            red_components={} if i%7 == 0 else {"total_points":red},blue_components={"total_points":blue}))
    return SeasonInputs(ResearchSeason(Season(2024,results,set(),{})),[0]*n,[r.card.time for r in results],
                        {"frc0":1.,"frc1":-.5},{"frc4"},20.,[])


@pytest.mark.parametrize("compiled",[False,True])
def test_packed_filter_matches_reference_with_masks_transfers_and_cutoffs(compiled):
    if compiled:
        pytest.importorskip("numba")
    data = inputs()
    packed = PackedInnovations(data,compiled=compiled)
    for p in (ScaleFreeParams(margin_cov=False),ScaleFreeParams(prior=.4,match=.02,event=.1,noise=.6,noise_count=.3,margin_cov=False)):
        for until in (0,1500,None):
            assert packed.verify(p,until)["max_abs_error"] < 1e-9


def test_future_rows_cannot_change_earlier_innovations():
    data = inputs()
    original = PackedInnovations(data,compiled=False)
    data.research.season.results[-1] = replace(data.research.season.results[-1],red_points=1e9,
        card=replace(data.research.season.results[-1].card,red=("new1","new2","new3")))
    changed = PackedInnovations(data,compiled=False)
    p = ScaleFreeParams(margin_cov=False,noise_count=.3)
    for a,b in zip(original(p,1500),changed(p,1500)):
        np.testing.assert_array_equal(a,b)


def test_unsupported_forms_and_unordered_times_fail_closed():
    data = inputs()
    packed = PackedInnovations(data,compiled=False)
    with pytest.raises(ValueError,match="counting noise"):
        packed(ScaleFreeParams(noise_prop=.2),None)
    data.times = list(reversed(data.times))
    with pytest.raises(ValueError,match="ordered"):
        PackedInnovations(data,compiled=False)


def test_fit_callback_preserves_the_reference_objective_and_fit():
    data = inputs(30)
    p = ScaleFreeParams(margin_cov=False,noise_count=.2)
    options = dict(names=("noise",),until=None,pull=Pull.around(p,("noise",)),max_evals=50,restarts=0)
    reference = fit_likelihood(data,p,**options)
    accelerated = fit_likelihood(data,p,innovations_fn=PackedInnovations(data,compiled=False),**options)
    assert accelerated["objective"] == pytest.approx(reference["objective"],abs=1e-9)
    assert accelerated["params"] == reference["params"]
    expected = innovations(data,p,None)
    assert len(expected[0]) == len(PackedInnovations(data,compiled=False)(p,None)[0])
