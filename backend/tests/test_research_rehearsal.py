import json

import numpy as np
import pytest

from app.services.ratings import research_rehearsal as rehearsal
from app.services.ratings.research_win import WinRecipe, walk_forward_scores, walk_forward_win
from tests.test_research_win import _as_of_holds, _leaky_weekly, _table


def test_first_match_fit_does_not_see_future(monkeypatch):
    def run(t):
        return rehearsal.first_match_scores(t)[0]
    assert _as_of_holds(run)
    monkeypatch.setattr(rehearsal, "weekly", _leaky_weekly)
    assert not _as_of_holds(run)


def test_first_match_fit_scale_free():
    a = rehearsal.first_match_scores(_table())[0]
    b = rehearsal.first_match_scores(_table(scale=10))[0]
    np.testing.assert_allclose(a, b / 10)


def test_settings_reproduce_weekly_predictions_and_detect_tampering(tmp_path):
    t = _table()
    red, blue, scores = walk_forward_scores(t, terms=True)
    recipe = WinRecipe(base="logistic", correction="logistic", past=True)
    probs, fits, _ = walk_forward_win(t, recipe)
    for s, w in zip(scores, fits):
        payload = {"schema": 1, "win_uses_uncalibrated_scores": True, "score_terms": True,
                   "score_coef": s["coef"], "temperatures": w["temperatures"],
                   "weights": w["weights"], "recipe": rehearsal.PROTOCOL["champion"]}
        path = rehearsal.write_settings(tmp_path, payload)
        assert rehearsal.write_settings(tmp_path, payload) == path
        idx = t["week"] == s["week"]
        tab = {k: v[idx] for k, v in t.items()}
        actual = rehearsal.apply_settings(tab, rehearsal.load_settings(path))
        for a, b in zip(actual, (red[idx], blue[idx], probs[idx])):
            np.testing.assert_allclose(a, b, rtol=1e-13, atol=1e-13)
    path.write_text(json.dumps({**payload, "schema": 99}))
    with pytest.raises(ValueError, match="version"):
        rehearsal.load_settings(path)


@pytest.mark.parametrize("comparison,verdict", [
    ({"n": 0}, "inconclusive"),
    ({"n": 100, "lo90": -0.001, "hi90": 0.0004}, "passed"),
    ({"n": 100, "lo90": 0.0006, "hi90": 0.002}, "failed"),
    ({"n": 100, "lo90": -0.001, "hi90": 0.002}, "inconclusive"),
])
def test_noninferiority_uses_interval(comparison, verdict):
    assert rehearsal.noninferiority(comparison) == verdict


def test_frozen_comparator_ignores_heldout_outcomes():
    from evaluate_rating_phase4 import frozen_logistic

    past_table = _table(seed=9)
    _, _, past = walk_forward_win(past_table, rehearsal.CHAMPION)
    t = _table()
    cutoff = int(t["time"][t["holdout"]].min())
    flipped = _table(flip_from_time=cutoff)
    a = frozen_logistic(t, [past], cutoff)
    b = frozen_logistic(flipped, [past], cutoff)
    np.testing.assert_array_equal(a, b)
    # A whole-season fit must detect the changed labels, proving this isn't vacuous.
    leaky_a = frozen_logistic(t, [past], int(t["time"].max()) + 1)
    leaky_b = frozen_logistic(flipped, [past], int(t["time"].max()) + 1)
    assert not np.allclose(leaky_a, leaky_b)


def test_late_term_is_scale_free_and_as_of(monkeypatch):
    from app.services.ratings import research_win

    recipe = rehearsal.CHALLENGERS["late_term"]
    def run(t):
        return walk_forward_win(t, recipe)[0]
    assert _as_of_holds(run)
    np.testing.assert_allclose(run(_table()), run(_table(scale=10)))
    monkeypatch.setattr(research_win, "weekly", _leaky_weekly)
    assert not _as_of_holds(run)
