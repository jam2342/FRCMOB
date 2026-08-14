from __future__ import annotations

import unittest

import cv2
import numpy as np

from app.services.auto_scout.on_device import project_point
from app.services.auto_scout.on_device_cv import (
    StabilizedPose,
    estimate_interframe_homography,
)


def _textured_image(w: int = 480, h: int = 360, seed: int = 0) -> np.ndarray:
    # A dark frame scattered with bright blobs -> lots of trackable corner features.
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 30, np.uint8)
    for _ in range(180):
        x = int(rng.integers(12, w - 12))
        y = int(rng.integers(12, h - 12))
        cv2.circle(img, (x, y), int(rng.integers(2, 5)), int(rng.integers(150, 255)), -1)
    return img


def _shift(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    # Translate content so every feature at (x,y) moves to (x+dx, y+dy) — unambiguous
    # ground-truth motion (the wrapped edge strip is a minority RANSAC rejects).
    return np.roll(img, shift=(dy, dx), axis=(0, 1))


class OpticalFlowTests(unittest.TestCase):
    def test_estimate_interframe_recovers_known_motion(self):
        prev = _textured_image()
        dx, dy = 12, -7  # prev->curr feature motion
        curr = _shift(prev, dx, dy)
        est = estimate_interframe_homography(prev, curr)
        self.assertIsNotNone(est)
        got = est @ np.array([240, 180, 1.0])
        self.assertAlmostEqual(got[0] / got[2], 240 + dx, delta=1.5)
        self.assertAlmostEqual(got[1] / got[2], 180 + dy, delta=1.5)

    def test_estimate_returns_none_without_features(self):
        blank = np.zeros((200, 200), np.uint8)
        self.assertIsNone(estimate_interframe_homography(blank, blank))

    def test_stabilized_pose_holds_static_field_point_under_pan(self):
        # base calibration: pixels/50 -> field metres
        base = np.array([[0.02, 0, 0], [0, 0.02, 0], [0, 0, 1]], dtype=np.float64)
        f0 = project_point(base, 240, 180)  # a static field point, at px (240,180) in frame 0
        prev = _textured_image()
        pose = StabilizedPose(base)
        pose.update(prev)
        dx, dy = 8, 3  # constant pan, prev->curr
        cur = prev
        drift = []
        for k in range(1, 6):
            cur = _shift(cur, dx, dy)
            h = pose.update(cur)
            # the same scene point now sits at (240 + k*dx, 180 + k*dy)
            fk = project_point(h, 240 + k * dx, 180 + k * dy)
            drift.append(abs(fk[0] - f0[0]) + abs(fk[1] - f0[1]))
        self.assertEqual(pose.lost_frames, 0)  # flow tracked every frame
        self.assertLess(max(drift), 0.25)  # static field point barely drifts (field metres)


if __name__ == "__main__":
    unittest.main()
