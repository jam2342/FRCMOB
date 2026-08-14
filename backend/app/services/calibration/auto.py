from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2  # type: ignore
import numpy as np

from app.services.game_config import get_field_layout_variant

APRILTAG_DICTIONARY_NAME = "DICT_APRILTAG_36h11"


@dataclass(slots=True)
class AutoCalibrationCandidate:
    frame_time_sec: float
    image_width: int
    image_height: int
    homography: list[list[float]]
    inlier_count: int
    correspondence_count: int
    unique_inlier_tag_ids: list[int]
    all_detected_tag_ids: list[int]
    inlier_ratio: float
    reprojection_rmse_m: float
    image_points: list[tuple[float, float]]
    field_points: list[tuple[float, float]]


class AutoCalibrationError(RuntimeError):
    pass


def normalize_perimeter_for_layout(value: str) -> str:
    normalized = (value or "").strip().lower()
    return "andymark" if normalized == "andymark" else "welded"


def build_tag_anchor_lookup(perimeter_type: str) -> dict[int, tuple[float, float]]:
    variant = get_field_layout_variant(normalize_perimeter_for_layout(perimeter_type))
    if variant is None:
        raise AutoCalibrationError(
            f"No field_layout variant found for perimeter_type={normalize_perimeter_for_layout(perimeter_type)}"
        )

    lookup: dict[int, tuple[float, float]] = {}
    for anchor in variant.anchor_points:
        lookup[int(anchor.id)] = (float(anchor.position.x), float(anchor.position.y))

    if len(lookup) < 4:
        raise AutoCalibrationError("Field layout has fewer than 4 AprilTag anchors; cannot calibrate.")

    return lookup


def sample_frame_times(
    *,
    duration_sec: float,
    sample_count: int,
    focus_time_sec: float | None,
) -> list[float]:
    safe_duration = max(1.0, float(duration_sec))
    safe_count = max(1, int(sample_count))

    start = max(0.0, safe_duration * 0.05)
    end = max(start + 0.05, safe_duration * 0.95)

    if safe_count == 1:
        base = [min(end, max(start, safe_duration * 0.5))]
    else:
        step = (end - start) / float(safe_count - 1)
        base = [start + (step * idx) for idx in range(safe_count)]

    ordered: list[float] = []
    seen = set()

    def add_time(value: float) -> None:
        rounded = round(max(0.0, min(safe_duration, float(value))), 3)
        if rounded in seen:
            return
        seen.add(rounded)
        ordered.append(rounded)

    if focus_time_sec is not None:
        add_time(float(focus_time_sec))

    for value in base:
        add_time(value)

    return ordered


def _build_detector():
    if not hasattr(cv2, "aruco"):
        raise AutoCalibrationError("OpenCV aruco module is unavailable; install opencv-contrib support.")

    aruco = cv2.aruco
    if not hasattr(aruco, APRILTAG_DICTIONARY_NAME):
        raise AutoCalibrationError("OpenCV AprilTag dictionary is unavailable in this runtime.")

    dictionary = aruco.getPredefinedDictionary(getattr(aruco, APRILTAG_DICTIONARY_NAME))

    if hasattr(aruco, "DetectorParameters") and hasattr(aruco, "ArucoDetector"):
        params = aruco.DetectorParameters()
        return aruco.ArucoDetector(dictionary, params)

    raise AutoCalibrationError("OpenCV ArUco detector API is unavailable in this runtime.")


def detect_apriltag_centers(frame_bgr: np.ndarray, detector=None) -> list[tuple[int, float, float]]:
    detector = detector or _build_detector()

    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)

    if ids is None or len(ids) == 0:
        return []

    observations: list[tuple[int, float, float]] = []
    for marker_corners, marker_id in zip(corners, ids, strict=False):
        tag_id = int(marker_id[0])
        points = np.asarray(marker_corners, dtype=np.float32).reshape(-1, 2)
        cx = float(points[:, 0].mean())
        cy = float(points[:, 1].mean())
        observations.append((tag_id, cx, cy))

    return observations


def fit_homography_from_correspondences(
    *,
    correspondences: list[tuple[int, float, float, float, float]],
    ransac_reproj_threshold_px: float,
    frame_time_sec: float,
    image_width: int,
    image_height: int,
) -> AutoCalibrationCandidate | None:
    if len(correspondences) < 4:
        return None

    tag_ids = [int(item[0]) for item in correspondences]
    image_points = np.asarray([[item[1], item[2]] for item in correspondences], dtype=np.float32)
    field_points = np.asarray([[item[3], item[4]] for item in correspondences], dtype=np.float32)

    homography, inlier_mask = cv2.findHomography(
        image_points,
        field_points,
        method=cv2.RANSAC,
        ransacReprojThreshold=float(ransac_reproj_threshold_px),
    )
    if homography is None or inlier_mask is None:
        return None

    inliers = inlier_mask.reshape(-1).astype(bool)
    inlier_count = int(inliers.sum())
    if inlier_count < 4:
        return None

    projected = cv2.perspectiveTransform(image_points.reshape(-1, 1, 2), homography).reshape(-1, 2)
    inlier_errors = np.linalg.norm(projected[inliers] - field_points[inliers], axis=1)
    rmse = float(np.sqrt(np.mean(inlier_errors**2))) if inlier_errors.size else float("inf")

    inlier_tag_ids = sorted({int(tag_id) for tag_id, keep in zip(tag_ids, inliers, strict=False) if keep})

    image_points_inliers = [(float(pt[0]), float(pt[1])) for pt in image_points[inliers]]
    field_points_inliers = [(float(pt[0]), float(pt[1])) for pt in field_points[inliers]]

    return AutoCalibrationCandidate(
        frame_time_sec=round(float(frame_time_sec), 3),
        image_width=int(image_width),
        image_height=int(image_height),
        homography=homography.astype(float).tolist(),
        inlier_count=inlier_count,
        correspondence_count=int(len(correspondences)),
        unique_inlier_tag_ids=inlier_tag_ids,
        all_detected_tag_ids=sorted({int(tag_id) for tag_id in tag_ids}),
        inlier_ratio=float(inlier_count / max(1, len(correspondences))),
        reprojection_rmse_m=rmse,
        image_points=image_points_inliers,
        field_points=field_points_inliers,
    )


def _candidate_sort_key(candidate: AutoCalibrationCandidate) -> tuple[int, int, float, int]:
    return (
        int(candidate.inlier_count),
        int(len(candidate.unique_inlier_tag_ids)),
        -float(candidate.reprojection_rmse_m),
        int(candidate.correspondence_count),
    )


def auto_calibrate_from_video(
    *,
    video_path: Path | str,
    perimeter_type: str,
    duration_sec: float,
    sample_count: int,
    focus_time_sec: float | None,
    min_inliers: int,
    ransac_reproj_threshold_px: float,
) -> dict:
    anchor_lookup = build_tag_anchor_lookup(perimeter_type)

    frame_times = sample_frame_times(
        duration_sec=float(duration_sec),
        sample_count=int(sample_count),
        focus_time_sec=focus_time_sec,
    )
    detector = _build_detector()

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise AutoCalibrationError(f"Unable to open video for auto calibration: {video_path}")

    best: AutoCalibrationCandidate | None = None
    scanned_frames = 0
    frames_with_any_tags = 0
    frames_with_known_tags = 0
    best_correspondence_count = 0
    per_frame_summary: list[dict] = []

    try:
        for time_sec in frame_times:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(time_sec) * 1000.0)
            ok, frame = capture.read()
            scanned_frames += 1
            if not ok or frame is None:
                per_frame_summary.append(
                    {
                        "time_sec": round(float(time_sec), 3),
                        "status": "frame_read_failed",
                    }
                )
                continue

            image_height, image_width = frame.shape[:2]
            detections = detect_apriltag_centers(frame, detector=detector)
            detected_tag_ids = sorted({int(tag_id) for tag_id, _, _ in detections})
            if detections:
                frames_with_any_tags += 1

            correspondences: list[tuple[int, float, float, float, float]] = []
            for tag_id, u, v in detections:
                anchor = anchor_lookup.get(int(tag_id))
                if anchor is None:
                    continue
                correspondences.append((int(tag_id), float(u), float(v), float(anchor[0]), float(anchor[1])))

            best_correspondence_count = max(best_correspondence_count, len(correspondences))
            if correspondences:
                frames_with_known_tags += 1

            if len(correspondences) < 4:
                per_frame_summary.append(
                    {
                        "time_sec": round(float(time_sec), 3),
                        "status": "insufficient_correspondences",
                        "detected_tag_ids": detected_tag_ids,
                        "correspondence_count": len(correspondences),
                    }
                )
                continue

            candidate = fit_homography_from_correspondences(
                correspondences=correspondences,
                ransac_reproj_threshold_px=float(ransac_reproj_threshold_px),
                frame_time_sec=float(time_sec),
                image_width=int(image_width),
                image_height=int(image_height),
            )
            if candidate is None:
                per_frame_summary.append(
                    {
                        "time_sec": round(float(time_sec), 3),
                        "status": "homography_failed",
                        "detected_tag_ids": detected_tag_ids,
                        "correspondence_count": len(correspondences),
                    }
                )
                continue

            per_frame_summary.append(
                {
                    "time_sec": round(float(time_sec), 3),
                    "status": "ok",
                    "detected_tag_ids": detected_tag_ids,
                    "correspondence_count": len(correspondences),
                    "inlier_count": int(candidate.inlier_count),
                    "inlier_ratio": round(float(candidate.inlier_ratio), 4),
                    "reprojection_rmse_m": round(float(candidate.reprojection_rmse_m), 4),
                }
            )

            if best is None or _candidate_sort_key(candidate) > _candidate_sort_key(best):
                best = candidate
    finally:
        capture.release()

    if best is None:
        raise AutoCalibrationError(
            "Auto calibration failed: no valid AprilTag homography candidate was found. "
            f"Scanned {scanned_frames} frames, frames with tags={frames_with_any_tags}, "
            f"frames with known tag IDs={frames_with_known_tags}, max_correspondences={best_correspondence_count}."
        )

    if int(best.inlier_count) < int(min_inliers):
        raise AutoCalibrationError(
            "Auto calibration candidate below inlier threshold: "
            f"inliers={best.inlier_count}, min_inliers={int(min_inliers)}."
        )

    return {
        "frame_time_sec": best.frame_time_sec,
        "image_width": best.image_width,
        "image_height": best.image_height,
        "image_points": [{"x": x, "y": y} for x, y in best.image_points],
        "field_points": [{"x": x, "y": y} for x, y in best.field_points],
        "homography": best.homography,
        "diagnostics": {
            "sample_count_requested": int(sample_count),
            "sampled_frame_times_sec": [round(float(value), 3) for value in frame_times],
            "scanned_frames": int(scanned_frames),
            "frames_with_any_tags": int(frames_with_any_tags),
            "frames_with_known_tags": int(frames_with_known_tags),
            "max_correspondences": int(best_correspondence_count),
            "selected": {
                "frame_time_sec": best.frame_time_sec,
                "correspondence_count": int(best.correspondence_count),
                "inlier_count": int(best.inlier_count),
                "inlier_ratio": round(float(best.inlier_ratio), 4),
                "reprojection_rmse_m": round(float(best.reprojection_rmse_m), 5),
                "detected_tag_ids": best.all_detected_tag_ids,
                "inlier_tag_ids": best.unique_inlier_tag_ids,
            },
            "frame_summaries": per_frame_summary,
        },
    }
