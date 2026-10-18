import unittest
from types import SimpleNamespace

from app.api.routes_alliances import (
    _build_model_rank_map,
    _simulate_alliance_selection,
    _softmax_probabilities,
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


if __name__ == "__main__":
    unittest.main()
