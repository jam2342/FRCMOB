# Tests for app.services.vision.tracking_detection – YOLO/ByteTrack utility helpers.

import unittest

from app.services.vision.tracking_detection import (
    _bbox_overlap_ratio,
    _is_coco_like_class_names,
    _normalize_yolo_label,
    _parse_csv_tokens,
    _parse_yolo_classes,
    _prune_yolo_track_noise,
)

class ParseYoloClassesTests(unittest.TestCase):
    def test_valid_csv(self):
        self.assertEqual(_parse_yolo_classes("0,1,2"), [0, 1, 2])

    def test_none(self):
        self.assertIsNone(_parse_yolo_classes(None))

    def test_empty(self):
        self.assertIsNone(_parse_yolo_classes(""))

    def test_negative_values_excluded(self):
        self.assertEqual(_parse_yolo_classes("0,-1,3"), [0, 3])

    def test_non_numeric_skipped(self):
        self.assertEqual(_parse_yolo_classes("0,abc,3"), [0, 3])

    def test_duplicates_removed(self):
        result = _parse_yolo_classes("1,1,2,2,3")
        self.assertEqual(result, [1, 2, 3])

    def test_sorted(self):
        result = _parse_yolo_classes("3,1,2")
        self.assertEqual(result, [1, 2, 3])

    def test_whitespace_tokens(self):
        self.assertEqual(_parse_yolo_classes(" 0 , 1 , 2 "), [0, 1, 2])

    def test_all_invalid(self):
        self.assertIsNone(_parse_yolo_classes("abc,def"))

class ParseCsvTokensTests(unittest.TestCase):
    def test_valid_csv(self):
        self.assertEqual(_parse_csv_tokens("a, b, c"), ["a", "b", "c"])

    def test_none(self):
        self.assertEqual(_parse_csv_tokens(None), [])

    def test_empty(self):
        self.assertEqual(_parse_csv_tokens(""), [])

    def test_whitespace_stripped(self):
        self.assertEqual(_parse_csv_tokens("  hello , world  "), ["hello", "world"])

    def test_empty_tokens_removed(self):
        self.assertEqual(_parse_csv_tokens("a,,b,"), ["a", "b"])

class NormalizeYoloLabelTests(unittest.TestCase):
    def test_lowercase_and_strip_special(self):
        self.assertEqual(_normalize_yolo_label("FRC Robot"), "frcrobot")

    def test_none(self):
        self.assertEqual(_normalize_yolo_label(None), "")

    def test_digits_preserved(self):
        self.assertEqual(_normalize_yolo_label("Robot 3"), "robot3")

    def test_all_special(self):
        self.assertEqual(_normalize_yolo_label("---"), "")

class IsCocoLikeClassNamesTests(unittest.TestCase):
    def _coco_like_dict(self):
        names = {i: f"class_{i}" for i in range(80)}
        names[0] = "person"
        names[1] = "car"
        names[32] = "sports ball"
        return names

    def test_coco_like_returns_true(self):
        self.assertTrue(_is_coco_like_class_names(self._coco_like_dict()))

    def test_empty_returns_false(self):
        self.assertFalse(_is_coco_like_class_names({}))

    def test_small_dict(self):
        self.assertFalse(_is_coco_like_class_names({0: "robot", 1: "ball"}))

    def test_missing_person(self):
        d = self._coco_like_dict()
        d[0] = "robot"  # Replace person with robot
        self.assertFalse(_is_coco_like_class_names(d))

class BboxOverlapRatioTests(unittest.TestCase):
    def test_full_overlap(self):
        ratio = _bbox_overlap_ratio(10, 10, 20, 20, 0, 0, 100, 100)
        self.assertAlmostEqual(ratio, 1.0)

    def test_no_overlap(self):
        ratio = _bbox_overlap_ratio(10, 10, 20, 20, 50, 50, 100, 100)
        self.assertAlmostEqual(ratio, 0.0)

    def test_partial_overlap(self):
        # Box 0-10, 0-10 (area 100). ROI 5-15, 0-10. Intersection = 5x10=50
        ratio = _bbox_overlap_ratio(0, 0, 10, 10, 5, 0, 15, 10)
        self.assertAlmostEqual(ratio, 0.5)

    def test_zero_area_box(self):
        ratio = _bbox_overlap_ratio(5, 5, 5, 5, 0, 0, 100, 100)
        self.assertAlmostEqual(ratio, 0.0)

class PruneYoloTrackNoiseTests(unittest.TestCase):
    def test_empty_observations(self):
        filtered, stats = _prune_yolo_track_noise([])
        self.assertEqual(filtered, [])
        self.assertEqual(stats["track_count_before"], 0)

    def test_single_obs_track_dropped(self):
        obs = [{"track_id": 1, "confidence": 0.3, "frame_index": 0}]
        filtered, stats = _prune_yolo_track_noise(obs, min_track_observations=2)
        self.assertEqual(len(filtered), 0)
        self.assertEqual(stats["dropped_tracks"], 1)

    def test_multi_obs_track_kept(self):
        obs = [
            {"track_id": 1, "confidence": 0.5, "frame_index": i}
            for i in range(5)
        ]
        filtered, stats = _prune_yolo_track_noise(obs, min_track_observations=3)
        self.assertEqual(len(filtered), 5)
        self.assertEqual(stats["dropped_tracks"], 0)

    def test_high_confidence_short_track_kept(self):
        obs = [{"track_id": 1, "confidence": 0.99, "frame_index": 0}]
        filtered, stats = _prune_yolo_track_noise(
            obs, min_track_observations=3, min_track_avg_confidence=0.5
        )
        # Very high confidence → avg_conf >= min+0.22 → kept
        self.assertEqual(len(filtered), 1)

    def test_low_confidence_short_track_dropped(self):
        obs = [{"track_id": 1, "confidence": 0.1, "frame_index": 0}]
        filtered, stats = _prune_yolo_track_noise(
            obs, min_track_observations=3, min_track_avg_confidence=0.5
        )
        self.assertEqual(len(filtered), 0)

    def test_mixed_tracks(self):
        obs = [
            {"track_id": 1, "confidence": 0.5, "frame_index": i} for i in range(5)
        ] + [
            {"track_id": 2, "confidence": 0.1, "frame_index": 0}
        ]
        filtered, stats = _prune_yolo_track_noise(obs, min_track_observations=2)
        self.assertEqual(stats["track_count_before"], 2)
        self.assertEqual(stats["dropped_tracks"], 1)
        self.assertTrue(all(r["track_id"] == 1 for r in filtered))

if __name__ == "__main__":
    unittest.main()
