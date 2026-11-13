import json
import math
import random

import numpy as np
import pytest

from app.services.ratings.replay import MatchCard, MatchResult, Prediction
from app.services.ratings.research_data import load_cached_season
from app.services.ratings.research_eval import (
    collect_rows,
    paired_cluster_bootstrap,
    per_match_brier,
    walk_forward_probs,
)
from app.services.ratings.research_models import EpaClone, RidgePriorOpr, ScaleFreeTotalModel


def _alliance(teams, score, *, dq=(), surrogate=()):
    return {"team_keys": list(teams), "score": score, "dq_team_keys": list(dq), "surrogate_team_keys": list(surrogate)}


def _match(key, level, when, red, blue, red_score, blue_score, *, red_dq=(), surrogate=(), foul=(0, 0), winner=None):
    played = red_score >= 0
    return {
        "key": key,
        "comp_level": level,
        "actual_time": when,
        "winning_alliance": winner if winner is not None else ("red" if red_score > blue_score else "blue" if blue_score > red_score else ""),
        "alliances": {"red": _alliance(red, red_score, dq=red_dq, surrogate=surrogate), "blue": _alliance(blue, blue_score)},
        "score_breakdown": {
            "red": {"foulPoints": foul[0], "autoPoints": 10, "teleopPoints": max(red_score - foul[0] - 10, 0), "rp": 2},
            "blue": {"foulPoints": foul[1], "autoPoints": 8, "teleopPoints": max(blue_score - foul[1] - 8, 0), "rp": 1},
        } if played else None,
    }


def _write_event(root, year, key, matches, *, week=0, event_type=0, teams=()):
    folder = root / str(year)
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"event": {"key": key, "event_type": event_type, "week": week, "year": year}, "teams": [{"key": t, "rookie_year": year} for t in teams], "matches": matches}
    (folder / f"{key}.json").write_text(json.dumps(payload))


R = ("frc1", "frc2", "frc3")
B = ("frc4", "frc5", "frc6")


def test_loader_handles_dq_surrogate_tie_unplayed_and_replay(tmp_path):
    matches = [
        _match("2025x_qm1", "qm", 1000, R, B, 100, 80, foul=(5, 0)),
        _match("2025x_qm2", "qm", 1100, R, B, 90, 90),  # a tie is kept
        _match("2025x_qm3", "qm", 1200, R, B, 70, 95, red_dq=("frc2",)),  # a DQ'd alliance explains nothing
        _match("2025x_qm3_replay", "qm", 1300, R, B, 75, 60),  # a replay is its own match
        _match("2025x_qm4", "qm", 1400, R, B, -1, -1),  # not played
        _match("2025x_qm5", "qm", 1500, R, ("frc4", "frc5"), 50, 40),  # two-robot side: skipped
        _match("2025x_qm6", "qm", 1250, R, B, 60, 50, surrogate=("frc3",)),
    ]
    _write_event(tmp_path, 2025, "2025x", matches, teams=("frc1",))
    research = load_cached_season(tmp_path, 2025)
    keys = [r.card.match_key for r in research.season.results]
    assert keys == ["2025x_qm1", "2025x_qm2", "2025x_qm3", "2025x_qm6", "2025x_qm3_replay"]  # by time
    assert research.skipped == {"unplayed": 1, "not_three_teams": 1}
    by_key = {r.card.match_key: r for r in research.season.results}
    # Points exclude fouls received.
    assert by_key["2025x_qm1"].red_points == 95.0
    assert by_key["2025x_qm1"].red_components["total_points"] == 95.0
    assert by_key["2025x_qm1"].red_components["auto_points"] == 10.0
    # A DQ'd side has no points to learn from; its score still decides the winner.
    assert by_key["2025x_qm3"].red_points is None and by_key["2025x_qm3"].blue_points == 95.0
    assert by_key["2025x_qm3"].red_score == 70
    assert by_key["2025x_qm6"].red_components["surrogate_team_keys"] == ["frc3"]
    assert by_key["2025x_qm2"].red_score == by_key["2025x_qm2"].blue_score
    assert research.rookie_year == {"frc1": 2025}
    assert all(r.card.event_start == 1000 for r in research.season.results)


def test_loader_can_keep_dq_alliances(tmp_path):
    _write_event(tmp_path, 2025, "2025x", [_match("2025x_qm1", "qm", 1000, R, B, 70, 95, red_dq=("frc2",))])
    assert load_cached_season(tmp_path, 2025, exclude_dq=False).season.results[0].red_points == 70.0


def test_loader_missing_season_says_how_to_fetch(tmp_path):
    with pytest.raises(FileNotFoundError, match="fetch_tba_season"):
        load_cached_season(tmp_path, 2030)


def _season(tmp_path, n_weeks=4, per_week=60, seed=1):
    # A small league: six teams with fixed strengths, one event a week.
    rng = random.Random(seed)
    strength = {f"frc{i}": 20.0 + 8 * i for i in range(1, 7)}
    for week in range(n_weeks):
        matches = []
        for i in range(per_week):
            teams = rng.sample(sorted(strength), 6)
            red, blue = tuple(teams[:3]), tuple(teams[3:])
            rs = int(sum(strength[t] for t in red) + rng.gauss(0, 15))
            bs = int(sum(strength[t] for t in blue) + rng.gauss(0, 15))
            matches.append(_match(f"2025w{week}_qm{i}", "qm", 1_000_000 * week + 100 * i, red, blue, max(rs, 0), max(bs, 0)))
        _write_event(tmp_path, 2025, f"2025w{week}", matches, week=week)
    return load_cached_season(tmp_path, 2025)


class _Strict:
    # Raises if a result is observed before its match was predicted, or twice.
    name = "strict"

    def __init__(self):
        self.predicted: list[str] = []
        self.observed: list[str] = []

    def predict(self, card):
        assert card.match_key not in self.observed, "predicted a match after seeing its result"
        self.predicted.append(card.match_key)
        return Prediction(60.0, 55.0, 100.0, 100.0)

    def observe(self, result):
        assert result.card.match_key in self.predicted, "observe() before predict()"
        assert result.card.match_key not in self.observed
        self.observed.append(result.card.match_key)

    def team_points(self, team_key, event_key):
        return 0.0


def test_no_match_is_scored_by_a_model_that_saw_it(tmp_path):
    research = _season(tmp_path)
    model = _Strict()
    rows = collect_rows(research, model)
    assert model.predicted == model.observed
    assert len(rows) == len(model.predicted) - sum(1 for r in research.season.results if r.red_score == r.blue_score)


def test_probability_scale_for_a_week_ignores_that_week_and_later(tmp_path):
    research = _season(tmp_path)
    rows = collect_rows(research, _Strict())
    probs, _, _ = walk_forward_probs(rows)
    # Flip every outcome from week 2 on: weeks 0-2 must keep exactly the same forecasts.
    for r in rows:
        if r.week >= 2:
            r.y = 1.0 - r.y
    flipped, _, scales = walk_forward_probs(rows)
    early = [i for i, r in enumerate(rows) if r.week <= 2]
    assert [probs[i] for i in early] == [flipped[i] for i in early]
    assert scales[0]["fitted_on"] == 0 and scales[2]["fitted_on"] > 0
    assert any(probs[i] != flipped[i] for i, r in enumerate(rows) if r.week == 3)


def test_baseline_models_are_as_of_and_learn_the_league(tmp_path):
    research = _season(tmp_path)
    reference = 70.0
    for model in (
        ScaleFreeTotalModel({}, 70.0, reference),
        EpaClone({}, 70.0),
        RidgePriorOpr({}, 70.0),
    ):
        rows = collect_rows(research, model)
        probs, _, _ = walk_forward_probs(rows)
        brier = sum((p - r.y) ** 2 for p, r in zip(probs, rows)) / len(rows)
        assert brier < 0.2, (model.name, brier)  # a coin flip scores 0.25
        late = [r for r in rows if r.week == 3]
        assert all(r.prediction.red_mean == r.prediction.red_mean for r in late)


def test_ridge_prior_only_moves_after_a_result():
    model = RidgePriorOpr({}, 70.0)
    card = MatchCard("m1", "e", 1, "qm", 1, R, B)
    first = model.predict(card)
    assert first == model.predict(card)
    model.observe(MatchResult(card, 400, 10, 400.0, 10.0))
    assert model.predict(card).red_mean > first.red_mean


def test_cluster_bootstrap_recovers_a_known_difference():
    a, b = {}, {}
    for e in range(40):
        for m in range(20):
            key = f"e{e}_{m}"
            a[key] = (f"e{e}", 0.20)
            b[key] = (f"e{e}", 0.23)
    out = paired_cluster_bootstrap(a, b, n=500, seed=3)
    assert out["mean_diff"] == pytest.approx(-0.03)
    assert out["lo90"] == pytest.approx(-0.03) and out["hi90"] == pytest.approx(-0.03)
    assert out["events"] == 40 and out["n"] == 800


def test_cluster_bootstrap_of_identical_inputs_brackets_zero():
    rng = np.random.default_rng(0)
    a = {f"e{e}_{m}": (f"e{e}", float(rng.random())) for e in range(30) for m in range(15)}
    out = paired_cluster_bootstrap(a, dict(a), n=300)
    assert out["mean_diff"] == 0.0 and out["lo90"] == 0.0 and out["hi90"] == 0.0
    noisy = {k: (e, v + float(rng.normal(0, 0.1))) for k, (e, v) in a.items()}
    out = paired_cluster_bootstrap(a, noisy, n=500)
    assert out["lo90"] < 0 < out["hi90"] or abs(out["mean_diff"]) < 0.02


def test_per_match_brier_keys_events_for_the_bootstrap(tmp_path):
    rows = collect_rows(_season(tmp_path, n_weeks=2, per_week=10), _Strict())
    losses = per_match_brier(rows, [0.5] * len(rows))
    assert all(math.isclose(v, 0.25) for _, v in losses.values())
    assert {e for e, _ in losses.values()} == {"2025w0", "2025w1"}
