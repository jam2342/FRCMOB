from __future__ import annotations

# OpenCV-side optical-flow camera stabilization for the on-device pose.
#
# Kept OUT of on_device.py on purpose: that module stays OpenCV-free so the PWA can
# mirror it in-browser. This file is the Python/server counterpart and uses cv2; the
# PWA implements the same idea with a JS/WebGL optical-flow routine.
#
# Why it exists: real-footage testing (2026-06-20) showed the field's AprilTags are
# unresolvable from stands distance, so the planned "re-detect tags every frame" pose
# recovery does not work where scouts actually film. Instead a scout taps the 4 field
# corners ONCE (on_device.calibrate_from_taps) to fix a base pose, and this module
# carries that pose frame-to-frame from optical flow of the static field/background,
# falling back to the last good pose when flow drops out.

import cv2
import numpy as np

from app.services.auto_scout.on_device import carry_pose

# Fewer than this many tracked features -> don't trust the motion estimate this frame.
_MIN_FLOW_POINTS = 12


def estimate_interframe_homography(
    prev_gray: np.ndarray,
    curr_gray: np.ndarray,
    *,
    max_features: int = 400,
    ransac_thresh_px: float = 3.0,
) -> np.ndarray | None:
    # Homography mapping previous-frame pixels -> current-frame pixels, from Lucas-Kanade
    # optical flow of good corner features. RANSAC rejects moving robots/people so the fit
    # locks onto the static field. Returns None when too few features track (caller holds
    # the last pose rather than carrying a garbage motion).
    prev_pts = cv2.goodFeaturesToTrack(
        prev_gray, maxCorners=max_features, qualityLevel=0.01, minDistance=8
    )
    if prev_pts is None or len(prev_pts) < _MIN_FLOW_POINTS:
        return None
    curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(prev_gray, curr_gray, prev_pts, None)
    if curr_pts is None:
        return None
    status = status.reshape(-1).astype(bool)
    p0 = prev_pts.reshape(-1, 2)[status]
    p1 = curr_pts.reshape(-1, 2)[status]
    if len(p0) < _MIN_FLOW_POINTS:
        return None
    motion, _ = cv2.findHomography(p0, p1, cv2.RANSAC, ransac_thresh_px)
    return motion


class StabilizedPose:
    # Carries a calibrated image->field homography across a moving (handheld) camera by
    # composing per-frame optical-flow motion, falling back to the last good pose when
    # flow fails — the moving-camera analogue of produce_track_points' pose fallback.
    # Feed grayscale frames in capture order; read .homography for the current pose.
    def __init__(self, base_homography: np.ndarray):
        self.homography = np.asarray(base_homography, dtype=np.float64)
        self.lost_frames = 0  # consecutive frames flow failed (UI can prompt a re-tap)
        self._prev_gray: np.ndarray | None = None

    def update(self, gray: np.ndarray) -> np.ndarray:
        if self._prev_gray is not None:
            motion = estimate_interframe_homography(self._prev_gray, gray)
            if motion is not None:
                try:
                    self.homography = carry_pose(self.homography, motion)
                    self.lost_frames = 0
                except np.linalg.LinAlgError:
                    self.lost_frames += 1  # singular motion -> hold pose
            else:
                self.lost_frames += 1  # too few features -> hold pose
        self._prev_gray = gray
        return self.homography
