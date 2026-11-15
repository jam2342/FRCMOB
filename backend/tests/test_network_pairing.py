import copy
from datetime import datetime, timezone
import hashlib
import json
import pickle
import random

import numpy as np
import pytest

from app.services.ratings import network_pairing as pairing
from app.services.ratings.constants import season_baseline
from app.services.ratings.replay import MatchCard, MatchResult
from tests.test_weekly_rehearsal import artifact, data, fake_fitter  # noqa: F401

START = int(datetime(2026, 3, 5, tzinfo=timezone.utc).timestamp())


def factory():
    return pairing.production_factory(season_baseline(2026), {"frc1": .4, "frc2": -.3}, {"frc30": 2026})


def components(rng):
    teleop, auto = rng.uniform(20, 90), rng.uniform(0, 30)
    return {"teleop_hub_points": teleop, "auto_hub_points": auto, "total_points": teleop + auto,
            "robot_auto_tower_points": [rng.choice([0, 15]) for _ in range(3)],
            "robot_endgame_tower_points": [rng.choice([0, 10, 20]) for _ in range(3)]}


def results(n=240, seed=3, ties=()):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        event = f"2026e{i // 80}"
        teams = rng.sample([f"frc{t}" for t in range(1, 31)], 6)
        card = MatchCard(f"{event}_qm{i % 80 + 1}", event, START + 600 * i, "qm", START + 600 * (i - i % 80), tuple(teams[:3]), tuple(teams[3:]))
        red, blue = components(rng), components(rng)
        rs, bs = int(red["total_points"]), int(blue["total_points"])
        if i in ties:
            bs = rs
        out.append(MatchResult(card, rs, bs, red["total_points"], blue["total_points"], red_components=red, blue_components=blue))
    return out


def test_shadow_forecast_matches_deepcopy_and_leaves_model_unchanged():
    model, rows = factory()(), results(120)
    for r in rows[:100]:
        model.observe(r)
    before = pickle.dumps(model)
    for r in rows[100:]:
        expected = pairing.production_forecast(copy.deepcopy(model), r.card, {}, {})
        assert pairing.production_forecast(model, r.card, {}, {}) == expected
    assert pickle.dumps(model) == before


def test_immediate_replay_matches_mutating_production_order():
    rows = results(160)
    model, here, total, out = factory()(), {}, {}, []
    for r in rows:
        # Phase 3's replay: predict on the live model, then observe.
        from app.services.events.win_probability import red_win_prob
        from app.services.ratings.team_model_service import correction_features
        prediction = model.predict(r.card)
        teams = r.card.red + r.card.blue
        seen = min(here.get((t, r.card.event_key), 0) for t in teams)
        before = min(total.get(t, 0) - here.get((t, r.card.event_key), 0) for t in teams)
        x = correction_features(model, r.card, prediction, seen, before)
        margin = (prediction.red_mean + prediction.red_fouls) - (prediction.blue_mean + prediction.blue_fouls)
        sd = max((prediction.red_var or 0) + (prediction.blue_var or 0) + prediction.red_fouls_var + prediction.blue_fouls_var, 1e-6) ** .5
        out.append((red_win_prob(margin, sd, seen, playoff=False), x))
        model.observe(r)
        pairing.count(here, total, r.card)
    assert [(b, x) for _, b, x in pairing.immediate(rows, factory())] == out


def test_published_replay_rewinds_late_results_to_a_fresh_playing_order_state(monkeypatch):
    monkeypatch.setattr(pairing, "SNAPSHOT_EVERY", 7)
    monkeypatch.setattr(pairing, "SNAPSHOT_KEEP", 3)
    rows = results(200)
    posted = {r.card.match_key: r.card.time + 120 for r in rows}
    posted[rows[50].card.match_key] = rows[60].card.time + 30      # rewinds to a snapshot
    posted[rows[3].card.match_key] = rows[190].card.time + 30      # older than every snapshot
    posted[rows[120].card.match_key] = None                        # never published
    replay = pairing.PublishedReplay(rows, posted, factory())
    for r in rows[:-1]:
        replay.advance(r.card.time)
        replay.forecast(r.card)
    target = rows[-1].card
    replay.advance(target.time)
    assert replay.rewinds >= 2
    known = [r for r in rows if posted[r.card.match_key] is not None and posted[r.card.match_key] < target.time]
    model, here, total = factory()(), {}, {}
    for r in sorted(known, key=lambda r: (r.card.time, r.card.match_key)):
        model.observe(r)
        pairing.count(here, total, r.card)
    assert replay.forecast(target) == pairing.production_forecast(model, target, here, total)
    assert rows[120].card.match_key not in {r.card.match_key for r in replay.ordered}


def test_networks_use_only_labels_posted_before_each_job_and_apply_strictly_after_publication():
    rows = results(400, ties={5, 77})
    records = [{"result": r, "posted": r.card.time + 200, "type": 0} for r in rows]
    records[10]["posted"] = None
    production = {r.card.match_key: r for r in rows}
    posted = {r["result"].card.match_key: r["posted"] for r in records}
    seen = []

    def fit(labelled):
        seen.append(labelled)
        return {"0.weight": [[0.0] * 14] * 16, "0.bias": [0.0] * 16, "2.weight": [[float(len(labelled))] * 16], "2.bias": [0.0]}
    networks = pairing.train_networks(records, production, factory(), fit=fit)
    assert networks and all(n["publication"] - n["job"] == 3600 for n in networks)
    for n in networks:
        keys = {r.card.match_key for r in rows if posted[r.card.match_key] is not None and posted[r.card.match_key] < n["job"] and r.red_score != r.blue_score}
        assert n["labels"] == len(keys) and ("state" in n) == (len(keys) >= pairing.POLICY["minimum_network_labels"])
    for labelled, n in zip(seen, [n for n in networks if "state" in n]):
        assert all(posted[r["key"]] < n["job"] and r["y"] != .5 for r in labelled)
        assert rows[10].card.match_key not in {r["key"] for r in labelled}
    fitted = [n for n in networks if "state" in n]
    assert pairing.in_effect(networks, fitted[0]["publication"]) is None
    assert pairing.in_effect(networks, fitted[0]["publication"] + 1) is fitted[0]


def test_static_network_cannot_see_the_evaluation_window():
    frames = [{"key": f"2026e{i}_qm1", "event": f"2026e{i}", "time": START + 86400 * i, "forecasts": {"active": {"version": 1}}} for i in range(10)]
    first = frames[-2]["time"]
    clean = {"publication": first - 10, "state": {}, "latest_label_posted": first - 100, "weights_sha256": "x"}
    assert pairing.static_versions(frames, [clean])[1:] == (1, clean)
    leaked = dict(clean, latest_label_posted=first)
    with pytest.raises(ValueError, match="evaluation window"):
        pairing.static_versions(frames, [leaked])


def test_network_forward_pass_matches_production_correction(monkeypatch):
    from app.services.ratings import win_correction
    from app.services.ratings.win_correction import _WEIGHTS, corrected

    monkeypatch.setattr(win_correction, "CORRECTION_BLEND", 1.0)  # production's value; conftest turns it off

    state = json.loads((_WEIGHTS / "win_correction_2026.json").read_text())["state"]
    rng = np.random.default_rng(0)
    for _ in range(50):
        x = rng.normal(size=14).tolist()
        assert pairing.network_probability(state, x) == pytest.approx(corrected(2026, .3, x), abs=1e-15)


def test_paired_comparison_uses_the_rehearsal_win_rule_and_identical_matches():
    rows = results(6, ties={2})
    rows[4] = MatchResult(rows[4].card, 50, 40, None, 40.0)
    table = [{"key": r.card.match_key, "event": r.card.event_key, "y": pairing.label(r), "eligibility": pairing.eligibility(r),
              "a": .6, "b": .5} for r in rows]
    assert [t["eligibility"] for t in table].count("eligible") == 4
    assert table[2]["eligibility"] == "tie" and table[4]["eligibility"] == "unexplained_points"
    out = pairing.compare(table, "a", "b", "brier")
    assert out["n"] == 4
    table[0]["b"] = None
    with pytest.raises(ValueError, match="identical"):
        pairing.compare(table, "a", "b", "brier")


def test_frozen_candidate_reproduces_saved_forecasts_from_a_read_only_source(tmp_path, fake_fitter):  # noqa: F811
    from app.services.ratings import weekly_rehearsal as w

    root = tmp_path / "source"
    current = data(2024)
    w.rehearse(root, current, data(2023, 1), artifact(), "p" * 64, "test")
    before = hashlib.sha256((root / "scratch.sqlite").read_bytes()).hexdigest()
    _, frames = pairing.load_source(root)
    versions = sorted({f["forecasts"]["active"]["version"] for f in frames})
    assert len(versions) > 1
    for version in versions:
        chosen = [f for f in frames if f["forecasts"]["active"]["version"] == version]
        frozen, sha = pairing.frozen_candidate(root, current, frames, version, [f["key"] for f in chosen])
        assert sha == chosen[0]["forecasts"]["active"]["params_sha256"]
        assert all(frozen[f["key"]]["p"] == f["forecasts"]["active"]["p"] for f in chosen)
    assert hashlib.sha256((root / "scratch.sqlite").read_bytes()).hexdigest() == before


def test_production_trainer_is_deterministic_and_rounded_like_its_weight_files():
    pytest.importorskip("torch")
    rng = np.random.default_rng(1)
    rows = [{"x": rng.normal(size=14).tolist(), "y": float(rng.random() < .5)} for _ in range(200)]
    first, second = pairing.fit_network(rows), pairing.fit_network(rows)
    assert first == second and set(first) == {"0.weight", "0.bias", "2.weight", "2.bias"}
    # Same float32-rounded values the trainer writes into win_correction_<season>.json.
    assert all(v == float(np.float32(round(v, 6))) for v in np.ravel(first["0.weight"]))
