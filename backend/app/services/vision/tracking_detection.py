# YOLO/ByteTrack tracking, motion fallback, scene-cut, and sampling plan.
#
# Extracted from jobs.py to reduce module size.
from __future__ import annotations

from contextlib import suppress
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.media.retention import run_media_retention
from app.services.utils import BACKEND_ROOT, _clamp, _round

MAX_TRACK_MATCH_DISTANCE_PX = 110.0
MAX_TRACK_GAP_FRAMES = 2
DEFAULT_SAMPLE_INTERVAL_SEC = 2.0
DEFAULT_MAX_SAMPLE_FRAMES = 120
DEFAULT_MAX_ARTIFACT_SAMPLE_FRAMES = 30
MAX_MOTION_DETECTIONS_PER_FRAME = 14

def _parse_yolo_classes(raw_value: str | None) -> list[int] | None:
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    classes: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            class_id = int(token)
        except ValueError:
            continue
        if class_id >= 0:
            classes.append(class_id)
    return sorted(set(classes)) if classes else None

def _parse_csv_tokens(raw_value: str | None) -> list[str]:
    raw = str(raw_value or "").strip()
    if not raw:
        return []
    return [token.strip() for token in raw.split(",") if token.strip()]

def _resolve_existing_path(raw_value: str | None) -> Path | None:
    token = str(raw_value or "").strip()
    if not token:
        return None
    candidate = Path(token)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    cwd_candidate = candidate.resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    backend_candidate = (BACKEND_ROOT / candidate).resolve()
    if backend_candidate.exists():
        return backend_candidate
    return None

def _resolve_yolo_model_candidates() -> list[tuple[str, str]]:
    # Return pairs of (model_source, source_tag) in preferred order.
    ordered: list[str] = []
    explicit = str(settings.video_tracking_yolo_model or "").strip()
    if explicit:
        ordered.append(explicit)
    ordered.extend(_parse_csv_tokens(settings.video_tracking_yolo_model_fallbacks))
    if not ordered:
        ordered.append("yolo11n.pt")

    seen: set[str] = set()
    resolved: list[tuple[str, str]] = []
    for item in ordered:
        if item in seen:
            continue
        seen.add(item)
        existing = _resolve_existing_path(item)
        if existing is not None:
            resolved.append((str(existing), "local_path"))
        else:
            resolved.append((item, "model_ref"))
    return resolved

def _resolve_tracker_config_candidate(raw_value: str | None) -> tuple[str, str]:
    token = str(raw_value or "").strip() or "bytetrack.yaml"
    if token.strip().lower() == "bytetrack.yaml":
        tuned = _resolve_existing_path("config/bytetrack_frc.yaml")
        if tuned is not None:
            return str(tuned), "local_tuned_default"
    resolved = _resolve_existing_path(token)
    if resolved is not None:
        return str(resolved), "local_path"
    return token, "model_ref"

def _model_class_names(model: Any) -> dict[int, str]:
    names = getattr(model, "names", None)
    resolved: dict[int, str] = {}
    if isinstance(names, dict):
        for raw_id, raw_name in names.items():
            try:
                class_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            label = str(raw_name or "").strip()
            if label:
                resolved[class_id] = label
        return resolved
    if isinstance(names, (list, tuple)):
        for class_id, raw_name in enumerate(names):
            label = str(raw_name or "").strip()
            if label:
                resolved[int(class_id)] = label
    return resolved

def _normalize_yolo_label(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

def _resolve_auto_robot_class_ids(model: Any) -> list[int]:
    class_names = _model_class_names(model)
    if not class_names:
        return []
    selected: list[int] = []
    for class_id, label in class_names.items():
        normalized = str(label or "").strip().lower()
        tokens = {token for token in re.findall(r"[a-z0-9]+", normalized) if token}
        if not tokens:
            continue
        has_robot_token = bool({"robot", "robots"} & tokens)
        has_frc_robot_compound = any(
            token.startswith("frc") and ("robot" in token or token.endswith("bot"))
            for token in tokens
        )
        has_frc_plus_bot_tokens = "frc" in tokens and "bot" in tokens
        if has_robot_token or has_frc_robot_compound or has_frc_plus_bot_tokens:
            selected.append(int(class_id))
    return sorted(set(selected))

def _is_coco_like_class_names(class_names: dict[int, str]) -> bool:
    if not class_names:
        return False
    normalized = {_normalize_yolo_label(label) for label in class_names.values()}
    return (
        len(class_names) >= 60
        and "person" in normalized
        and "car" in normalized
        and "sportsball" in normalized
    )

def _prune_yolo_track_noise(
    observations: list[dict],
    *,
    min_track_observations: int | None = None,
    min_track_avg_confidence: float | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    if not observations:
        return observations, {
            "enabled": True,
            "reason": "no_observations",
            "track_count_before": 0,
            "track_count_after": 0,
            "dropped_tracks": 0,
            "dropped_observations": 0,
        }

    min_obs = max(
        2,
        int(
            min_track_observations
            if min_track_observations is not None
            else (settings.video_tracking_yolo_min_track_observations or 3)
        ),
    )
    min_track_avg_conf = _clamp(
        float(
            min_track_avg_confidence
            if min_track_avg_confidence is not None
            else (settings.video_tracking_yolo_min_track_avg_confidence or 0.24)
        ),
        0.05,
        0.99,
    )

    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in observations:
        grouped[int(row["track_id"])].append(row)

    keep_tracks: set[int] = set()
    dropped_tracks = 0
    dropped_observations = 0
    for track_id, rows in grouped.items():
        obs_count = len(rows)
        avg_conf = (
            sum(float(row.get("confidence") or 0.0) for row in rows) / float(obs_count)
            if obs_count > 0
            else 0.0
        )
        keep = (
            obs_count >= min_obs
            or avg_conf >= min(0.98, min_track_avg_conf + 0.22)
        )
        if keep:
            keep_tracks.add(track_id)
        else:
            dropped_tracks += 1
            dropped_observations += obs_count

    filtered = [row for row in observations if int(row["track_id"]) in keep_tracks]
    return filtered, {
        "enabled": True,
        "min_track_observations": int(min_obs),
        "min_track_avg_confidence": _round(min_track_avg_conf, 4),
        "track_count_before": int(len(grouped)),
        "track_count_after": int(len(keep_tracks)),
        "dropped_tracks": int(dropped_tracks),
        "dropped_observations": int(dropped_observations),
    }

def _dense_retry_plan(
    *,
    sample_interval_sec: float,
    sample_max_frames: int,
) -> tuple[float, int]:
    retry_interval_setting = max(0.1, float(settings.video_tracking_dense_retry_interval_sec or 0.7))
    retry_interval = min(
        sample_interval_sec * 0.75,
        retry_interval_setting,
        max(0.1, sample_interval_sec - 0.08),
    )
    retry_interval = _round(max(0.1, retry_interval), 4)
    retry_max_frames = max(
        int(sample_max_frames) + 20,
        int(settings.video_tracking_dense_retry_max_frames or 220),
    )
    retry_max_frames = min(340, retry_max_frames)
    return retry_interval, retry_max_frames

def _should_dense_retry(
    *,
    observations: list[dict],
    track_summaries: dict[int, dict],
    team_count: int,
    sample_interval_sec: float,
) -> tuple[bool, dict[str, Any]]:
    if not bool(settings.video_tracking_dense_retry_enabled):
        return False, {"enabled": False, "reason": "disabled"}

    retry_interval_setting = max(0.1, float(settings.video_tracking_dense_retry_interval_sec or 0.7))
    if sample_interval_sec <= (retry_interval_setting + 0.04):
        return False, {"enabled": True, "reason": "already_dense_enough"}

    team_total = max(1, int(team_count))
    observations_per_team = float(len(observations)) / float(team_total)
    min_obs_per_team = max(4, int(settings.video_tracking_dense_retry_min_observations_per_team or 18))
    min_tracks = max(2, int(settings.video_tracking_dense_retry_min_tracks or 4))
    reasons: list[str] = []
    if len(observations) <= 0:
        reasons.append("no_observations")
    if observations_per_team < float(min_obs_per_team):
        reasons.append("low_observations_per_team")
    if len(track_summaries) < min_tracks:
        reasons.append("low_track_count")
    return bool(reasons), {
        "enabled": True,
        "reasons": reasons,
        "observations": int(len(observations)),
        "observations_per_team": _round(observations_per_team, 3),
        "track_count": int(len(track_summaries)),
        "min_observations_per_team": int(min_obs_per_team),
        "min_tracks": int(min_tracks),
    }

def _resolve_sampling_plan(
    *,
    duration_sec: float,
    phase_windows: dict[str, Any] | None,
) -> dict[str, Any]:
    duration = max(1.0, float(duration_sec))
    mode = str(settings.video_tracking_sampling_mode or "adaptive").strip().lower()
    if mode not in {"fixed", "dense", "adaptive"}:
        mode = "adaptive"

    base_interval = max(0.1, float(settings.video_tracking_sample_interval_sec or DEFAULT_SAMPLE_INTERVAL_SEC))
    dense_interval = max(0.1, float(settings.video_tracking_dense_interval_sec or base_interval))
    adaptive_min = max(0.1, float(settings.video_tracking_adaptive_min_interval_sec or min(base_interval, dense_interval)))
    adaptive_max = max(adaptive_min, float(settings.video_tracking_adaptive_max_interval_sec or max(base_interval, dense_interval)))
    max_frames_setting = max(1, int(settings.video_tracking_max_sample_frames or DEFAULT_MAX_SAMPLE_FRAMES))
    max_artifact_frames = max(1, int(settings.video_tracking_max_artifact_sample_frames or DEFAULT_MAX_ARTIFACT_SAMPLE_FRAMES))

    interval = base_interval
    reason = "fixed"
    if mode == "dense":
        interval = min(base_interval, dense_interval)
        reason = "dense_mode"
    elif mode == "adaptive":
        phase_payload = phase_windows if isinstance(phase_windows, dict) else {}
        phase_confidence = float(phase_payload.get("confidence_0_1") or 0.0)
        teleop_duration = float(phase_payload.get("teleop_scoring_duration_sec") or 0.0)
        interval = base_interval
        if duration >= 200.0:
            interval = min(interval, 1.4)
            reason = "adaptive_long_match"
        if teleop_duration >= 90.0:
            interval = min(interval, 1.2)
            reason = "adaptive_high_action_window"
        if phase_confidence >= 0.85:
            interval = min(interval, max(1.0, dense_interval))
            reason = "adaptive_high_confidence_dense"
        interval = _clamp(interval, adaptive_min, adaptive_max)

    required_frames = int(math.ceil(duration / max(1e-6, interval))) + 1
    if mode == "fixed":
        max_frames = max_frames_setting
    elif mode == "adaptive":
        adaptive_cap = min(300, int(math.ceil(max_frames_setting * 1.5)))
        max_frames = min(max(max_frames_setting, required_frames), adaptive_cap)
    else:
        max_frames = min(max(max_frames_setting, required_frames), 320)

    return {
        "mode": mode,
        "reason": reason,
        "sample_interval_sec": _round(interval, 4),
        "max_frames": int(max_frames),
        "required_frames": int(required_frames),
        "max_artifact_frames": int(max_artifact_frames),
    }

def _run_media_cleanup_best_effort(reason: str) -> None:
    with suppress(Exception):
        run_media_retention(force=False, reason=reason)

def _detect_motion_observations(
    sample_frames: list,
    *,
    sample_interval_sec: float,
) -> list[dict]:
    import cv2  # Imported lazily so backend API process does not require cv2 at import time.

    subtractor = cv2.createBackgroundSubtractorMOG2(
        history=220,
        varThreshold=40,
        detectShadows=False,
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    observations: list[dict] = []
    tracks_state: dict[int, dict] = {}
    next_track_id = 1

    for frame_index, frame_path in enumerate(sample_frames):
        image = cv2.imread(str(frame_path))
        if image is None:
            continue
        height, width = image.shape[:2]
        frame_area = float(width * height)

        mask = subtractor.apply(image)
        _, mask = cv2.threshold(mask, 210, 255, cv2.THRESH_BINARY)
        mask = cv2.medianBlur(mask, 5)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel, iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[dict] = []
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < 160:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            if w < 12 or h < 12:
                continue
            if (w * h) > (0.25 * frame_area):
                continue
            cx = x + (w / 2.0)
            cy = y + (h / 2.0)
            confidence = _clamp((area / max(frame_area * 0.02, 1.0)) + 0.2, 0.2, 0.98)
            detections.append(
                {
                    "bbox_x1": float(x),
                    "bbox_y1": float(y),
                    "bbox_x2": float(x + w),
                    "bbox_y2": float(y + h),
                    "cx": float(cx),
                    "cy": float(cy),
                    "area": area,
                    "confidence": confidence,
                }
            )

        detections.sort(key=lambda item: item["area"], reverse=True)
        detections = detections[:MAX_MOTION_DETECTIONS_PER_FRAME]
        active_track_ids = [
            track_id
            for track_id, state in tracks_state.items()
            if frame_index - state["last_frame_index"] <= MAX_TRACK_GAP_FRAMES
        ]
        used_track_ids: set[int] = set()

        for det in detections:
            best_track_id: int | None = None
            best_distance: float | None = None

            for track_id in active_track_ids:
                if track_id in used_track_ids:
                    continue
                state = tracks_state[track_id]
                distance = math.hypot(det["cx"] - state["cx"], det["cy"] - state["cy"])
                frame_gap = frame_index - state["last_frame_index"]
                threshold = MAX_TRACK_MATCH_DISTANCE_PX * (1.0 + (0.22 * frame_gap))
                if distance > threshold:
                    continue
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_track_id = track_id

            speed_px = None
            if best_track_id is None:
                track_id = next_track_id
                next_track_id += 1
            else:
                track_id = best_track_id
                prev_state = tracks_state[track_id]
                frame_gap = max(1, frame_index - prev_state["last_frame_index"])
                speed_px = best_distance / (frame_gap * sample_interval_sec) if best_distance is not None else None

            tracks_state[track_id] = {
                "cx": det["cx"],
                "cy": det["cy"],
                "last_frame_index": frame_index,
            }
            used_track_ids.add(track_id)
            observations.append(
                {
                    "track_id": track_id,
                    "frame_index": frame_index,
                    "time_sec": _round(frame_index * sample_interval_sec, 3),
                    "bbox_x1": det["bbox_x1"],
                    "bbox_y1": det["bbox_y1"],
                    "bbox_x2": det["bbox_x2"],
                    "bbox_y2": det["bbox_y2"],
                    "centroid_x": det["cx"],
                    "centroid_y": det["cy"],
                    "speed_px": _round(speed_px, 4) if speed_px is not None else None,
                    "confidence": _round(det["confidence"], 4),
                }
            )

    observations.sort(key=lambda row: (row["time_sec"], row["track_id"]))
    return observations

def _scene_signature_from_frame(frame: object) -> dict[str, Any] | None:
    try:
        import numpy as np  # type: ignore
    except Exception:
        return None

    if not isinstance(frame, np.ndarray) or frame.ndim < 2:
        return None

    try:
        if frame.ndim == 3 and frame.shape[2] >= 3:
            gray = (
                (0.114 * frame[:, :, 0])
                + (0.587 * frame[:, :, 1])
                + (0.299 * frame[:, :, 2])
            )
        else:
            gray = frame.astype("float64", copy=False)
    except Exception:
        return None

    if gray.size <= 0:
        return None

    h = int(gray.shape[0]) if gray.ndim >= 2 else 0
    w = int(gray.shape[1]) if gray.ndim >= 2 else 0
    if h <= 0 or w <= 0:
        return None

    # Focus cut detection on the same broadcast ROI used for robot detections.
    min_x_ratio = _clamp(float(settings.video_tracking_main_view_min_x_ratio or 0.0), 0.0, 0.99)
    max_x_ratio = _clamp(float(settings.video_tracking_main_view_max_x_ratio or 1.0), min_x_ratio + 0.01, 1.0)
    min_y_ratio = _clamp(float(settings.video_tracking_main_view_min_y_ratio or 0.0), 0.0, 0.99)
    max_y_ratio = _clamp(float(settings.video_tracking_main_view_max_y_ratio or 1.0), min_y_ratio + 0.01, 1.0)
    roi_x1 = int(round(float(w) * min_x_ratio))
    roi_x2 = int(round(float(w) * max_x_ratio))
    roi_y1 = int(round(float(h) * min_y_ratio))
    roi_y2 = int(round(float(h) * max_y_ratio))
    roi_x1 = max(0, min(roi_x1, w - 1))
    roi_x2 = max(roi_x1 + 1, min(roi_x2, w))
    roi_y1 = max(0, min(roi_y1, h - 1))
    roi_y2 = max(roi_y1 + 1, min(roi_y2, h))
    gray = gray[roi_y1:roi_y2, roi_x1:roi_x2]
    if gray.size <= 0:
        return None

    target_h = 36
    target_w = 64
    src_h, src_w = int(gray.shape[0]), int(gray.shape[1])
    block_h = max(1, src_h // target_h)
    block_w = max(1, src_w // target_w)
    crop_h = (src_h // block_h) * block_h
    crop_w = (src_w // block_w) * block_w
    cropped = gray[:crop_h, :crop_w]
    small = cropped.reshape(crop_h // block_h, block_h, crop_w // block_w, block_w).mean(axis=(1, 3))
    if small.size <= 0:
        return None

    hist, _ = np.histogram(
        small,
        bins=24,
        range=(0.0, 255.0),
    )
    hist = hist.astype("float64", copy=False)
    hist_sum = float(hist.sum())
    if hist_sum > 0.0:
        hist /= hist_sum
    return {
        "small": small,
        "hist": hist,
    }

def _scene_cut_score(
    previous_signature: dict[str, Any] | None,
    current_signature: dict[str, Any] | None,
) -> dict[str, float] | None:
    try:
        import numpy as np  # type: ignore
    except Exception:
        return None

    if not isinstance(previous_signature, dict) or not isinstance(current_signature, dict):
        return None
    prev_hist = previous_signature.get("hist")
    curr_hist = current_signature.get("hist")
    prev_small = previous_signature.get("small")
    curr_small = current_signature.get("small")
    if (
        not hasattr(prev_hist, "__len__")
        or not hasattr(curr_hist, "__len__")
        or not isinstance(prev_small, np.ndarray)
        or not isinstance(curr_small, np.ndarray)
    ):
        return None
    try:
        prev_hist_arr = np.asarray(prev_hist, dtype="float64")
        curr_hist_arr = np.asarray(curr_hist, dtype="float64")
        if prev_hist_arr.shape != curr_hist_arr.shape or prev_hist_arr.size <= 0:
            return None
        bc = float(np.sum(np.sqrt(np.clip(prev_hist_arr * curr_hist_arr, 0.0, None))))
        hist_distance = math.sqrt(max(0.0, 1.0 - bc))
        h = int(min(prev_small.shape[0], curr_small.shape[0]))
        w = int(min(prev_small.shape[1], curr_small.shape[1]))
        if h <= 0 or w <= 0:
            return None
        diff = float(np.mean(np.abs(prev_small[:h, :w] - curr_small[:h, :w])) / 255.0)
        return {
            "hist_distance": _round(hist_distance, 6),
            "frame_diff": _round(diff, 6),
        }
    except Exception:
        return None

def _bbox_overlap_ratio(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    roi_x1: float,
    roi_y1: float,
    roi_x2: float,
    roi_y2: float,
) -> float:
    box_w = max(0.0, float(x2) - float(x1))
    box_h = max(0.0, float(y2) - float(y1))
    box_area = box_w * box_h
    if box_area <= 1e-9:
        return 0.0

    ix1 = max(float(x1), float(roi_x1))
    iy1 = max(float(y1), float(roi_y1))
    ix2 = min(float(x2), float(roi_x2))
    iy2 = min(float(y2), float(roi_y2))
    inter_area = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    return _clamp(inter_area / box_area, 0.0, 1.0)

def _detect_yolo_bytetrack_observations(
    video_source: Path | str,
    *,
    sample_interval_sec: float,
    max_frames: int,
    fps_hint: float | None,
) -> tuple[list[dict], dict]:
    try:
        from ultralytics import YOLO  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "ultralytics is not installed. Install it to enable YOLO+ByteTrack."
        ) from exc

    candidate_models = _resolve_yolo_model_candidates()
    model = None
    model_source = ""
    model_errors: list[str] = []
    for candidate_source, candidate_kind in candidate_models:
        try:
            model = YOLO(candidate_source)
            model_source = candidate_source
            break
        except Exception as exc:
            model_errors.append(f"{candidate_source} ({candidate_kind}): {exc}")
    if model is None or not model_source:
        message = "; ".join(model_errors[-3:])
        raise RuntimeError(
            "Unable to load any YOLO model candidate."
            + (f" Recent errors: {message}" if message else "")
        )

    fps_value = float(fps_hint) if isinstance(fps_hint, (int, float)) and fps_hint and fps_hint > 0 else 30.0
    stride = max(1, int(round(fps_value * max(0.1, float(sample_interval_sec)))))
    effective_interval_sec = float(stride) / max(1e-6, fps_value)
    device = (settings.video_tracking_yolo_device or "").strip()
    tracker_config, tracker_config_source = _resolve_tracker_config_candidate(
        settings.video_tracking_bytetrack_config
    )

    explicit_yolo_classes = _parse_yolo_classes(settings.video_tracking_yolo_classes)
    class_filter_source = "explicit" if explicit_yolo_classes else "none"
    yolo_classes = explicit_yolo_classes
    class_names = _model_class_names(model)
    if not yolo_classes and bool(settings.video_tracking_yolo_auto_robot_classes):
        auto_classes = _resolve_auto_robot_class_ids(model)
        if auto_classes:
            yolo_classes = auto_classes
            class_filter_source = "model_robot_labels"
    if not yolo_classes and _is_coco_like_class_names(class_names):
        generic_classes = _parse_yolo_classes(settings.video_tracking_yolo_generic_allowed_classes)
        if generic_classes:
            yolo_classes = generic_classes
            class_filter_source = "generic_allowlist"
    class_filter_set = set(yolo_classes or [])

    track_kwargs: dict[str, Any] = {
        "source": str(video_source),
        "stream": True,
        "persist": True,
        "tracker": tracker_config,
        "conf": float(settings.video_tracking_yolo_conf),
        "iou": float(settings.video_tracking_yolo_iou),
        "max_det": int(settings.video_tracking_yolo_max_det),
        "imgsz": int(settings.video_tracking_yolo_imgsz),
        "vid_stride": stride,
        "verbose": False,
    }
    if device:
        track_kwargs["device"] = device
    if class_filter_set:
        track_kwargs["classes"] = sorted(class_filter_set)

    observations: list[dict] = []
    sampled_frames = 0
    detection_count = 0
    tracked_count = 0
    roi_filtered_out = 0
    roi_overlap_filtered_out = 0
    class_filtered_out = 0
    geometry_filtered_out = 0
    frame_capped_out = 0
    tracker_fallback_reason = ""
    cut_detection_enabled = bool(settings.video_tracking_cut_detection_enabled)
    cut_hist_threshold = _clamp(float(settings.video_tracking_cut_detection_hist_threshold), 0.05, 1.0)
    cut_frame_diff_threshold = _clamp(
        float(settings.video_tracking_cut_detection_frame_diff_threshold),
        0.02,
        1.0,
    )
    cut_min_gap_sec = max(0.0, float(settings.video_tracking_cut_detection_min_gap_sec))
    cut_track_id_segment_stride = max(
        100000,
        int(settings.video_tracking_cut_track_id_segment_stride or 1000000),
    )
    previous_scene_signature: dict[str, Any] | None = None
    current_scene_segment = 0
    scene_cuts_detected = 0
    last_cut_time_sec = -1e9
    scene_cut_samples: list[dict[str, Any]] = []

    min_x_ratio = _clamp(float(settings.video_tracking_main_view_min_x_ratio), 0.0, 0.95)
    max_x_ratio = _clamp(float(settings.video_tracking_main_view_max_x_ratio), min_x_ratio + 0.01, 1.0)
    min_y_ratio = _clamp(float(settings.video_tracking_main_view_min_y_ratio), 0.0, 0.95)
    max_y_ratio = _clamp(float(settings.video_tracking_main_view_max_y_ratio), min_y_ratio + 0.01, 1.0)
    min_box_overlap_ratio = _clamp(
        float(settings.video_tracking_main_view_min_box_overlap_ratio or 0.6),
        0.0,
        1.0,
    )
    min_box_area_ratio = _clamp(float(settings.video_tracking_yolo_min_box_area_ratio or 0.00008), 0.000001, 0.35)
    max_box_area_ratio = _clamp(
        float(settings.video_tracking_yolo_max_box_area_ratio or 0.09),
        min_box_area_ratio + 0.000001,
        0.98,
    )
    min_aspect_ratio = _clamp(float(settings.video_tracking_yolo_min_aspect_ratio or 0.32), 0.05, 10.0)
    max_aspect_ratio = _clamp(
        float(settings.video_tracking_yolo_max_aspect_ratio or 3.2),
        min_aspect_ratio + 0.05,
        25.0,
    )
    max_detections_per_frame = max(
        3,
        int(
            settings.video_tracking_yolo_max_detections_per_frame
            or settings.video_tracking_yolo_max_det
            or MAX_MOTION_DETECTIONS_PER_FRAME
        ),
    )

    try:
        stream = model.track(**track_kwargs)
        tracker_used = tracker_config
    except Exception as exc:
        if tracker_config != "bytetrack.yaml":
            fallback_kwargs = {**track_kwargs, "tracker": "bytetrack.yaml"}
            stream = model.track(**fallback_kwargs)
            tracker_used = "bytetrack.yaml"
            tracker_config_source = "builtin_fallback"
            tracker_fallback_reason = str(exc)
        else:
            raise

    for sampled_idx, result in enumerate(stream):
        if sampled_idx >= max_frames:
            break
        sampled_frames += 1
        frame_time_sec = float(sampled_idx * effective_interval_sec)
        if cut_detection_enabled:
            current_signature = _scene_signature_from_frame(getattr(result, "orig_img", None))
            cut_scores = _scene_cut_score(previous_scene_signature, current_signature)
            if cut_scores is not None:
                hist_distance = float(cut_scores.get("hist_distance") or 0.0)
                frame_diff = float(cut_scores.get("frame_diff") or 0.0)
                is_cut = (
                    hist_distance >= cut_hist_threshold
                    and frame_diff >= cut_frame_diff_threshold
                    and (frame_time_sec - last_cut_time_sec) >= cut_min_gap_sec
                )
                if is_cut:
                    current_scene_segment += 1
                    scene_cuts_detected += 1
                    last_cut_time_sec = frame_time_sec
                    if len(scene_cut_samples) < 20:
                        scene_cut_samples.append(
                            {
                                "time_sec": _round(frame_time_sec, 3),
                                "hist_distance": _round(hist_distance, 5),
                                "frame_diff": _round(frame_diff, 5),
                            }
                        )
            if current_signature is not None:
                previous_scene_signature = current_signature

        boxes = result.boxes
        if boxes is None or getattr(boxes, "xyxy", None) is None:
            continue
        xyxy_list = boxes.xyxy.cpu().tolist() if hasattr(boxes.xyxy, "cpu") else boxes.xyxy.tolist()
        ids_list = []
        if boxes.id is not None:
            ids_list = boxes.id.cpu().tolist() if hasattr(boxes.id, "cpu") else boxes.id.tolist()
        conf_list = []
        if boxes.conf is not None:
            conf_list = boxes.conf.cpu().tolist() if hasattr(boxes.conf, "cpu") else boxes.conf.tolist()
        cls_list = []
        if boxes.cls is not None:
            cls_list = boxes.cls.cpu().tolist() if hasattr(boxes.cls, "cpu") else boxes.cls.tolist()

        frame_h = None
        frame_w = None
        orig_shape = getattr(result, "orig_shape", None)
        if isinstance(orig_shape, (list, tuple)) and len(orig_shape) >= 2:
            try:
                frame_h = int(orig_shape[0])
                frame_w = int(orig_shape[1])
            except Exception:
                frame_h = None
                frame_w = None

        frame_rows: list[dict] = []
        roi_x1 = float(frame_w * min_x_ratio) if frame_w else None
        roi_x2 = float(frame_w * max_x_ratio) if frame_w else None
        roi_y1 = float(frame_h * min_y_ratio) if frame_h else None
        roi_y2 = float(frame_h * max_y_ratio) if frame_h else None
        for idx, coords in enumerate(xyxy_list):
            if len(coords) != 4:
                continue
            detection_count += 1
            track_id_raw = ids_list[idx] if idx < len(ids_list) else None
            if track_id_raw is None:
                continue
            tracked_count += 1
            x1, y1, x2, y2 = [float(value) for value in coords]
            if x2 <= x1 or y2 <= y1:
                continue
            class_id_raw = cls_list[idx] if idx < len(cls_list) else None
            class_id: int | None = None
            if class_id_raw is not None:
                try:
                    class_id = int(class_id_raw)
                except Exception:
                    class_id = None
            if class_filter_set:
                if class_id is None or class_id not in class_filter_set:
                    class_filtered_out += 1
                    continue
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            if frame_w and frame_h:
                if (
                    cx < (frame_w * min_x_ratio)
                    or cx > (frame_w * max_x_ratio)
                    or cy < (frame_h * min_y_ratio)
                    or cy > (frame_h * max_y_ratio)
                ):
                    roi_filtered_out += 1
                    continue
                if (
                    roi_x1 is not None
                    and roi_x2 is not None
                    and roi_y1 is not None
                    and roi_y2 is not None
                ):
                    overlap_ratio = _bbox_overlap_ratio(
                        x1,
                        y1,
                        x2,
                        y2,
                        roi_x1,
                        roi_y1,
                        roi_x2,
                        roi_y2,
                    )
                    if overlap_ratio < min_box_overlap_ratio:
                        roi_overlap_filtered_out += 1
                        continue
                box_area = max(0.0, (x2 - x1) * (y2 - y1))
                frame_area = max(1.0, float(frame_w * frame_h))
                box_area_ratio = box_area / frame_area
                aspect_ratio = (x2 - x1) / max(1e-6, (y2 - y1))
                if (
                    box_area_ratio < min_box_area_ratio
                    or box_area_ratio > max_box_area_ratio
                    or aspect_ratio < min_aspect_ratio
                    or aspect_ratio > max_aspect_ratio
                ):
                    geometry_filtered_out += 1
                    continue
            conf_value = float(conf_list[idx]) if idx < len(conf_list) else 0.45
            frame_rows.append(
                {
                    "track_id": int(track_id_raw) + (current_scene_segment * cut_track_id_segment_stride),
                    "frame_index": int(sampled_idx),
                    "time_sec": _round(sampled_idx * effective_interval_sec, 3),
                    "bbox_x1": x1,
                    "bbox_y1": y1,
                    "bbox_x2": x2,
                    "bbox_y2": y2,
                    "centroid_x": _round(cx, 4),
                    "centroid_y": _round(cy, 4),
                    "speed_px": None,
                    "confidence": _round(_clamp(conf_value, 0.05, 0.999), 4),
                    "class_id": class_id,
                    "class_label": class_names.get(class_id) if class_id is not None else None,
                    "scene_segment": int(current_scene_segment),
                }
            )

        if not frame_rows:
            continue
        frame_rows.sort(key=lambda row: float(row.get("confidence") or 0.0), reverse=True)
        if len(frame_rows) > max_detections_per_frame:
            frame_capped_out += int(len(frame_rows) - max_detections_per_frame)
            frame_rows = frame_rows[:max_detections_per_frame]
        observations.extend(frame_rows)

    observations.sort(key=lambda row: (row["time_sec"], row["track_id"]))
    observations_before_prune = len(observations)
    prune_min_obs = int(settings.video_tracking_yolo_min_track_observations or 3)
    prune_min_conf = float(settings.video_tracking_yolo_min_track_avg_confidence or 0.24)
    if class_filter_source in {"none", "generic_allowlist"}:
        prune_min_obs = max(prune_min_obs, 6)
        prune_min_conf = max(prune_min_conf, 0.3)
    observations, prune_meta = _prune_yolo_track_noise(
        observations,
        min_track_observations=prune_min_obs,
        min_track_avg_confidence=prune_min_conf,
    )
    return observations, {
        "model_source": model_source,
        "model_candidates": [source for source, _ in candidate_models],
        "tracker": tracker_used,
        "tracker_requested": settings.video_tracking_bytetrack_config,
        "tracker_source": tracker_config_source,
        "tracker_fallback_reason": tracker_fallback_reason or None,
        "vid_stride": stride,
        "effective_interval_sec": _round(effective_interval_sec, 4),
        "classes": sorted(class_filter_set),
        "class_filter_source": class_filter_source,
        "class_names": {str(class_id): class_names[class_id] for class_id in sorted(class_filter_set)},
        "sampled_frames": sampled_frames,
        "detections": detection_count,
        "tracked_detections": tracked_count,
        "roi_filtered_detections": int(roi_filtered_out),
        "roi_overlap_filtered_detections": int(roi_overlap_filtered_out),
        "class_filtered_detections": int(class_filtered_out),
        "geometry_filtered_detections": int(geometry_filtered_out),
        "frame_capped_detections": int(frame_capped_out),
        "observations_before_prune": int(observations_before_prune),
        "observations_after_prune": int(len(observations)),
        "track_noise_prune": prune_meta,
        "max_detections_per_frame": int(max_detections_per_frame),
        "box_area_ratio": {
            "min": _round(min_box_area_ratio, 6),
            "max": _round(max_box_area_ratio, 6),
        },
        "aspect_ratio": {
            "min": _round(min_aspect_ratio, 4),
            "max": _round(max_aspect_ratio, 4),
        },
        "main_view_roi": {
            "min_x_ratio": _round(min_x_ratio, 4),
            "max_x_ratio": _round(max_x_ratio, 4),
            "min_y_ratio": _round(min_y_ratio, 4),
            "max_y_ratio": _round(max_y_ratio, 4),
            "min_box_overlap_ratio": _round(min_box_overlap_ratio, 4),
        },
        "scene_cut_detection": {
            "enabled": bool(cut_detection_enabled),
            "hist_threshold": _round(cut_hist_threshold, 4),
            "frame_diff_threshold": _round(cut_frame_diff_threshold, 4),
            "min_gap_sec": _round(cut_min_gap_sec, 3),
            "track_id_segment_stride": int(cut_track_id_segment_stride),
            "cuts_detected": int(scene_cuts_detected),
            "samples": scene_cut_samples,
        },
    }
