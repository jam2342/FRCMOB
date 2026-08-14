import unittest

try:
    from app.services.jobs import (
        _bbox_overlap_ratio,
        _is_coco_like_class_names,
        _prune_observations_to_field_bounds,
        _prune_yolo_track_noise,
        _resolve_auto_robot_class_ids,
        _trimmed_window_weak_tracking_signal,
    )

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - optional dependency guarded import
    _IMPORT_ERROR = exc


class _DummyModel:
    def __init__(self, names):
        self.names = names


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional deps unavailable: {_IMPORT_ERROR}")
class TrackingQualityFilterTests(unittest.TestCase):
    def test_auto_robot_class_detection_prefers_robot_labels(self):
        model = _DummyModel({0: "robot", 1: "person", 2: "game-piece"})
        classes = _resolve_auto_robot_class_ids(model)
        self.assertEqual(classes, [0])

    def test_auto_robot_class_detection_does_not_match_bottle_label(self):
        model = _DummyModel({39: "bottle", 0: "person"})
        classes = _resolve_auto_robot_class_ids(model)
        self.assertEqual(classes, [])

    def test_coco_like_class_names_detection(self):
        class_names = {
            0: "person",
            1: "bicycle",
            2: "car",
            3: "motorcycle",
            32: "sports ball",
            79: "toothbrush",
        }
        # Simulate a full COCO-like catalog by filling sparse ids.
        for idx in range(80):
            class_names.setdefault(idx, f"class_{idx}")
        self.assertTrue(_is_coco_like_class_names(class_names))

    def test_track_pruning_drops_short_low_confidence_tracks(self):
        observations = [
            {"track_id": 1, "confidence": 0.31},
            {"track_id": 1, "confidence": 0.28},
            {"track_id": 1, "confidence": 0.34},
            {"track_id": 2, "confidence": 0.18},
            {"track_id": 3, "confidence": 0.88},
        ]

        filtered, meta = _prune_yolo_track_noise(observations)
        kept_track_ids = sorted({int(row["track_id"]) for row in filtered})

        self.assertEqual(kept_track_ids, [1, 3])
        self.assertEqual(meta["track_count_before"], 3)
        self.assertEqual(meta["track_count_after"], 2)
        self.assertEqual(meta["dropped_tracks"], 1)
        self.assertEqual(meta["dropped_observations"], 1)

    def test_field_bounds_prune_removes_out_of_bounds_points(self):
        observations = [
            {"track_id": 1, "field_x": 8.2, "field_y": 4.1},   # in bounds
            {"track_id": 1, "field_x": -1.2, "field_y": 4.0},  # out of bounds for 0.8m margin
            {"track_id": 2, "field_x": 17.6, "field_y": 9.3},  # out of bounds on both
            {"track_id": 3, "field_x": None, "field_y": None}, # unprojectable retained by default
        ]
        filtered, meta = _prune_observations_to_field_bounds(
            observations,
            field_length_m=16.54,
            field_width_m=8.21,
            enabled=True,
            margin_m=0.8,
            drop_unprojectable=False,
        )
        self.assertEqual(len(filtered), 2)
        self.assertEqual(meta["dropped_out_of_bounds"], 2)
        self.assertEqual(meta["dropped_unprojectable"], 0)

    def test_field_bounds_prune_can_drop_unprojectable_points(self):
        observations = [
            {"track_id": 1, "field_x": 8.2, "field_y": 4.1},
            {"track_id": 3, "field_x": None, "field_y": None},
        ]
        filtered, meta = _prune_observations_to_field_bounds(
            observations,
            field_length_m=16.54,
            field_width_m=8.21,
            enabled=True,
            margin_m=0.8,
            drop_unprojectable=True,
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(meta["dropped_unprojectable"], 1)

    def test_bbox_overlap_ratio(self):
        self.assertAlmostEqual(
            _bbox_overlap_ratio(20.0, 20.0, 80.0, 80.0, 0.0, 0.0, 100.0, 100.0),
            1.0,
            places=6,
        )
        self.assertAlmostEqual(
            _bbox_overlap_ratio(0.0, 0.0, 100.0, 100.0, 50.0, 0.0, 100.0, 100.0),
            0.5,
            places=6,
        )
        self.assertAlmostEqual(
            _bbox_overlap_ratio(0.0, 0.0, 20.0, 20.0, 40.0, 40.0, 60.0, 60.0),
            0.0,
            places=6,
        )

    def test_trimmed_window_weak_tracking_signal_triggers_fallback(self):
        from app.core.config import settings

        original_enabled = settings.video_tracking_interlude_trim_weak_signal_fallback_enabled
        original_min_obs = settings.video_tracking_interlude_trim_weak_signal_min_observations_per_team
        original_min_tracks = settings.video_tracking_interlude_trim_weak_signal_min_tracks
        try:
            settings.video_tracking_interlude_trim_weak_signal_fallback_enabled = True
            settings.video_tracking_interlude_trim_weak_signal_min_observations_per_team = 8
            settings.video_tracking_interlude_trim_weak_signal_min_tracks = 4
            should_fallback, meta = _trimmed_window_weak_tracking_signal(
                observations_count=10,
                tracks_count=2,
                team_count=6,
            )
            self.assertTrue(should_fallback)
            self.assertIn("observations_below_threshold", meta.get("trigger_reasons") or [])
            self.assertIn("tracks_below_threshold", meta.get("trigger_reasons") or [])
            self.assertEqual(meta.get("min_total_observations"), 48)
        finally:
            settings.video_tracking_interlude_trim_weak_signal_fallback_enabled = original_enabled
            settings.video_tracking_interlude_trim_weak_signal_min_observations_per_team = original_min_obs
            settings.video_tracking_interlude_trim_weak_signal_min_tracks = original_min_tracks

    def test_trimmed_window_weak_tracking_signal_respects_disable_flag(self):
        from app.core.config import settings

        original_enabled = settings.video_tracking_interlude_trim_weak_signal_fallback_enabled
        original_min_obs = settings.video_tracking_interlude_trim_weak_signal_min_observations_per_team
        original_min_tracks = settings.video_tracking_interlude_trim_weak_signal_min_tracks
        try:
            settings.video_tracking_interlude_trim_weak_signal_fallback_enabled = False
            settings.video_tracking_interlude_trim_weak_signal_min_observations_per_team = 8
            settings.video_tracking_interlude_trim_weak_signal_min_tracks = 4
            should_fallback, meta = _trimmed_window_weak_tracking_signal(
                observations_count=5,
                tracks_count=1,
                team_count=6,
            )
            self.assertFalse(should_fallback)
            self.assertFalse(meta.get("enabled"))
            self.assertTrue(len(meta.get("trigger_reasons") or []) >= 1)
        finally:
            settings.video_tracking_interlude_trim_weak_signal_fallback_enabled = original_enabled
            settings.video_tracking_interlude_trim_weak_signal_min_observations_per_team = original_min_obs
            settings.video_tracking_interlude_trim_weak_signal_min_tracks = original_min_tracks


if __name__ == "__main__":
    unittest.main()
