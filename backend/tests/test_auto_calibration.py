import unittest

try:
    import numpy as np

    from app.services.calibration.auto import (
        build_tag_anchor_lookup,
        fit_homography_from_correspondences,
        sample_frame_times,
    )

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - dependency-gated test import
    _IMPORT_ERROR = exc


@unittest.skipIf(_IMPORT_ERROR is not None, f"auto calibration deps unavailable: {_IMPORT_ERROR}")
class AutoCalibrationHelperTests(unittest.TestCase):
    def test_build_tag_anchor_lookup_returns_2026_layout(self):
        welded = build_tag_anchor_lookup("welded")
        andymark = build_tag_anchor_lookup("andymark")

        self.assertGreaterEqual(len(welded), 4)
        self.assertGreaterEqual(len(andymark), 4)
        self.assertIn(1, welded)
        self.assertIn(1, andymark)

    def test_sample_frame_times_focus_priority_and_bounds(self):
        times = sample_frame_times(duration_sec=120.0, sample_count=6, focus_time_sec=35.0)
        self.assertGreaterEqual(len(times), 6)
        self.assertEqual(times[0], 35.0)
        self.assertTrue(all(0.0 <= value <= 120.0 for value in times))

    def test_fit_homography_from_correspondences(self):
        # Synthetic affine mapping from image -> field.
        # x = 0.01u + 0.4 ; y = 0.02v + 0.7
        image_points = [
            (100.0, 120.0),
            (220.0, 140.0),
            (340.0, 160.0),
            (460.0, 180.0),
            (130.0, 300.0),
            (260.0, 320.0),
            (390.0, 340.0),
            (520.0, 360.0),
        ]
        correspondences = []
        for idx, (u, v) in enumerate(image_points, start=1):
            x = (0.01 * u) + 0.4
            y = (0.02 * v) + 0.7
            correspondences.append((idx, u, v, x, y))

        candidate = fit_homography_from_correspondences(
            correspondences=correspondences,
            ransac_reproj_threshold_px=2.5,
            frame_time_sec=12.5,
            image_width=1920,
            image_height=1080,
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertGreaterEqual(candidate.inlier_count, 8)
        self.assertLess(candidate.reprojection_rmse_m, 1e-4)

        point_h = np.array([250.0, 210.0, 1.0], dtype=np.float64)
        homography = np.asarray(candidate.homography, dtype=np.float64)
        mapped = homography @ point_h
        mapped /= mapped[2]
        self.assertAlmostEqual(float(mapped[0]), 2.9, places=3)
        self.assertAlmostEqual(float(mapped[1]), 4.9, places=3)


if __name__ == "__main__":
    unittest.main()
