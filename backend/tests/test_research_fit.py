import math
import random
from dataclasses import replace

import numpy as np
import pytest

from app.services.ratings.research_data import load_cached_season
from app.services.ratings.research_eval import collect_rows, walk_forward_probs
from app.services.ratings.research_filter import ScaleFreeFilter, ScaleFreeParams
from app.services.ratings.research_fit import (
    BASE,
    Pull,
    ScheduledModel,
    as_of_plan,
    fit_likelihood,
    innovations,
    loglik,
    nelder_mead,
    season_inputs,
)
from app.services.ratings.research_models import ScaleFreeTotalModel, start_multipliers
from app.services.ratings.team_model import TeamModelParams
from tests.test_research_harness import _match, _write_event

STRENGTH = {f"frc{i}": 10.0 + 4 * i for i in range(1, 13)}


def _late_event(tmp_path, when, *, flip=False, seed=5):
    # Labelled week 0 but played at `when` (TBA weeks aren't playing order).
    rng = random.Random(seed)
    matches = []
    for i in range(30):
        teams = rng.sample(sorted(STRENGTH), 6)
        red, blue = tuple(teams[:3]), tuple(teams[3:])
        rs, bs = int(sum(STRENGTH[t] for t in red)), int(sum(STRENGTH[t] for t in blue))
        if flip:
            rs, bs = bs + 40, max(rs - 40, 0)
        matches.append(_match(f"2025late_qm{i}", "qm", when + 100 * i, red, blue, rs, bs))
    _write_event(tmp_path, 2025, "2025late", matches, week=0)


def _league(tmp_path, *, n_weeks=4, per_week=50, noise=12.0, seed=2, flip_from=None, late=None):
    # Twelve teams with fixed strengths, two events a week. flip_from=k swaps every
    # result from week k on (same schedule, different outcomes). late=(when, flip) adds a
    # week-0-labelled event played at `when`.
    if late is not None:
        _late_event(tmp_path, late[0], flip=late[1])
    rng = random.Random(seed)
    for week in range(n_weeks):
        for e in range(2):
            matches = []
            for i in range(per_week):
                teams = rng.sample(sorted(STRENGTH), 6)
                red, blue = tuple(teams[:3]), tuple(teams[3:])
                rs = max(int(sum(STRENGTH[t] for t in red) + rng.gauss(0, noise)), 0)
                bs = max(int(sum(STRENGTH[t] for t in blue) + rng.gauss(0, noise)), 0)
                if flip_from is not None and week >= flip_from:
                    rs, bs = bs + 7, max(rs - 7, 0)
                when = 1_000_000 * week + 100_000 * e + 100 * i
                matches.append(_match(f"2025w{week}e{e}_qm{i}", "qm", when, red, blue, rs, bs))
            _write_event(tmp_path, 2025, f"2025w{week}e{e}", matches, week=week, teams=("frc1",))
    return load_cached_season(tmp_path, 2025)


def test_filter_matches_the_phase1_stand_in(tmp_path):
    research = _league(tmp_path)
    z = {"frc3": 0.4, "frc7": -0.6}
    rookies = {"frc1"}
    old = ScaleFreeTotalModel(start_multipliers(z, rookies), 50.0, 64.0)
    new = ScaleFreeFilter(ScaleFreeParams.from_points(TeamModelParams().total, 64.0), z_previous=z, rookies=rookies, previous_unit=50.0)
    for a, b in zip(collect_rows(research, old), collect_rows(research, new)):
        assert a.prediction.red_mean == pytest.approx(b.prediction.red_mean, rel=1e-9)
        assert a.prediction.blue_var == pytest.approx(b.prediction.blue_var, rel=1e-9)


def test_level_terms_are_off_by_default_and_equal_at_the_mean():
    plain = ScaleFreeFilter(ScaleFreeParams(noise=0.8))
    assert plain.noise_var(0.5) == plain.noise_var(9.0) == pytest.approx(0.64)
    level = ScaleFreeFilter(ScaleFreeParams(noise=0.5, noise_count=0.1, noise_prop=0.2))
    assert level.noise_var(3.0) == pytest.approx(0.25 + 0.3 + 0.04 * 9)
    assert level.noise_var(-1.0) == pytest.approx(0.25)
    drift = ScaleFreeFilter(ScaleFreeParams(drift_level=True))
    assert drift._drift(0.3, 1.0) == pytest.approx(0.09) and drift._drift(0.3, 3.0) == pytest.approx(0.45)


def test_student_t_approaches_the_normal_and_discounts_outliers():
    nu = np.array([0.1, -0.5, 1.2])
    s = np.array([1.0, 2.0, 0.5])
    assert loglik(nu, s, df=1e6) == pytest.approx(loglik(nu, s), rel=1e-4)
    far = np.array([8.0])
    assert loglik(far, np.ones(1), df=5.0) > loglik(far, np.ones(1))


def test_nelder_mead_finds_a_minimum():
    x, value, _, converged = nelder_mead(lambda v: (v[0] - 1.5) ** 2 + 3 * (v[1] + 0.5) ** 2, np.zeros(2), ftol=1e-10, xtol=1e-6, max_evals=2000)
    assert x == pytest.approx([1.5, -0.5], abs=1e-3) and value < 1e-6 and converged
    _, _, evals, converged = nelder_mead(lambda v: float(np.sum(v * v)), np.ones(4), ftol=1e-12, xtol=1e-9, max_evals=20)
    assert not converged and evals <= 25


def test_likelihood_fit_recovers_the_match_noise(tmp_path):
    inputs = season_inputs(_league(tmp_path, n_weeks=3, per_week=80, noise=12.0), None)
    out = fit_likelihood(inputs, ScaleFreeParams(), ("prior", "noise"), None)
    unit = inputs.model(ScaleFreeParams())
    for result in inputs.research.season.results:
        unit.observe(result)
    noise_points = out["params"].noise * unit.unit.value()
    # The unit drifts a little through the season, and integer scores add a bit.
    assert 9.0 < noise_points < 16.0, noise_points


def test_a_week_s_settings_and_forecasts_ignore_that_week_and_later(tmp_path):
    k = 1
    base = season_inputs(_league(tmp_path / "a"), None)
    flipped = season_inputs(_league(tmp_path / "b", flip_from=k), None)
    pull = Pull.around(ScaleFreeParams(), BASE)

    def run(inputs, method):
        plan = as_of_plan(inputs, ScaleFreeParams(), BASE, method=method, pull=pull)
        rows = collect_rows(inputs.research, ScheduledModel(inputs, [(p["from_time"], p["params"]) for p in plan]))
        return plan, rows

    week_k = min(t for t, w in zip(base.times, base.weeks) if w == k)
    for method in ("map", "brier"):
        plan_a, rows_a = run(base, method)
        plan_b, rows_b = run(flipped, method)
        # Every fit used from week k's start or before saw the same (earlier) matches.
        assert [p["params"] for p in plan_a if p["from_time"] <= week_k] == [p["params"] for p in plan_b if p["from_time"] <= week_k]
        before = [(a.prediction, b.prediction) for a, b in zip(rows_a, rows_b) if a.week < k]
        assert before and all(pa == pb for pa, pb in before)
        first_k = next(i for i, r in enumerate(rows_a) if r.week == k)
        assert rows_a[first_k].prediction == rows_b[first_k].prediction
        # ...and the flip did reach later forecasts, so the check isn't vacuous.
        assert any(a.prediction != b.prediction for a, b in zip(rows_a, rows_b) if a.week > k)
        assert any(p["params"] != q["params"] for p, q in zip(plan_a, plan_b) if p["from_time"] > week_k)


def test_a_late_event_labelled_early_never_reaches_an_earlier_fit_or_scale(tmp_path):
    # A "week 0" event played after week 1 has started: flipping its results must leave
    # the fits made before it, every forecast before it and those weeks' probabilities
    # untouched, and must reach the next fit.
    when = 1_000_000 + 500_000
    # 100 matches a week: enough for week 1's logistic scale to be fitted, not defaulted.
    base = season_inputs(_league(tmp_path / "a", per_week=100, late=(when, False)), None)
    flipped = season_inputs(_league(tmp_path / "b", per_week=100, late=(when, True)), None)
    pull = Pull.around(ScaleFreeParams(), BASE)
    out = []
    for inputs in (base, flipped):
        plan = as_of_plan(inputs, ScaleFreeParams(), BASE, method="map", pull=pull)
        rows = collect_rows(inputs.research, ScheduledModel(inputs, [(p["from_time"], p["params"]) for p in plan]))
        probs, _, _ = walk_forward_probs(rows)
        out.append((plan, rows, probs))
    (plan_a, rows_a, probs_a), (plan_b, rows_b, probs_b) = out
    assert [p["params"] for p in plan_a if p["from_time"] <= when] == [p["params"] for p in plan_b if p["from_time"] <= when]
    before = [i for i, r in enumerate(rows_a) if r.time < when]
    assert before and all(rows_a[i].prediction == rows_b[i].prediction for i in before)
    # Weeks 0-1 get their scale from matches before week 1 started.
    same = [i for i in before if rows_a[i].week <= 1]
    assert any(rows_a[i].week == 1 for i in same)
    assert all(probs_a[i] == probs_b[i] for i in same)
    later = [p for p in plan_a if p["from_time"] > when]
    assert later and later[0]["params"] != [p for p in plan_b if p["from_time"] > when][0]["params"]


def test_margin_variance_counts_the_covariance_between_alliances(tmp_path):
    research = _league(tmp_path, n_weeks=1, per_week=40)
    model = ScaleFreeFilter(ScaleFreeParams())
    results = research.season.results
    for result in results[:-1]:
        model.predict(result.card)
        model.observe(result)
    card = results[-1].card
    p = model.predict(card)
    block = model.blocks[card.event_key]
    h = np.zeros(block.n)
    for t in card.red:
        h[block.index[t]] += 1.0
    for t in card.blue:
        h[block.index[t]] -= 1.0
    u = model.unit.value()
    expected = (h @ block.cov[: block.n, : block.n] @ h + 2 * model.noise_var(0.0)) * u * u
    assert p.margin_var == pytest.approx(expected, rel=1e-9)
    assert p.margin_var != pytest.approx(p.red_var + p.blue_var, rel=1e-3)


def test_unit_never_divides_by_zero_after_opening_shutouts(tmp_path):
    from tests.test_research_harness import R, B

    matches = [_match(f"2025z_qm{i}", "qm", 1000 + i, R, B, 0, 0) for i in range(3)] + [_match("2025z_qm9", "qm", 2000, R, B, 30, 20)]
    _write_event(tmp_path, 2025, "2025z", matches)
    model = ScaleFreeFilter(ScaleFreeParams())
    for result in load_cached_season(tmp_path, 2025).season.results:
        model.predict(result.card)
        model.observe(result)
    assert model.unit.value() > 0


def test_ties_are_kept_for_scores_and_left_out_of_win_fits(tmp_path):
    research = _league(tmp_path, n_weeks=3)
    rows = collect_rows(research, ScaleFreeFilter(ScaleFreeParams()), keep_ties=True)
    rows[5].y = 0.5
    probs, _, _ = walk_forward_probs(rows)
    rows[5].y = 1.0
    probs_won, _, _ = walk_forward_probs(rows)
    assert [p for i, p in enumerate(probs) if rows[i].week > rows[5].week] != [p for i, p in enumerate(probs_won) if rows[i].week > rows[5].week]


def test_innovations_stop_at_the_week_asked(tmp_path):
    inputs = season_inputs(_league(tmp_path), None)
    week1 = min(t for t, w in zip(inputs.times, inputs.weeks) if w == 1)
    nu1, _ = innovations(inputs, ScaleFreeParams(), week1)
    nu_all, _ = innovations(inputs, ScaleFreeParams(), None)
    week0 = sum(1 for w in inputs.weeks if w < 1)
    assert len(nu1) == 2 * week0 and len(nu_all) == 2 * len(inputs.weeks)
    assert np.allclose(nu1, nu_all[: len(nu1)])
    assert math.isfinite(loglik(nu_all, np.ones(len(nu_all))))


def test_a_margin_model_s_first_week_probabilities_ignore_later_results(tmp_path):
    from app.services.ratings.research_models import EpaClone

    # Week 0 has no earlier matches, so each match falls back to a default scale; that
    # default must come from the match's own prediction, not from later ones.
    research = _league(tmp_path, n_weeks=1, per_week=120)
    results = research.season.results
    half = len(results) // 2
    swapped = [replace(r, red_points=r.blue_points, blue_points=r.red_points, red_score=r.blue_score, blue_score=r.red_score) if i >= half else r for i, r in enumerate(results)]
    flipped = replace(research, season=replace(research.season, results=swapped))
    a, _, _ = walk_forward_probs(collect_rows(research, EpaClone({}, 50.0)))
    b, _, _ = walk_forward_probs(collect_rows(flipped, EpaClone({}, 50.0)))
    rows = collect_rows(research, EpaClone({}, 50.0))
    early = [i for i, r in enumerate(rows) if r.time < results[half].card.time]
    assert early and all(a[i] == b[i] for i in early)
