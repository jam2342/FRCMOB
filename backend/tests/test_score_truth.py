# Tests for app.services.scoring.truth – score-breakdown truth extraction helpers.

import unittest

from app.services.scoring.truth import (
    DEFAULT_TBA_PHASES,
    DEFAULT_TBA_SCORING_POINTS,
    REBUILT_HUB_SCORE_GRACE_SEC,
    _coalesce_float,
    _first_non_null,
    _is_yes,
    _rebuilt_active_hub_duration_sec,
    _reefscape_endgame_points_2025,
    _share_evenly,
    _share_weighted,
    _sum_dict_keys,
    _sum_optional,
    _tower_points_from_status,
)

class CoalesceFloatTests(unittest.TestCase):
    def test_first_non_none(self):
        self.assertAlmostEqual(_coalesce_float(None, 3.0, 5.0), 3.0)

    def test_all_none(self):
        self.assertIsNone(_coalesce_float(None, None))

    def test_single_value(self):
        self.assertAlmostEqual(_coalesce_float(7.0), 7.0)

    def test_empty(self):
        self.assertIsNone(_coalesce_float())

    def test_zero_is_valid(self):
        self.assertAlmostEqual(_coalesce_float(None, 0.0, 5.0), 0.0)

class SumOptionalTests(unittest.TestCase):
    def test_all_none_returns_none(self):
        self.assertIsNone(_sum_optional(None, None))

    def test_some_none_ignored(self):
        self.assertAlmostEqual(_sum_optional(1.0, None, 2.0), 3.0)

    def test_empty(self):
        self.assertIsNone(_sum_optional())

    def test_single_value(self):
        self.assertAlmostEqual(_sum_optional(5.0), 5.0)

class SumDictKeysTests(unittest.TestCase):
    def test_valid_keys(self):
        data = {"a": 1, "b": 2, "c": 3}
        self.assertAlmostEqual(_sum_dict_keys(data, ["a", "b"]), 3.0)

    def test_missing_keys_ignored(self):
        data = {"a": 1}
        self.assertAlmostEqual(_sum_dict_keys(data, ["a", "z"]), 1.0)

    def test_all_missing(self):
        self.assertIsNone(_sum_dict_keys({}, ["a", "b"]))

    def test_non_numeric_values_skipped(self):
        data = {"a": "hello", "b": 5}
        self.assertAlmostEqual(_sum_dict_keys(data, ["a", "b"]), 5.0)

class ShareEvenlyTests(unittest.TestCase):
    def test_normal_split(self):
        result = _share_evenly(12.0, 3)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0], 4.0)

    def test_none_value(self):
        result = _share_evenly(None, 3)
        self.assertEqual(result, [0.0, 0.0, 0.0])

    def test_zero_slots(self):
        result = _share_evenly(10.0, 0)
        self.assertEqual(result, [])

    def test_negative_slots(self):
        result = _share_evenly(10.0, -1)
        self.assertEqual(result, [])

class ShareWeightedTests(unittest.TestCase):
    def test_equal_weights(self):
        result = _share_weighted(12.0, ["a", "b", "c"], {"a": 1.0, "b": 1.0, "c": 1.0})
        self.assertEqual(len(result), 3)
        for v in result:
            self.assertAlmostEqual(v, 4.0)

    def test_unequal_weights(self):
        result = _share_weighted(10.0, ["a", "b"], {"a": 3.0, "b": 1.0})
        self.assertAlmostEqual(result[0], 7.5)
        self.assertAlmostEqual(result[1], 2.5)

    def test_none_value(self):
        result = _share_weighted(None, ["a", "b"], {"a": 1.0, "b": 1.0})
        self.assertEqual(result, [0.0, 0.0])

    def test_no_weights_dict(self):
        result = _share_weighted(10.0, ["a", "b"], None)
        self.assertAlmostEqual(result[0], 5.0)
        self.assertAlmostEqual(result[1], 5.0)

    def test_empty_keys(self):
        result = _share_weighted(10.0, [], {})
        self.assertEqual(result, [])

class FirstNonNullTests(unittest.TestCase):
    def test_first_found(self):
        self.assertEqual(_first_non_null({"a": None, "b": 5, "c": 10}, ["a", "b", "c"]), 5)

    def test_all_none(self):
        self.assertIsNone(_first_non_null({"a": None}, ["a"]))

    def test_missing_key(self):
        self.assertIsNone(_first_non_null({}, ["a"]))

class IsYesTests(unittest.TestCase):
    def test_yes_variants(self):
        self.assertTrue(_is_yes("Yes"))
        self.assertTrue(_is_yes("yes"))
        self.assertTrue(_is_yes("YES"))

    def test_true_variants(self):
        self.assertTrue(_is_yes("true"))
        self.assertTrue(_is_yes("True"))

    def test_one(self):
        self.assertTrue(_is_yes("1"))

    def test_no(self):
        self.assertFalse(_is_yes("no"))
        self.assertFalse(_is_yes("false"))
        self.assertFalse(_is_yes(""))

    def test_none(self):
        self.assertFalse(_is_yes(None))

class TowerPointsTests(unittest.TestCase):
    def _pts(self, status, auto_phase=False):
        return _tower_points_from_status(
            status,
            auto_phase=auto_phase,
            scoring_points=DEFAULT_TBA_SCORING_POINTS,
        )

    def test_high_climb(self):
        self.assertAlmostEqual(self._pts("Level3"), 30.0)

    def test_mid_climb(self):
        self.assertAlmostEqual(self._pts("Level2"), 20.0)

    def test_low_climb_auto(self):
        self.assertAlmostEqual(self._pts("Level1", auto_phase=True), 15.0)

    def test_low_climb_teleop(self):
        self.assertAlmostEqual(self._pts("Level1", auto_phase=False), 10.0)

    def test_park(self):
        self.assertAlmostEqual(self._pts("Park"), 10.0 * 0.25)

    def test_none_string(self):
        self.assertAlmostEqual(self._pts("None"), 0.0)

    def test_empty(self):
        self.assertAlmostEqual(self._pts(""), 0.0)

    def test_deep_traverse(self):
        self.assertAlmostEqual(self._pts("DeepTraverse"), 30.0)

class ReefscapeEndgameTests(unittest.TestCase):
    def test_deep_cage(self):
        self.assertAlmostEqual(_reefscape_endgame_points_2025("DeepCage"), 12.0)

    def test_shallow_cage(self):
        self.assertAlmostEqual(_reefscape_endgame_points_2025("ShallowCage"), 6.0)

    def test_parked(self):
        self.assertAlmostEqual(_reefscape_endgame_points_2025("Parked"), 2.0)

    def test_empty(self):
        self.assertAlmostEqual(_reefscape_endgame_points_2025(""), 0.0)

    def test_none(self):
        self.assertAlmostEqual(_reefscape_endgame_points_2025(None), 0.0)

    def test_case_sensitive(self):
        # Function uses lowered comparison
        self.assertAlmostEqual(_reefscape_endgame_points_2025("deepcage"), 12.0)

class RebuiltActiveHubDurationTests(unittest.TestCase):
    def test_default_phases(self):
        duration = _rebuilt_active_hub_duration_sec(DEFAULT_TBA_PHASES)
        self.assertGreater(duration, 1.0)

    def test_without_grace(self):
        with_grace = _rebuilt_active_hub_duration_sec(
            DEFAULT_TBA_PHASES, include_post_deactivate_grace=True
        )
        without_grace = _rebuilt_active_hub_duration_sec(
            DEFAULT_TBA_PHASES, include_post_deactivate_grace=False
        )
        self.assertAlmostEqual(with_grace - without_grace, 3.0 * REBUILT_HUB_SCORE_GRACE_SEC)

    def test_minimum_duration(self):
        duration = _rebuilt_active_hub_duration_sec(
            {"total_sec": 0, "auto_sec": 0, "teleop_sec": 0, "endgame_sec": 0},
            include_post_deactivate_grace=False,
        )
        self.assertGreaterEqual(duration, 1.0)

    def test_custom_phases(self):
        phases = {"total_sec": 200, "auto_sec": 30, "teleop_sec": 170, "endgame_sec": 40}
        duration = _rebuilt_active_hub_duration_sec(phases)
        self.assertGreater(duration, 40.0)  # At least endgame duration

if __name__ == "__main__":
    unittest.main()
