# Tests for app.services.vision.track_analysis – field projection & track summary helpers.

import unittest

from app.services.vision.track_analysis import (
    _build_track_summaries,
    _compute_field_speeds,
    _prune_observations_to_field_bounds,
)

def _obs(track_id, frame_index, time_sec, field_x=None, field_y=None,
         centroid_x=100.0, centroid_y=100.0, **extras):
    row = {
        "track_id": track_id,
        "frame_index": frame_index,
        "time_sec": time_sec,
        "field_x": field_x,
        "field_y": field_y,
        "centroid_x": centroid_x,
        "centroid_y": centroid_y,
    }
    row.update(extras)
    return row

class PruneObservationsToFieldBoundsTests(unittest.TestCase):
    def test_disabled_passthrough(self):
        obs = [_obs(1, 0, 0.0, field_x=100.0, field_y=100.0)]
        result, stats = _prune_observations_to_field_bounds(
            obs, field_length_m=16.46, field_width_m=8.23,
            enabled=False, margin_m=0.5, drop_unprojectable=False,
        )
        self.assertEqual(len(result), 1)
        self.assertFalse(stats["enabled"])

    def test_empty_observations(self):
        result, stats = _prune_observations_to_field_bounds(
            [], field_length_m=16.46, field_width_m=8.23,
            enabled=True, margin_m=0.5, drop_unprojectable=False,
        )
        self.assertEqual(len(result), 0)
        self.assertEqual(stats["reason"], "no_observations")

    def test_in_bounds_kept(self):
        obs = [_obs(1, 0, 0.0, field_x=5.0, field_y=4.0)]
        result, stats = _prune_observations_to_field_bounds(
            obs, field_length_m=16.46, field_width_m=8.23,
            enabled=True, margin_m=0.5, drop_unprojectable=False,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(stats["dropped_out_of_bounds"], 0)

    def test_out_of_bounds_dropped(self):
        obs = [_obs(1, 0, 0.0, field_x=100.0, field_y=100.0)]
        result, stats = _prune_observations_to_field_bounds(
            obs, field_length_m=16.46, field_width_m=8.23,
            enabled=True, margin_m=0.5, drop_unprojectable=False,
        )
        self.assertEqual(len(result), 0)
        self.assertEqual(stats["dropped_out_of_bounds"], 1)

    def test_unprojectable_dropped_when_flag_set(self):
        obs = [_obs(1, 0, 0.0, field_x=None, field_y=None)]
        result, stats = _prune_observations_to_field_bounds(
            obs, field_length_m=16.46, field_width_m=8.23,
            enabled=True, margin_m=0.5, drop_unprojectable=True,
        )
        self.assertEqual(len(result), 0)
        self.assertEqual(stats["dropped_unprojectable"], 1)

    def test_unprojectable_kept_when_flag_false(self):
        obs = [_obs(1, 0, 0.0, field_x=None, field_y=None)]
        result, stats = _prune_observations_to_field_bounds(
            obs, field_length_m=16.46, field_width_m=8.23,
            enabled=True, margin_m=0.5, drop_unprojectable=False,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(stats["dropped_unprojectable"], 0)

    def test_margin_extends_bounds(self):
        obs = [_obs(1, 0, 0.0, field_x=-0.3, field_y=-0.3)]
        # Without margin, -0.3 is out of bounds. With margin=0.5, it's in bounds.
        result, stats = _prune_observations_to_field_bounds(
            obs, field_length_m=16.46, field_width_m=8.23,
            enabled=True, margin_m=0.5, drop_unprojectable=False,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(stats["dropped_out_of_bounds"], 0)

class BuildTrackSummariesTests(unittest.TestCase):
    def test_single_track(self):
        obs = [
            _obs(1, 0, 0.0, field_x=0.0, field_y=0.0),
            _obs(1, 1, 1.0, field_x=3.0, field_y=4.0),
        ]
        summaries = _build_track_summaries(obs)
        self.assertIn(1, summaries)
        s = summaries[1]
        self.assertEqual(s["observation_count"], 2)
        self.assertAlmostEqual(s["distance_m"], 5.0, places=2)  # 3-4-5 triangle
        self.assertAlmostEqual(s["avg_speed_mps"], 5.0, places=2)
        self.assertAlmostEqual(s["duration_sec"], 1.0, places=2)

    def test_no_field_coords(self):
        obs = [
            _obs(1, 0, 0.0, field_x=None, field_y=None),
            _obs(1, 1, 1.0, field_x=None, field_y=None),
        ]
        summaries = _build_track_summaries(obs)
        self.assertIsNone(summaries[1]["avg_field_x"])
        self.assertIsNone(summaries[1]["avg_field_y"])
        self.assertAlmostEqual(summaries[1]["distance_m"], 0.0)

    def test_multiple_tracks(self):
        obs = [
            _obs(1, 0, 0.0, field_x=0.0, field_y=0.0),
            _obs(2, 0, 0.0, field_x=10.0, field_y=10.0),
        ]
        summaries = _build_track_summaries(obs)
        self.assertIn(1, summaries)
        self.assertIn(2, summaries)

    def test_single_point_speed_is_none(self):
        obs = [_obs(1, 0, 0.0, field_x=5.0, field_y=5.0)]
        summaries = _build_track_summaries(obs)
        self.assertIsNone(summaries[1]["avg_speed_mps"])

class ComputeFieldSpeedsTests(unittest.TestCase):
    def test_two_point_speed(self):
        obs = [
            _obs(1, 0, 0.0, field_x=0.0, field_y=0.0),
            _obs(1, 1, 1.0, field_x=3.0, field_y=4.0),
        ]
        result = _compute_field_speeds(obs)
        self.assertIsNone(result[0]["speed_mps"])  # First point has no previous
        self.assertAlmostEqual(result[1]["speed_mps"], 5.0, places=2)

    def test_missing_field_coords_no_speed(self):
        obs = [
            _obs(1, 0, 0.0, field_x=0.0, field_y=0.0),
            _obs(1, 1, 1.0, field_x=None, field_y=None),
        ]
        result = _compute_field_speeds(obs)
        self.assertIsNone(result[1]["speed_mps"])

    def test_single_observation_no_speed(self):
        obs = [_obs(1, 0, 0.0, field_x=5.0, field_y=5.0)]
        result = _compute_field_speeds(obs)
        self.assertIsNone(result[0]["speed_mps"])

if __name__ == "__main__":
    unittest.main()
