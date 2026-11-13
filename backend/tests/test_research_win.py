import random
from dataclasses import replace

import sys
from pathlib import Path

import numpy as np
import pytest

from app.services.ratings import research_win
from app.services.ratings.research_data import load_cached_season
from app.services.ratings.research_eval import collect_rows, walk_forward_probs
from app.services.ratings.research_filter import ScaleFreeParams
from app.services.ratings.research_fit import season_inputs
from app.services.ratings.research_models import MarginElo
from app.services.ratings.research_win import (
    Network,
    WinRecipe,
    bucket_probs,
    bundle_table,
    correction_features,
    side_probs,
    walk_forward_scores,
    walk_forward_win,
    zscore,
)
from tests.test_research_fit import _league
from tests.test_research_harness import _match, _write_event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

RECIPES = {
    "temperatures": WinRecipe(base="logistic"),
    "phi temperatures": WinRecipe(),
    "logistic correction": WinRecipe(base="logistic", correction="logistic"),
    "network": WinRecipe(base="logistic", correction="network"),
    "stacked": WinRecipe(base="logistic", correction="logistic", stack=("epa", "elo")),
}


def _table(*, weeks=6, per_week=400, seed=0, flip_from_time=None, scale=1.0, late_label=True):
    # A synthetic season table: alliances of three latent robots, a weekly schedule of
    # events, sides scored with diminishing returns so the layers have something to fit.
    # late_label: the last week's first event carries week label 0 (TBA labels aren't
    # playing order), so a fit cut by label instead of time would see it early.
    rng = np.random.default_rng(seed)
    n = weeks * per_week
    week = np.repeat(np.arange(weeks), per_week)
    time = week * 1_000_000 + np.tile(np.arange(per_week), weeks) * 100
    robots = rng.gamma(2.0, 0.5, size=(n, 6))
    red, blue = robots[:, :3], robots[:, 3:]
    rm, bm = red.sum(1), blue.sum(1)
    ra = (rm - 0.3 * red.min(1) + rng.normal(0, 0.8, n)) * 30 * scale
    ba = (bm - 0.3 * blue.min(1) + rng.normal(0, 0.8, n)) * 30 * scale
    if flip_from_time is not None:
        later = time >= flip_from_time
        ra[later], ba[later] = ba[later] + 7, ra[later]
    y = np.where(ra > ba, 1.0, np.where(ra < ba, 0.0, 0.5))
    min_here = np.tile(np.arange(per_week) % 12, weeks)
    if late_label:
        week = week.copy()
        week[(week == weeks - 1) & (np.tile(np.arange(per_week), weeks) < per_week // 4)] = 0
    return {
        "key": np.array([f"m{i}" for i in range(n)]),
        "event": np.array([f"e{w}_{i % 4}" for w, i in zip(week, range(n))]),
        "week": week, "time": time, "event_type": np.zeros(n, int), "playoff": (min_here == 11).astype(float),
        "min_here": min_here, "max_here": min_here, "min_before": np.minimum(week * 10, 40), "y": y, "holdout": week == weeks - 1,
        "third": np.minimum(min_here // 4, 3), "rm": rm * 30 * scale, "bm": bm * 30 * scale,
        "rv": np.full(n, 900.0) * scale**2, "bv": np.full(n, 900.0) * scale**2, "mv": np.full(n, 1800.0) * scale**2,
        "u": np.full(n, 30.0 * scale), "ra": ra, "ba": ba, "best_r": red.max(1), "best_b": blue.max(1),
        "worst_r": red.min(1), "worst_b": blue.min(1), "sd_r": np.full(n, 0.3), "sd_b": np.full(n, 0.3),
        "season_frac": week / 17.0, "p_bucket": np.full(n, 0.5),
        "p_epa": 1 / (1 + np.exp(-(rm - bm))), "p_elo": 1 / (1 + np.exp(-0.5 * (rm - bm))),
    }


def _cut(t, k=3):
    return int(t["time"][t["week"] == k].min())


def _as_of_holds(run, k=3) -> bool:
    # Flip every result played from week k's start on: every forecast made before that
    # time (weeks up to k, and the late event labelled week 0, which is forecast from
    # week 0's fit) must not move, and something later must, or the test proves nothing.
    base = _table()
    cut = _cut(base, k)
    flipped = _table(flip_from_time=cut)
    a, b = run(base), run(flipped)
    early = base["week"] <= k
    assert not np.allclose(a[~early], b[~early]), "the flip never reached a later forecast"
    return bool(np.array_equal(a[early], b[early]))


_WEEKLY = research_win.weekly


def _leaky_weekly(t):
    # The leak planted back: each week's fit sees the whole season.
    return [(w, int(t["time"].max()) + 1, idx) for w, _start, idx in _WEEKLY(t)]


def _label_weekly(t):
    # The week-label leak: each week's fit sees every row labelled earlier, whenever it
    # was played.
    out = []
    for w, start, idx in _WEEKLY(t):
        lower = t["time"][t["week"] < w]
        out.append((w, max(start, int(lower.max()) + 1) if len(lower) else start, idx))
    return out


@pytest.fixture
def plant_leak(monkeypatch):
    def plant(kind="season"):
        monkeypatch.setattr(research_win, "weekly", _leaky_weekly if kind == "season" else _label_weekly)
    return plant


@pytest.mark.parametrize("leak", ["season", "label"])
@pytest.mark.parametrize("terms", [False, True])
def test_score_calibration_never_sees_a_later_result(plant_leak, terms, leak):
    def run(t):
        red, blue, _ = walk_forward_scores(t, terms=terms)
        return np.column_stack([red, blue])

    assert _as_of_holds(run)
    plant_leak(leak)
    assert not _as_of_holds(run)


@pytest.mark.parametrize("leak", ["season", "label"])
@pytest.mark.parametrize("name", list(RECIPES))
def test_win_layer_never_sees_a_later_result(plant_leak, name, leak):
    def run(t):
        return walk_forward_win(t, RECIPES[name])[0]

    assert _as_of_holds(run)
    plant_leak(leak)
    assert not _as_of_holds(run)


def test_earlier_seasons_start_the_correction_and_this_season_still_cannot_leak(plant_leak):
    recipe = replace_past(RECIPES["logistic correction"])
    _, _, last_season = walk_forward_win(_table(seed=9), recipe)

    def run(t):
        return walk_forward_win(t, recipe, [last_season])[0]

    # Week 0 has no earlier match: without a past season it gets no correction at all.
    t = _table()
    alone = walk_forward_win(t, RECIPES["logistic correction"])[0]
    started = run(t)
    first = t["week"] == 0
    assert not np.allclose(alone[first], started[first])
    assert _as_of_holds(run)
    plant_leak()
    assert not _as_of_holds(run)


def replace_past(recipe):
    return replace(recipe, past=True)


def test_correction_features_are_scale_free_and_flip_with_the_sides():
    small, big = _table(), _table(scale=10.0)
    assert np.allclose(correction_features(small), correction_features(big))
    assert np.allclose(zscore(small), zscore(big))
    swapped = {**small, "rm": small["bm"], "bm": small["rm"], "best_r": small["best_b"], "best_b": small["best_r"],
               "worst_r": small["worst_b"], "worst_b": small["worst_r"]}
    f, g = correction_features(small), correction_features(swapped)
    assert np.allclose(f[:, :2], g[:, :2])  # the intercepts don't flip
    assert np.allclose(f[:, 2:], -g[:, 2:])


def test_weights_learned_on_one_scale_start_another():
    # A correction fitted on a season scored in tens of points applies unchanged to one
    # scored in hundreds: same probabilities, because nothing is in points.
    recipe = replace_past(RECIPES["logistic correction"])
    _, _, past = walk_forward_win(_table(seed=4), recipe)
    a = walk_forward_win(_table(seed=5), recipe, [past])[0]
    b = walk_forward_win(_table(seed=5, scale=10.0), recipe, [past])[0]
    assert np.allclose(a, b)


def test_composition_terms_find_diminishing_returns():
    t = _table(per_week=1500)
    sides = research_win.side_arrays(t)
    cal = research_win.fit_score_calibration(sides, terms=True)
    # The weakest robot adds 0.7 of itself (simulated): its term is about -0.3.
    assert cal.coef[-3] == pytest.approx(-0.3, abs=0.05)


def test_network_learns_a_logistic_signal():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(4000, 3))
    y = (rng.random(4000) < 1 / (1 + np.exp(-(1.5 * x[:, 0] - x[:, 1])))).astype(float)
    p = 1 / (1 + np.exp(-Network().fit(x, y).logit(x)))
    assert np.mean((p - y) ** 2) < np.mean((0.5 - y) ** 2) - 0.05


def test_bucket_replica_matches_the_harness(tmp_path):
    inputs = season_inputs(_league(tmp_path, per_week=100), None)
    plan = [(0, ScaleFreeParams())]
    t = bundle_table(inputs, plan)
    assert np.array_equal(bucket_probs(t, zscore(t)), t["p_bucket"])


def test_bundle_table_is_as_of_and_scale_free(tmp_path):
    k = 2
    base = season_inputs(_league(tmp_path / "a"), None)
    flipped = season_inputs(_league(tmp_path / "b", flip_from=k), None)
    a, b = (bundle_table(i, [(0, ScaleFreeParams())]) for i in (base, flipped))
    early = a["week"] < k
    for column in ("rm", "bm", "mv", "best_r", "worst_b", "sd_r", "u", "p_bucket"):
        assert np.array_equal(a[column][early], b[column][early]), column
    assert not np.allclose(a["rm"][~early], b["rm"][~early])


def _scaled_league(root, factor):
    rng = random.Random(3)
    strength = {f"frc{i}": 10.0 + 4 * i for i in range(1, 13)}
    for week in range(3):
        matches = []
        for i in range(60):
            teams = rng.sample(sorted(strength), 6)
            red, blue = tuple(teams[:3]), tuple(teams[3:])
            rs = int(factor * (sum(strength[t] for t in red) + rng.gauss(0, 10)))
            bs = int(factor * (sum(strength[t] for t in blue) + rng.gauss(0, 10)))
            matches.append(_match(f"2025w{week}_qm{i}", "qm", 1_000_000 * week + 100 * i, red, blue, max(rs, 0), max(bs, 0)))
        _write_event(root, 2025, f"2025w{week}", matches, week=week)
    return root


def test_replayed_features_do_not_depend_on_the_game_s_scale(tmp_path):
    # Both start from a previous season's unit in their own points (as every real season
    # does); with none, the first match divides by a fixed 30 points.
    tables = []
    for factor in (1, 10):
        research = load_cached_season(_scaled_league(tmp_path / str(factor), factor), 2025)
        inputs = replace(season_inputs(research, None), previous_unit=20.0 * factor)
        tables.append(bundle_table(inputs, [(0, ScaleFreeParams())]))
    a, b = tables
    # Integer rounding of the scores differs between the two scales, hence the tolerance.
    assert np.allclose(correction_features(a), correction_features(b), atol=0.02)


def test_margin_elo_predicts_before_it_learns(tmp_path):
    k = 1
    out = []
    for folder, flip in (("a", None), ("b", k)):
        research = _league(tmp_path / folder, flip_from=flip)
        inputs = season_inputs(research, None)
        out.append((collect_rows(research, MarginElo({}, None)), side_probs(inputs, MarginElo({}, None))))
    (rows_a, probs_a), (rows_b, probs_b) = out
    before = [i for i, r in enumerate(rows_a) if r.week < k]
    assert before and all(rows_a[i].prediction == rows_b[i].prediction for i in before)
    assert all(probs_a[rows_a[i].match_key] == probs_b[rows_b[i].match_key] for i in before if rows_a[i].week == 0)
    assert any(a.prediction != b.prediction for a, b in zip(rows_a, rows_b) if a.week > k)
    # It learns the league: by the last week it favours the stronger side most of the time.
    probs, _, _ = walk_forward_probs(rows_a)
    late = [(p, r.y) for p, r in zip(probs, rows_a) if r.week == 3]
    assert np.mean([(p > 0.5) == (y > 0.5) for p, y in late]) > 0.7


def test_a_season_chain_only_runs_forward():
    # Changing a later season leaves every earlier one alone; changing an earlier one
    # reaches the later season's "past" variants and nothing else of it.
    p3 = pytest.importorskip("evaluate_rating_phase3")

    def run(t23, t24):
        return p3.run_seasons([2023, 2024], {2023: t23, 2024: t24}, p3.BLIND_WIN, p3.BLIND_SCORE, p3.BLIND_FIXED)

    a = run(_table(seed=1), _table(seed=2))
    b = run(_table(seed=1), _table(seed=2, flip_from_time=0))
    for name in p3.BLIND_WIN:
        assert np.array_equal(a["win"][2023][name], b["win"][2023][name])
    for name in p3.BLIND_SCORE:
        assert all(np.array_equal(x, y) for x, y in zip(a["score"][2023][name], b["score"][2023][name]))
    c = run(_table(seed=3), _table(seed=2))
    for name in p3.BLIND_WIN:
        same = np.array_equal(a["win"][2024][name], c["win"][2024][name])
        assert same == (not p3.WIN_VARIANTS[name].past), name
    assert not np.array_equal(a["score"][2024]["score_terms_past"][0], c["score"][2024]["score_terms_past"][0])
    assert np.array_equal(a["score"][2024]["score_none"][0], c["score"][2024]["score_none"][0])


def test_features_come_from_the_settings_in_use(tmp_path):
    # A plan that switches settings mid-season: every row's features come from the filter
    # serving it, the same as a filter run on those settings alone.
    inputs = season_inputs(_league(tmp_path), None)
    switch = min(t for t, w in zip(inputs.times, inputs.weeks) if w == 2)
    first, second = ScaleFreeParams(), replace(ScaleFreeParams(), noise=0.5, event=0.6)
    mixed = bundle_table(inputs, [(0, first), (switch, second)])
    for params, keep in ((first, mixed["time"] < switch), (second, mixed["time"] >= switch)):
        alone = bundle_table(inputs, [(0, params)])
        assert keep.any()
        for column in ("rm", "mv", "best_r", "worst_b", "sd_r", "u"):
            assert np.array_equal(mixed[column][keep], alone[column][keep]), column
