import random
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

from app.api import routes_alliances
from app.api.routes_alliances import (
    _build_model_rank_map,
    _simulate_alliance_selection,
    _softmax_probabilities,
)


def _reference_simulation(*, event_team_keys, captain_team_keys, desirability_by_team, scale, simulations):
    # The simulation as it was before 2026-10-30: one softmax + weighted draw per pick.
    non_captains = [k for k in event_team_keys if k not in set(captain_team_keys)]
    first = {k: 0 for k in event_team_keys}
    second = {k: 0 for k in event_team_keys}
    rng = random.Random(118)
    for _ in range(simulations):
        available = set(non_captains)
        for counts in [first] * len(captain_team_keys) + [second] * len(captain_team_keys):
            if not available:
                break
            probs = _softmax_probabilities(desirability_by_team, sorted(available), scale)
            threshold, cumulative, selected = rng.random(), 0.0, None
            for row in probs:
                cumulative += float(row["probability_0_1"])
                if cumulative >= threshold:
                    selected = str(row["team_key"])
                    break
            selected = selected or str(probs[-1]["team_key"])
            counts[selected] += 1
            available.remove(selected)
    captains = set(captain_team_keys)
    return (
        {k: c / float(simulations) for k, c in first.items() if k not in captains},
        {k: c / float(simulations) for k, c in second.items() if k not in captains},
        {},
    )


class AllianceSelectionModelTests(unittest.TestCase):
    def test_softmax_probabilities_rank_order_and_normalization(self):
        desirability = {"frc1": 1900.0, "frc2": 1800.0, "frc3": 1700.0}
        rows = _softmax_probabilities(desirability, ["frc1", "frc2", "frc3"], scale=35.0)
        self.assertEqual(rows[0]["team_key"], "frc1")
        self.assertGreater(float(rows[0]["probability_0_1"]), float(rows[1]["probability_0_1"]))
        self.assertGreater(float(rows[1]["probability_0_1"]), float(rows[2]["probability_0_1"]))
        total = sum(float(row["probability_0_1"]) for row in rows)
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_build_model_rank_map_prefers_higher_rating(self):
        ratings = {
            "frc1": SimpleNamespace(rating_0_100=60.0),
            "frc2": SimpleNamespace(rating_0_100=92.0),
            "frc3": SimpleNamespace(rating_0_100=75.0),
        }
        rank_map = _build_model_rank_map(["frc1", "frc2", "frc3"], ratings)  # type: ignore[arg-type]
        self.assertEqual(rank_map["frc2"], 1)
        self.assertEqual(rank_map["frc3"], 2)
        self.assertEqual(rank_map["frc1"], 3)


    def test_simulation_prefers_higher_desirability_team(self):
        event_team_keys = ["frc1", "frc2", "frc3", "frc4", "frc5"]
        captain_team_keys = ["frc1", "frc2"]
        desirability = {
            "frc1": 2100.0,
            "frc2": 2050.0,
            "frc3": 2000.0,
            "frc4": 1800.0,
            "frc5": 1700.0,
        }
        first_round, second_round, _ = _simulate_alliance_selection(
            event_team_keys=event_team_keys,
            captain_team_keys=captain_team_keys,
            desirability_by_team=desirability,
            scale=35.0,
            simulations=400,
        )
        self.assertIn("frc3", first_round)
        self.assertIn("frc4", first_round)
        self.assertGreater(first_round["frc3"], first_round["frc4"])
        self.assertIn("frc3", second_round)

    def test_the_faster_simulation_gives_the_same_numbers(self):
        rng = random.Random(7)
        for case in range(60):
            keys = sorted({f"frc{rng.randint(1, 9999)}" for _ in range(rng.choice([6, 9, 24, 40, 60]))})
            desirability = {k: 1300 + 10 * rng.uniform(0, 100) - rng.choice([0, 1, 2.5]) * i for i, k in enumerate(keys)}
            if case % 5 == 0:
                for key in keys[:4]:
                    desirability[key] = 1500.0  # ties
            kwargs = dict(
                event_team_keys=keys,
                captain_team_keys=keys[: rng.randint(1, min(8, len(keys) - 1))],
                desirability_by_team=desirability,
                scale=rng.choice([1, 0.3, 35, 200, 1e-9]),
                simulations=rng.choice([50, 150]),
            )
            self.assertEqual(_simulate_alliance_selection(**kwargs), _reference_simulation(**kwargs), case)


class SelectionModelBoundsTests(unittest.TestCase):
    def setUp(self):
        routes_alliances._selection_cache.clear()
        self.calls = []

        def fake_payload(db, event_key, **kwargs):
            self.calls.append(kwargs)
            return {"ok": True, "selection_model": {"simulations": kwargs["simulations"]}}

        patcher = mock.patch.object(routes_alliances, "_selection_model_payload", side_effect=fake_payload)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.redis_counts: dict[str, int] = {}
        fake_redis = mock.Mock()
        fake_redis.incr.side_effect = lambda key: self.redis_counts.__setitem__(key, self.redis_counts.get(key, 0) + 1) or self.redis_counts[key]
        cache = mock.patch.object(routes_alliances, "get_cache", return_value=mock.Mock(redis_client=fake_redis))
        cache.start()
        self.addCleanup(cache.stop)

    def run_model(self, **overrides):
        params = dict(rank_weight=1.0, scale=35.0, captains=8, simulations=500, rank_source="auto", db=None)
        return routes_alliances.get_selection_model("2026txhou", **{**params, **overrides})

    def test_runs_are_capped_and_inputs_rounded(self):
        self.assertEqual(self.run_model(simulations=5000)["selection_model"]["simulations"], 1000)
        self.run_model(rank_weight=1.04, scale=35.04, simulations=520)
        self.assertEqual(self.calls[-1], {"rank_weight": 1.0, "scale": 35.0, "captains": 8, "simulations": 500, "rank_source": "auto"})

    def test_a_repeat_is_answered_from_the_cache(self):
        self.run_model()
        self.run_model(scale=35.01)  # rounds to the same inputs
        self.assertEqual(len(self.calls), 1)

    def test_a_second_simulation_waits_then_is_turned_away(self):
        holder = threading.BoundedSemaphore(1)
        holder.acquire()
        with (
            mock.patch.object(routes_alliances, "_selection_gate", holder),
            mock.patch.object(routes_alliances, "_SELECTION_GATE_WAIT_SEC", 0.01),
        ):
            with self.assertRaises(HTTPException) as busy:
                self.run_model()
        self.assertEqual(busy.exception.status_code, 503)
        self.assertEqual(self.calls, [])

    def test_fresh_simulations_have_a_site_wide_budget_a_minute(self):
        with mock.patch.object(routes_alliances, "SIMULATIONS_PER_MINUTE", 3):
            for scale in (10.0, 20.0, 30.0):
                self.run_model(scale=scale)
            self.run_model(scale=10.0)  # answered from the cache: free
            with self.assertRaises(HTTPException) as busy:
                self.run_model(scale=40.0)
        self.assertEqual(busy.exception.status_code, 503)
        self.assertEqual(len(self.calls), 3)


if __name__ == "__main__":
    unittest.main()
