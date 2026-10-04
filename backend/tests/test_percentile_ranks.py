import unittest

from app.services.ratings.helpers import _percentile_map
from app.services.utils import percentile_ranks


class PercentileRanksTests(unittest.TestCase):
    def test_tied_values_share_one_percentile(self):
        ranks = percentile_ranks({"frc1": 0.0, "frc2": 0.0, "frc3": 0.0, "frc4": 0.0, "frc5": 1.0})
        self.assertEqual({ranks[k] for k in ("frc1", "frc2", "frc3", "frc4")}, {37.5})
        self.assertEqual(ranks["frc5"], 100.0)

    def test_result_does_not_depend_on_input_order(self):
        forward = percentile_ranks({"a": 0.0, "b": 0.0, "c": 2.0, "d": 0.0})
        backward = percentile_ranks({"d": 0.0, "c": 2.0, "b": 0.0, "a": 0.0})
        self.assertEqual(forward, backward)

    def test_distinct_values_keep_their_spread(self):
        self.assertEqual(percentile_ranks({"a": 1.0, "b": 2.0, "c": 3.0}), {"a": 0.0, "b": 50.0, "c": 100.0})

    def test_missing_values_get_the_default_and_lower_is_better_flips(self):
        ranks = percentile_ranks({"a": 1.0, "b": 2.0, "c": None, "d": float("nan")}, higher_is_better=False)
        self.assertEqual(ranks, {"a": 100.0, "b": 0.0, "c": 50.0, "d": 50.0})

    def test_single_value_is_the_middle(self):
        self.assertEqual(percentile_ranks({"a": 7.0}), {"a": 50.0})

    def test_ratings_map_uses_the_tie_aware_ranks(self):
        self.assertEqual(_percentile_map({"a": 0.0, "b": 0.0}), {"a": 50.0, "b": 50.0})


if __name__ == "__main__":
    unittest.main()
