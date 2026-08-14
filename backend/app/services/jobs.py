# Match analysis orchestrator.
#
# Sub-modules:
# score_breakdown      – score breakdown + cycle fallback helpers
# tracking_detection   – YOLO/ByteTrack + motion detection
# bumper_ocr           – bumper-number OCR
# track_analysis       – field projection, track summary, assignment, defense, metrics
from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.config import settings
from app.db import models
from app.db.session import SessionLocal
from app.services.analysis.hash import build_analysis_params_hash as build_analysis_params_hash_core
from app.services.analysis.pipeline_quality import _stage_evaluate_quality
from app.services.analysis.pipeline_types import (
    _PersistTracksResult,
    _StageContext,
    _TeamMetricsResult,
    _TrackingResult,
    _VideoAndPhasesResult,
)
from app.services.events.classifier import (
    TransitionEventModel,
    load_transition_event_model,
)
from app.services.game_config import load_game_config
from app.services.media.retention import run_media_retention
from app.services.vision.perimeter_resolver import resolve_perimeter_type_for_event_profile
from app.services.media.storage_cleanup import cleanup_match_media
from app.services.utils import MEDIA_ROOT, _as_float, _clamp, _round
from app.services.vision.video_extraction import (
    VideoExtractionError,
    cleanup_prepared_youtube_source,
    extract_sample_frames,
    media_url_for_path,
    prepare_youtube_video_source,
)
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from app.services.season_config import REBUILT_HUB_SCORE_GRACE_SEC
from app.services.scoring.breakdown import (
    _fetch_match_payload_from_tba,
    _infer_rebuilt_shift1_active_alliance as _infer_rebuilt_shift1_active_alliance,
    _merge_time_windows,
    _rebuilt_expected_active_hub_duration_sec,
    _rebuilt_hub_activity_windows_by_alliance as _rebuilt_hub_activity_windows_by_alliance,
    _resolve_rebuilt_hub_activity_payload,
    _resolve_sparse_cycle_fallbacks,
    _score_breakdown_cycle_fallbacks_from_match_payload as _score_breakdown_cycle_fallbacks_from_match_payload,
    _season_year_from_event_key,
    _time_in_windows,
)
from app.services.vision.model_provisioning import is_generic_fallback_model
from app.services.vision.tracking_detection import (
    _dense_retry_plan,
    _detect_motion_observations,
    _detect_yolo_bytetrack_observations,
    _parse_csv_tokens,
    _parse_yolo_classes,
    _resolve_sampling_plan,
    _should_dense_retry,
)
from app.services.vision.bumper_ocr import (
    _infer_track_bumper_numbers,
)
from app.services.vision.track_analysis import (
    _annotate_observations_with_field,
    _assign_tracks_to_teams,
    _build_track_summaries,
    _compute_field_speeds,
    _compute_team_metrics_and_events,
    _detect_defensive_interactions,
    _prune_observations_to_field_bounds,
    _resolve_phase_window_seconds,
)

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_INTERVAL_SEC = 2.0
DEFAULT_MAX_SAMPLE_FRAMES = 120
DEFAULT_MAX_ARTIFACT_SAMPLE_FRAMES = 30
MAX_TRACK_MATCH_DISTANCE_PX = 110.0
MAX_TRACK_GAP_FRAMES = 2
MIN_TRACK_OBS_FOR_ASSIGNMENT = 4
DEFAULT_ANALYSIS_VERSION = "video_v3_tracks"
RUN_ACTIVE_OR_DONE_STATUSES = {"running", "completed"}

def _run_media_cleanup_best_effort(reason: str) -> None:
    try:
        run_media_retention(force=False, reason=reason)
    except (OSError, RuntimeError, ValueError, SQLAlchemyError) as exc:
        # Cleanup must never break match analysis execution.
        logger.warning("media cleanup skipped reason=%s error=%s", reason, exc)

def _latest_matching_run(
    db,
    *,
    match_key: str,
    analysis_version: str,
    params_hash: str,
    calibration_id: int,
):
    return (
        db.query(models.AnalysisRun, models.AnalysisRunContext)
        .join(models.AnalysisRunContext, models.AnalysisRunContext.run_id == models.AnalysisRun.id)
        .filter(
            models.AnalysisRun.match_key == match_key,
            models.AnalysisRunContext.match_key == match_key,
            models.AnalysisRunContext.analysis_version == analysis_version,
            models.AnalysisRunContext.params_hash == params_hash,
            models.AnalysisRunContext.calibration_id == calibration_id,
            models.AnalysisRun.status.in_(RUN_ACTIVE_OR_DONE_STATUSES),
        )
        .order_by(models.AnalysisRun.created_at.desc(), models.AnalysisRun.id.desc())
        .first()
    )

_TIMECODE_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)(h|m|s)")

def _parse_timecode_seconds(raw_value: str | None) -> float | None:
    if raw_value is None:
        return None
    token = str(raw_value).strip().lower()
    if not token:
        return None

    normalized = token
    if normalized.startswith("pt"):
        normalized = normalized[2:]
    if normalized.endswith("s") and normalized.count("s") == 1 and all(ch.isdigit() or ch == "." for ch in normalized[:-1]):
        normalized = normalized[:-1]

    try:
        parsed = float(normalized)
        if parsed >= 0.0:
            return parsed
    except ValueError:
        pass

    if ":" in token:
        parts = [piece.strip() for piece in token.split(":")]
        if 1 <= len(parts) <= 3 and all(part and part.replace(".", "", 1).isdigit() for part in parts):
            try:
                values = [float(part) for part in parts]
                if len(values) == 3:
                    total = (values[0] * 3600.0) + (values[1] * 60.0) + values[2]
                elif len(values) == 2:
                    total = (values[0] * 60.0) + values[1]
                else:
                    total = values[0]
                if total >= 0.0:
                    return total
            except ValueError:
                pass

    matches = _TIMECODE_TOKEN_RE.findall(token)
    if not matches:
        return None
    total_sec = 0.0
    for value_token, unit_token in matches:
        try:
            value = float(value_token)
        except ValueError:
            return None
        if unit_token == "h":  # nosec B105
            total_sec += value * 3600.0
        elif unit_token == "m":  # nosec B105
            total_sec += value * 60.0
        else:
            total_sec += value
    return total_sec if total_sec >= 0.0 else None

def _youtube_start_offset_seconds(youtube_url: str | None) -> float | None:
    if not isinstance(youtube_url, str) or not youtube_url.strip():
        return None
    raw_url = youtube_url.strip()
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return None

    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in ("t", "start", "time_continue"):
        for raw in (query.get(key) or []):
            parsed_sec = _parse_timecode_seconds(raw)
            if parsed_sec is not None:
                return parsed_sec

    fragment = (parsed.fragment or "").strip()
    if fragment:
        fragment_query = parse_qs(fragment, keep_blank_values=False)
        for key in ("t", "start", "time_continue"):
            for raw in (fragment_query.get(key) or []):
                parsed_sec = _parse_timecode_seconds(raw)
                if parsed_sec is not None:
                    return parsed_sec
        parsed_fragment = _parse_timecode_seconds(fragment)
        if parsed_fragment is not None:
            return parsed_fragment

    # Some legacy rows store malformed URLs like "...watch?v=ID?t=8270".
    # Fall back to token scanning to recover anchor offsets robustly.
    token_match = re.search(r"[?&#]\s*(t|start|time_continue)=([^&#]+)", raw_url, flags=re.IGNORECASE)
    if token_match:
        parsed_sec = _parse_timecode_seconds(token_match.group(2))
        if parsed_sec is not None:
            return parsed_sec
    return None

def _calibration_anchor_reuse_count(
    db,
    *,
    event_key: str,
    frame_time_sec: float,
    tolerance_sec: float = 1.0,
) -> int:
    tolerance = max(0.0, float(tolerance_sec))
    lo = float(frame_time_sec) - tolerance
    hi = float(frame_time_sec) + tolerance
    return int(
        db.query(func.count(models.FieldCalibration.id))
        .filter(
            models.FieldCalibration.event_key == event_key,
            models.FieldCalibration.frame_time_sec.isnot(None),
            models.FieldCalibration.frame_time_sec.between(lo, hi),
        )
        .scalar()
        or 0
    )

def _offset_phase_windows(
    phase_windows: dict[str, Any],
    *,
    offset_sec: float,
    duration_sec: float,
) -> dict[str, Any]:
    if not isinstance(phase_windows, dict):
        return {}
    offset = max(0.0, float(offset_sec))
    duration = max(1.0, float(duration_sec))
    shifted = dict(phase_windows)
    for key in (
        "auto_end_sec",
        "endgame_start_sec",
        "teleop_scoring_start_sec",
        "teleop_scoring_end_sec",
    ):
        raw_value = phase_windows.get(key)
        if isinstance(raw_value, (int, float)):
            shifted[key] = _round(_clamp(float(raw_value) + offset, 0.0, duration), 3)
    if isinstance(shifted.get("teleop_scoring_start_sec"), (int, float)) and isinstance(
        shifted.get("teleop_scoring_end_sec"), (int, float)
    ):
        shifted["teleop_scoring_duration_sec"] = _round(
            max(0.0, float(shifted["teleop_scoring_end_sec"]) - float(shifted["teleop_scoring_start_sec"])),
            3,
        )
    if isinstance(shifted.get("endgame_start_sec"), (int, float)):
        shifted["endgame_window_sec"] = _round(
            max(0.0, duration - float(shifted["endgame_start_sec"])),
            3,
        )
    return shifted

def _resolve_interlude_trim_window(
    *,
    db,
    match: models.Match,
    calibration: models.FieldCalibration,
    youtube_url: str | None,
    video_duration_sec: float,
    expected_match_duration_sec: float,
) -> dict[str, Any]:
    enabled = bool(settings.video_tracking_interlude_trim_enabled)
    duration = max(0.0, float(video_duration_sec))
    expected_match_sec = max(30.0, float(expected_match_duration_sec))
    min_long_video_sec = max(
        expected_match_sec + 20.0,
        float(settings.video_tracking_interlude_trim_long_video_min_sec or 260.0),
    )
    pre_roll_sec = max(0.0, float(settings.video_tracking_interlude_trim_pre_roll_sec or 0.0))
    post_roll_sec = max(0.0, float(settings.video_tracking_interlude_trim_post_roll_sec or 0.0))
    target_window_sec = expected_match_sec + pre_roll_sec + post_roll_sec
    max_anchor_reuse = max(1, int(settings.video_tracking_interlude_trim_calibration_anchor_max_reuse or 3))
    anchor_fraction = _clamp(
        float(settings.video_tracking_interlude_trim_calibration_anchor_fraction or 0.5),
        0.05,
        0.95,
    )

    details: dict[str, Any] = {
        "enabled": enabled,
        "applied": False,
        "reason": "disabled",
        "source": None,
        "video_duration_sec": _round(duration, 3),
        "expected_match_duration_sec": _round(expected_match_sec, 3),
        "long_video_threshold_sec": _round(min_long_video_sec, 3),
        "pre_roll_sec": _round(pre_roll_sec, 3),
        "post_roll_sec": _round(post_roll_sec, 3),
        "window_target_sec": _round(target_window_sec, 3),
    }
    if not enabled:
        return details
    if duration <= 0.0:
        details["reason"] = "invalid_video_duration"
        return details
    if duration < min_long_video_sec:
        details["reason"] = "video_not_long_enough"
        return details

    match_start_estimate_sec: float | None = None
    explicit_start_sec = _youtube_start_offset_seconds(youtube_url)
    if explicit_start_sec is not None:
        match_start_estimate_sec = float(explicit_start_sec)
        details["source"] = "youtube_url_timestamp"
    elif bool(settings.video_tracking_interlude_trim_allow_calibration_anchor):
        frame_time_raw = calibration.frame_time_sec
        if isinstance(frame_time_raw, (int, float)) and float(frame_time_raw) >= 0.0:
            frame_time_sec = float(frame_time_raw)
            reuse_count = _calibration_anchor_reuse_count(
                db,
                event_key=str(match.event_key or ""),
                frame_time_sec=frame_time_sec,
            )
            details["calibration_anchor_frame_time_sec"] = _round(frame_time_sec, 3)
            details["calibration_anchor_reuse_count"] = int(reuse_count)
            details["calibration_anchor_max_reuse"] = int(max_anchor_reuse)
            if reuse_count <= max_anchor_reuse:
                match_start_estimate_sec = frame_time_sec - (expected_match_sec * anchor_fraction)
                details["source"] = "calibration_frame_anchor"
            else:
                details["reason"] = "calibration_anchor_reused_too_often"
                return details
        else:
            details["reason"] = "calibration_anchor_missing"
            return details
    else:
        details["reason"] = "no_explicit_anchor"
        return details

    if match_start_estimate_sec is None:
        details["reason"] = "unable_to_estimate_match_start"
        return details

    start_sec = _clamp(float(match_start_estimate_sec) - pre_roll_sec, 0.0, duration)
    end_sec = _clamp(
        float(match_start_estimate_sec) + expected_match_sec + post_roll_sec,
        start_sec + 1.0,
        duration,
    )
    window_duration_sec = max(0.0, end_sec - start_sec)
    if window_duration_sec <= 30.0:
        details["reason"] = "window_too_short_after_clamp"
        return details
    if window_duration_sec >= (duration * 0.98):
        details["reason"] = "window_near_full_duration"
        return details

    details.update(
        {
            "applied": True,
            "reason": "trimmed_to_estimated_match_window",
            "start_sec": _round(start_sec, 3),
            "end_sec": _round(end_sec, 3),
            "window_duration_sec": _round(window_duration_sec, 3),
            "estimated_match_start_sec": _round(float(match_start_estimate_sec), 3),
            "match_start_in_window_sec": _round(max(0.0, float(match_start_estimate_sec) - start_sec), 3),
            "explicit_url_timestamp_sec": (
                _round(float(explicit_start_sec), 3) if explicit_start_sec is not None else None
            ),
        }
    )
    return details

def _trimmed_window_weak_tracking_signal(
    *,
    observations_count: int,
    tracks_count: int,
    team_count: int,
) -> tuple[bool, dict[str, Any]]:
    enabled = bool(settings.video_tracking_interlude_trim_weak_signal_fallback_enabled)
    bounded_team_count = max(1, int(team_count))
    min_observations_per_team = max(
        1,
        int(settings.video_tracking_interlude_trim_weak_signal_min_observations_per_team or 8),
    )
    min_tracks = max(1, int(settings.video_tracking_interlude_trim_weak_signal_min_tracks or 4))
    min_total_observations = min_observations_per_team * bounded_team_count
    observations = max(0, int(observations_count))
    tracks = max(0, int(tracks_count))
    trigger_reasons: list[str] = []
    if observations < min_total_observations:
        trigger_reasons.append("observations_below_threshold")
    if tracks < min_tracks:
        trigger_reasons.append("tracks_below_threshold")
    should_fallback = bool(enabled and trigger_reasons)
    return should_fallback, {
        "enabled": enabled,
        "applied": False,
        "trigger_reasons": trigger_reasons,
        "team_count": bounded_team_count,
        "observations_count": observations,
        "tracks_count": tracks,
        "min_observations_per_team": min_observations_per_team,
        "min_total_observations": min_total_observations,
        "min_tracks": min_tracks,
    }

def build_analysis_params_hash(
    *,
    analysis_version: str = DEFAULT_ANALYSIS_VERSION,
    calibration_id: int | None = None,
    perimeter_type: str | None = None,
    sampling_overrides: dict[str, Any] | None = None,
) -> str:
    sampling_config = {
        "mode": str(settings.video_tracking_sampling_mode or "adaptive").strip().lower(),
        "sample_interval_sec": float(settings.video_tracking_sample_interval_sec or DEFAULT_SAMPLE_INTERVAL_SEC),
        "dense_interval_sec": float(settings.video_tracking_dense_interval_sec or 0.8),
        "adaptive_min_interval_sec": float(settings.video_tracking_adaptive_min_interval_sec or 0.75),
        "adaptive_max_interval_sec": float(settings.video_tracking_adaptive_max_interval_sec or 2.0),
        "max_sample_frames": int(settings.video_tracking_max_sample_frames or DEFAULT_MAX_SAMPLE_FRAMES),
        "max_artifact_frames": int(
            settings.video_tracking_max_artifact_sample_frames or DEFAULT_MAX_ARTIFACT_SAMPLE_FRAMES
        ),
    }
    normalized_sampling_override = _normalize_sampling_override(sampling_overrides)
    if normalized_sampling_override:
        sampling_config["override"] = normalized_sampling_override

    return build_analysis_params_hash_core(
        analysis_version=analysis_version,
        calibration_id=calibration_id,
        sampling=sampling_config,
        tracking={
            "mode": settings.video_tracking_mode,
            "max_track_match_distance_px": MAX_TRACK_MATCH_DISTANCE_PX,
            "max_track_gap_frames": MAX_TRACK_GAP_FRAMES,
            "min_track_obs_assignment": MIN_TRACK_OBS_FOR_ASSIGNMENT,
            "yolo_model": (settings.video_tracking_yolo_model or "").strip() or "yolo11n.pt",
            "yolo_model_fallbacks": _parse_csv_tokens(settings.video_tracking_yolo_model_fallbacks),
            "yolo_conf": float(settings.video_tracking_yolo_conf),
            "yolo_iou": float(settings.video_tracking_yolo_iou),
            "yolo_max_det": int(settings.video_tracking_yolo_max_det),
            "yolo_imgsz": int(settings.video_tracking_yolo_imgsz),
            "yolo_classes": _parse_yolo_classes(settings.video_tracking_yolo_classes) or [],
            "yolo_generic_allowed_classes": _parse_yolo_classes(
                settings.video_tracking_yolo_generic_allowed_classes
            )
            or [],
            "yolo_auto_robot_classes": bool(settings.video_tracking_yolo_auto_robot_classes),
            "yolo_min_box_area_ratio": float(settings.video_tracking_yolo_min_box_area_ratio),
            "yolo_max_box_area_ratio": float(settings.video_tracking_yolo_max_box_area_ratio),
            "yolo_min_aspect_ratio": float(settings.video_tracking_yolo_min_aspect_ratio),
            "yolo_max_aspect_ratio": float(settings.video_tracking_yolo_max_aspect_ratio),
            "yolo_max_detections_per_frame": int(settings.video_tracking_yolo_max_detections_per_frame),
            "yolo_min_track_observations": int(settings.video_tracking_yolo_min_track_observations),
            "yolo_min_track_avg_confidence": float(settings.video_tracking_yolo_min_track_avg_confidence),
            "bytetrack_config": settings.video_tracking_bytetrack_config,
            "dense_retry_enabled": bool(settings.video_tracking_dense_retry_enabled),
            "dense_retry_interval_sec": float(settings.video_tracking_dense_retry_interval_sec or 0.7),
            "dense_retry_max_frames": int(settings.video_tracking_dense_retry_max_frames or 220),
            "dense_retry_min_observations_per_team": int(
                settings.video_tracking_dense_retry_min_observations_per_team or 18
            ),
            "dense_retry_min_tracks": int(settings.video_tracking_dense_retry_min_tracks or 4),
            "main_view_min_x_ratio": float(settings.video_tracking_main_view_min_x_ratio),
            "main_view_max_x_ratio": float(settings.video_tracking_main_view_max_x_ratio),
            "main_view_min_y_ratio": float(settings.video_tracking_main_view_min_y_ratio),
            "main_view_max_y_ratio": float(settings.video_tracking_main_view_max_y_ratio),
            "main_view_min_box_overlap_ratio": float(settings.video_tracking_main_view_min_box_overlap_ratio),
            "cut_detection_enabled": bool(settings.video_tracking_cut_detection_enabled),
            "cut_detection_hist_threshold": float(settings.video_tracking_cut_detection_hist_threshold),
            "cut_detection_frame_diff_threshold": float(settings.video_tracking_cut_detection_frame_diff_threshold),
            "cut_detection_min_gap_sec": float(settings.video_tracking_cut_detection_min_gap_sec),
            "cut_track_id_segment_stride": int(settings.video_tracking_cut_track_id_segment_stride),
            "interlude_trim_enabled": bool(settings.video_tracking_interlude_trim_enabled),
            "interlude_trim_long_video_min_sec": float(settings.video_tracking_interlude_trim_long_video_min_sec),
            "interlude_trim_pre_roll_sec": float(settings.video_tracking_interlude_trim_pre_roll_sec),
            "interlude_trim_post_roll_sec": float(settings.video_tracking_interlude_trim_post_roll_sec),
            "interlude_trim_allow_calibration_anchor": bool(
                settings.video_tracking_interlude_trim_allow_calibration_anchor
            ),
            "interlude_trim_calibration_anchor_fraction": float(
                settings.video_tracking_interlude_trim_calibration_anchor_fraction
            ),
            "interlude_trim_calibration_anchor_max_reuse": int(
                settings.video_tracking_interlude_trim_calibration_anchor_max_reuse
            ),
            "interlude_trim_weak_signal_fallback_enabled": bool(
                settings.video_tracking_interlude_trim_weak_signal_fallback_enabled
            ),
            "interlude_trim_weak_signal_min_observations_per_team": int(
                settings.video_tracking_interlude_trim_weak_signal_min_observations_per_team
            ),
            "interlude_trim_weak_signal_min_tracks": int(
                settings.video_tracking_interlude_trim_weak_signal_min_tracks
            ),
            "field_bounds_prune_enabled": bool(settings.video_tracking_field_bounds_prune_enabled),
            "field_bounds_prune_margin_m": float(settings.video_tracking_field_bounds_prune_margin_m),
            "field_bounds_drop_unprojectable": bool(settings.video_tracking_field_bounds_drop_unprojectable),
            "enable_bumper_ocr": bool(settings.video_tracking_enable_bumper_ocr),
            "bumper_ocr_language": settings.video_tracking_bumper_ocr_language,
            "bumper_ocr_max_samples_per_track": int(settings.video_tracking_bumper_ocr_max_samples_per_track),
            "bumper_ocr_min_text_conf": float(settings.video_tracking_bumper_ocr_min_text_conf),
            "bumper_ocr_expand_ratio": float(settings.video_tracking_bumper_ocr_expand_ratio),
            "bumper_ocr_bottom_band_ratio": float(settings.video_tracking_bumper_ocr_bottom_band_ratio),
            "event_classifier_enabled": bool(settings.video_event_classifier_enabled),
            "event_classifier_model_path": str(settings.video_event_classifier_model_path or ""),
            "event_classifier_conf_threshold": float(settings.video_event_classifier_conf_threshold),
            "event_classifier_margin_threshold": float(settings.video_event_classifier_margin_threshold),
            "event_classifier_prefer_model": bool(settings.video_event_classifier_prefer_model),
        },
        perimeter_type=perimeter_type,
    )

def _normalize_sampling_override(raw_override: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw_override, dict):
        return {}
    normalized: dict[str, Any] = {}
    sample_interval = _as_float(raw_override.get("sample_interval_sec"))
    if sample_interval is not None and sample_interval > 0.0:
        normalized["sample_interval_sec"] = _round(_clamp(float(sample_interval), 0.2, 10.0), 4)
    max_frames = _as_float(raw_override.get("max_frames"))
    if max_frames is not None and max_frames > 0:
        normalized["max_frames"] = int(_clamp(float(max_frames), 1.0, 5000.0))
    max_artifact_frames = _as_float(raw_override.get("max_artifact_frames"))
    if max_artifact_frames is not None and max_artifact_frames > 0:
        normalized["max_artifact_frames"] = int(_clamp(float(max_artifact_frames), 1.0, 500.0))
    return normalized

def _stage_resolve_video_and_phases(ctx: _StageContext) -> _VideoAndPhasesResult:
    # Prepare the video source, compute phase windows, and build the sampling plan.
    #
    # Downloads / resolves the YouTube video, probes metadata, determines the
    # interlude-trim window, resolves game-phase boundaries, computes cycle
    # fallback durations, and assembles the sampling plan.
    prepared_video_source = prepare_youtube_video_source(
        match_key=ctx.match_key,
        youtube_url=str(ctx.youtube_video.url),
        force_download_refresh=False,
    )
    video_source = prepared_video_source.video_source
    video_meta = prepared_video_source.video_metadata
    video_source_mode = str(prepared_video_source.source_mode or "youtube_stream")
    stream_fallback_reason = (
        str(prepared_video_source.stream_error)
        if prepared_video_source.stream_error
        else None
    )
    streamed_video = video_source_mode == "youtube_stream"
    resolved_video_source = str(video_source)
    ctx.mark_stage("resolve_stream_and_probe_video")

    raw_video_duration_sec = float(video_meta.get("duration_sec") or 0.0)
    config = load_game_config()
    expected_match_duration_sec = max(30.0, float(getattr(config.phases, "total_sec", 150.0)))
    interlude_trim_window = _resolve_interlude_trim_window(
        db=ctx.db,
        match=ctx.match,
        calibration=ctx.calibration,
        youtube_url=str(ctx.youtube_video.url),
        video_duration_sec=raw_video_duration_sec,
        expected_match_duration_sec=expected_match_duration_sec,
    )
    analysis_duration_sec = (
        float(interlude_trim_window.get("window_duration_sec") or 0.0)
        if bool(interlude_trim_window.get("applied"))
        else raw_video_duration_sec
    )
    analysis_duration_sec = max(1.0, float(analysis_duration_sec))
    phase_windows = _resolve_phase_window_seconds(
        ctx.db,
        ctx.match,
        duration_sec=analysis_duration_sec,
    )
    if bool(interlude_trim_window.get("applied")):
        phase_windows = _offset_phase_windows(
            phase_windows,
            offset_sec=float(interlude_trim_window.get("match_start_in_window_sec") or 0.0),
            duration_sec=analysis_duration_sec,
        )
    duration_sec = analysis_duration_sec
    season_year = _season_year_from_event_key(ctx.match.event_key)
    match_payload = (
        _fetch_match_payload_from_tba(ctx.match_key)
        if isinstance(season_year, int) and season_year >= 2026
        else None
    )
    hub_activity_payload = (
        _resolve_rebuilt_hub_activity_payload(
            match_key=ctx.match_key,
            auto_end_sec=float(phase_windows.get("auto_end_sec") or 0.0),
            duration_sec=duration_sec,
            match_payload=match_payload,
        )
        if isinstance(season_year, int) and season_year >= 2026
        else None
    )
    cycle_fallback_duration_sec = max(1e-6, float(phase_windows.get("teleop_scoring_duration_sec") or 0.0))
    if isinstance(season_year, int) and season_year >= 2026:
        cycle_fallback_duration_sec = _rebuilt_expected_active_hub_duration_sec(
            auto_end_sec=float(phase_windows.get("auto_end_sec") or 0.0),
            duration_sec=duration_sec,
            include_post_deactivate_grace=True,
        )
    if isinstance(hub_activity_payload, dict):
        shift1_active_token = str(hub_activity_payload.get("shift1_active_alliance") or "").strip().lower()
        attributed_durations = (
            hub_activity_payload.get("attributed_duration_by_alliance")
            if isinstance(hub_activity_payload.get("attributed_duration_by_alliance"), dict)
            else {}
        )
        derived_values = [
            float(value)
            for value in attributed_durations.values()
            if isinstance(value, (int, float)) and float(value) > 0.0
        ]
        if shift1_active_token in {"red", "blue"} and derived_values:
            cycle_fallback_duration_sec = max(1e-6, float(sum(derived_values) / len(derived_values)))
    sparse_cycle_fallbacks_by_team = _resolve_sparse_cycle_fallbacks(
        ctx.db,
        match=ctx.match,
        match_teams=ctx.match_teams,
        teleop_duration_sec=cycle_fallback_duration_sec,
        match_payload=match_payload,
    )
    sampling_plan = _resolve_sampling_plan(
        duration_sec=duration_sec,
        phase_windows=phase_windows,
    )
    if ctx.normalized_sampling_override:
        _interval_overridden = False
        if isinstance(ctx.normalized_sampling_override.get("sample_interval_sec"), (int, float)):
            new_interval = float(ctx.normalized_sampling_override["sample_interval_sec"])
            if new_interval != sampling_plan["sample_interval_sec"]:
                _interval_overridden = True
            sampling_plan["sample_interval_sec"] = new_interval
        if isinstance(ctx.normalized_sampling_override.get("max_frames"), int):
            sampling_plan["max_frames"] = int(ctx.normalized_sampling_override["max_frames"])
        if isinstance(ctx.normalized_sampling_override.get("max_artifact_frames"), int):
            sampling_plan["max_artifact_frames"] = int(ctx.normalized_sampling_override["max_artifact_frames"])
        if _interval_overridden:
            sampling_plan["mode"] = "forced_dense_reprocess"
            sampling_plan["reason"] = "low_quality_reprocess_dense_sampling"
    ctx.mark_stage("phase_windows_and_sparse_cycle_fallbacks")

    return _VideoAndPhasesResult(
        prepared_video_source=prepared_video_source,
        video_source=video_source,
        video_meta=video_meta,
        video_source_mode=video_source_mode,
        stream_fallback_reason=stream_fallback_reason,
        streamed_video=streamed_video,
        resolved_video_source=resolved_video_source,
        raw_video_duration_sec=raw_video_duration_sec,
        duration_sec=duration_sec,
        interlude_trim_window=interlude_trim_window,
        phase_windows=phase_windows,
        sampling_plan=sampling_plan,
        season_year=season_year,
        match_payload=match_payload,
        hub_activity_payload=hub_activity_payload,
        cycle_fallback_duration_sec=cycle_fallback_duration_sec,
        sparse_cycle_fallbacks_by_team=sparse_cycle_fallbacks_by_team,
        config=config,
    )

def _stage_run_tracking(
    ctx: _StageContext,
    vp: _VideoAndPhasesResult,
) -> _TrackingResult:
    # Run frame sampling, object detection, tracking, OCR and team assignment.
    #
    # Handles interlude-trim fallback on weak signal, dense retry on low
    # coverage, bumper-number OCR, and spatial team assignment.
    sample_interval_sec = float(vp.sampling_plan["sample_interval_sec"])
    sample_max_frames = int(vp.sampling_plan["max_frames"])
    artifact_max_frames = int(vp.sampling_plan["max_artifact_frames"])

    calibration_x = [float(point.get("x", 0.0)) for point in ctx.calibration.field_points]
    calibration_y = [float(point.get("y", 0.0)) for point in ctx.calibration.field_points]
    min_cal_x = min(calibration_x) if calibration_x else 0.0
    max_cal_x = max(calibration_x) if calibration_x else 1.0
    min_cal_y = min(calibration_y) if calibration_y else 0.0
    max_cal_y = max(calibration_y) if calibration_y else 1.0

    interlude_trim_active = bool(vp.interlude_trim_window.get("applied"))
    interlude_trim_start_sec = (
        float(vp.interlude_trim_window.get("start_sec") or 0.0)
        if interlude_trim_active
        else None
    )
    interlude_trim_duration_sec = (
        float(vp.interlude_trim_window.get("window_duration_sec") or 0.0)
        if interlude_trim_active
        else None
    )
    interlude_trim_fallback_used = False
    interlude_trim_weak_signal_fallback_meta: dict[str, Any] = {
        "enabled": bool(settings.video_tracking_interlude_trim_weak_signal_fallback_enabled),
        "applied": False,
        "trigger_reasons": [],
    }

    config_length = float(vp.config.field.length_m)
    config_width = float(vp.config.field.width_m)
    cal_span_x = max(1e-6, max_cal_x - min_cal_x)
    cal_span_y = max(1e-6, max_cal_y - min_cal_y)
    scale_x = config_length / cal_span_x
    scale_y = config_width / cal_span_y

    tracking_mode = (settings.video_tracking_mode or "motion_v1").strip().lower()

    def _run_tracking_pass(
        *,
        pass_interval_sec: float,
        pass_max_frames: int,
        pass_name: str,
        pass_reason: str,
    ) -> tuple[list[Path], list[dict], dict[int, dict], str, dict]:
        nonlocal interlude_trim_active, interlude_trim_fallback_used
        pass_window_start_sec = interlude_trim_start_sec if interlude_trim_active else None
        pass_window_duration_sec = interlude_trim_duration_sec if interlude_trim_active else None
        trim_fallback_reason: str | None = None
        try:
            pass_sample_frames = extract_sample_frames(
                match_key=ctx.match_key,
                video_source=vp.video_source,
                run_id=ctx.run.id,
                sample_every_sec=pass_interval_sec,
                max_frames=pass_max_frames,
                start_sec=pass_window_start_sec,
                duration_sec=pass_window_duration_sec,
            )
        except VideoExtractionError as exc:
            if not interlude_trim_active:
                raise
            interlude_trim_active = False
            interlude_trim_fallback_used = True
            trim_fallback_reason = str(exc) or exc.__class__.__name__
            pass_window_start_sec = None
            pass_window_duration_sec = None
            pass_sample_frames = extract_sample_frames(
                match_key=ctx.match_key,
                video_source=vp.video_source,
                run_id=ctx.run.id,
                sample_every_sec=pass_interval_sec,
                max_frames=pass_max_frames,
            )
        pass_tracking_backend = "motion_v1"  # nosec B105
        pass_tracking_meta: dict[str, Any] = {
            "requested_mode": tracking_mode,
            "sampling_mode": str(vp.sampling_plan.get("mode") or "adaptive"),
            "sampling_reason": pass_reason,
            "pass_name": pass_name,
            "sample_interval_sec": _round(pass_interval_sec, 4),
            "sample_max_frames": int(pass_max_frames),
            "required_frames_for_full_match": int(
                math.ceil(float(vp.duration_sec) / max(1e-6, float(pass_interval_sec))) + 1
            ),
            "analysis_window": {
                **vp.interlude_trim_window,
                "applied_for_pass": bool(pass_window_start_sec is not None and pass_window_duration_sec is not None),
                "pass_start_sec": (
                    _round(float(pass_window_start_sec), 3)
                    if pass_window_start_sec is not None
                    else None
                ),
                "pass_duration_sec": (
                    _round(float(pass_window_duration_sec), 3)
                    if pass_window_duration_sec is not None
                    else None
                ),
                "fallback_to_full_video": bool(interlude_trim_fallback_used),
                "fallback_reason": trim_fallback_reason,
            },
        }
        if tracking_mode in {"yolo_bytetrack", "auto"}:
            try:
                pass_observations, yolo_meta = _detect_yolo_bytetrack_observations(
                    vp.video_source,
                    sample_interval_sec=pass_interval_sec,
                    max_frames=pass_max_frames,
                    fps_hint=vp.video_meta.get("fps"),
                )
                if pass_observations:
                    pass_tracking_backend = "yolo_bytetrack"  # nosec B105
                    pass_tracking_meta.update(yolo_meta)
                    # Run-but-flag-loudly: if tracking had to use the generic
                    # (non-FRC) model, the findings are unreliable. Mark the run
                    # degraded and log an error so it is visible in monitoring
                    # instead of silently producing empty/low findings.
                    if is_generic_fallback_model(str(yolo_meta.get("model_source") or "")):
                        pass_tracking_meta["model_degraded"] = True
                        pass_tracking_meta["model_degraded_reason"] = (
                            "frc_detector_unavailable_used_generic_fallback"
                        )
                        logger.error(
                            "DEGRADED ANALYSIS match=%s run=%s: FRC detector "
                            "unavailable, used generic fallback model=%s. "
                            "Findings will be low/empty — provision "
                            "media/models/frc_robot_detector_v1.pt or set "
                            "VIDEO_TRACKING_YOLO_MODEL_URL.",
                            ctx.match_key,
                            ctx.run.id,
                            yolo_meta.get("model_source"),
                        )
                else:
                    pass_tracking_meta["fallback_reason"] = "yolo_returned_no_observations"
                    pass_observations = _detect_motion_observations(
                        pass_sample_frames,
                        sample_interval_sec=pass_interval_sec,
                    )
            except (RuntimeError, OSError, ValueError) as exc:
                pass_tracking_meta["fallback_reason"] = str(exc)
                pass_observations = _detect_motion_observations(
                    pass_sample_frames,
                    sample_interval_sec=pass_interval_sec,
                )
        else:
            pass_observations = _detect_motion_observations(
                pass_sample_frames,
                sample_interval_sec=pass_interval_sec,
            )

        pass_observations = _annotate_observations_with_field(
            pass_observations,
            ctx.calibration.homography,
            field_min_x=min_cal_x,
            field_min_y=min_cal_y,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        pass_observations, field_bounds_meta = _prune_observations_to_field_bounds(
            pass_observations,
            field_length_m=config_length,
            field_width_m=config_width,
            enabled=bool(settings.video_tracking_field_bounds_prune_enabled),
            margin_m=float(settings.video_tracking_field_bounds_prune_margin_m or 0.8),
            drop_unprojectable=bool(settings.video_tracking_field_bounds_drop_unprojectable),
        )
        pass_tracking_meta["field_bounds_prune"] = field_bounds_meta
        pass_observations = _compute_field_speeds(pass_observations)
        pass_tracks = _build_track_summaries(pass_observations)
        return pass_sample_frames, pass_observations, pass_tracks, pass_tracking_backend, pass_tracking_meta

    sample_frames, observations, track_summaries, tracking_backend, tracking_meta = _run_tracking_pass(
        pass_interval_sec=sample_interval_sec,
        pass_max_frames=sample_max_frames,
        pass_name="primary",  # nosec B106
        pass_reason=str(vp.sampling_plan.get("reason") or "fixed"),
    )
    if interlude_trim_active:
        should_fallback_full_stream, weak_signal_meta = _trimmed_window_weak_tracking_signal(
            observations_count=len(observations),
            tracks_count=len(track_summaries),
            team_count=len(ctx.match_teams),
        )
        interlude_trim_weak_signal_fallback_meta = dict(weak_signal_meta)
        if should_fallback_full_stream:
            trimmed_score = float(len(observations)) + (12.0 * float(len(track_summaries)))
            interlude_trim_active = False
            interlude_trim_fallback_used = True
            (
                sample_frames,
                observations,
                track_summaries,
                tracking_backend,
                tracking_meta,
            ) = _run_tracking_pass(
                pass_interval_sec=sample_interval_sec,
                pass_max_frames=sample_max_frames,
                pass_name="trim_fullstream_fallback",  # nosec B106
                pass_reason="interlude_trim_weak_signal",
            )
            fallback_score = float(len(observations)) + (12.0 * float(len(track_summaries)))
            interlude_trim_weak_signal_fallback_meta.update(
                {
                    "applied": True,
                    "trimmed_score": _round(trimmed_score, 3),
                    "fallback_score": _round(fallback_score, 3),
                    "score_delta": _round(fallback_score - trimmed_score, 3),
                }
            )
            analysis_window_meta = (
                tracking_meta.get("analysis_window")
                if isinstance(tracking_meta.get("analysis_window"), dict)
                else None
            )
            if analysis_window_meta is not None:
                analysis_window_meta["fallback_reason"] = "trim_window_weak_tracking_signal"
                analysis_window_meta["fallback_to_full_video"] = True

    dense_retry_used = False
    dense_retry_signal: dict[str, Any] = {}
    retry_needed, retry_signal = _should_dense_retry(
        observations=observations,
        track_summaries=track_summaries,
        team_count=len(ctx.match_teams),
        sample_interval_sec=sample_interval_sec,
    )
    dense_retry_signal = retry_signal
    if retry_needed:
        retry_interval_sec, retry_max_frames = _dense_retry_plan(
            sample_interval_sec=sample_interval_sec,
            sample_max_frames=sample_max_frames,
        )
        retry_frames, retry_observations, retry_tracks, retry_backend, retry_meta = _run_tracking_pass(
            pass_interval_sec=retry_interval_sec,
            pass_max_frames=retry_max_frames,
            pass_name="dense_retry",  # nosec B106
            pass_reason="dense_retry_low_coverage",
        )
        primary_score = float(len(observations)) + (12.0 * float(len(track_summaries)))
        retry_score = float(len(retry_observations)) + (12.0 * float(len(retry_tracks)))
        dense_retry_improved = retry_score > (primary_score * 1.18)
        dense_retry_used = dense_retry_improved
        dense_retry_signal["retry_interval_sec"] = retry_interval_sec
        dense_retry_signal["retry_max_frames"] = int(retry_max_frames)
        dense_retry_signal["primary_score"] = _round(primary_score, 3)
        dense_retry_signal["retry_score"] = _round(retry_score, 3)
        dense_retry_signal["selected_retry_pass"] = bool(dense_retry_improved)
        if dense_retry_improved:
            sample_frames = retry_frames
            observations = retry_observations
            track_summaries = retry_tracks
            tracking_backend = retry_backend
            tracking_meta = retry_meta
            sample_interval_sec = float(retry_interval_sec)
            sample_max_frames = int(retry_max_frames)

    tracking_meta["dense_retry"] = {
        **dense_retry_signal,
        "used": bool(dense_retry_used),
    }
    tracking_meta["interlude_trim_weak_signal_fallback"] = interlude_trim_weak_signal_fallback_meta

    # Update the interlude_trim_window dict in-place so downstream stages see it.
    interlude_trim_window = dict(vp.interlude_trim_window)
    interlude_trim_window["runtime_fallback_to_full_video"] = bool(interlude_trim_fallback_used)
    interlude_trim_window["runtime_fallback_reason"] = (
        "trim_window_weak_tracking_signal"
        if bool(interlude_trim_weak_signal_fallback_meta.get("applied"))
        else ("sample_extraction_error" if interlude_trim_fallback_used else None)
    )
    interlude_trim_window["weak_signal_fallback"] = interlude_trim_weak_signal_fallback_meta

    ocr_track_hints, ocr_meta = _infer_track_bumper_numbers(
        sample_frames=sample_frames,
        observations=observations,
        match_teams=ctx.match_teams,
    )

    field_mid_x = config_length / 2.0
    track_team_map, assignment_meta = _assign_tracks_to_teams(
        track_summaries,
        ctx.match_teams,
        field_mid_x,
        ocr_track_hints=ocr_track_hints,
    )
    ctx.mark_stage("tracking_and_assignment")

    return _TrackingResult(
        sample_frames=sample_frames,
        observations=observations,
        track_summaries=track_summaries,
        tracking_backend=tracking_backend,
        tracking_meta=tracking_meta,
        track_team_map=track_team_map,
        assignment_meta=assignment_meta,
        ocr_meta=ocr_meta,
        sample_interval_sec=sample_interval_sec,
        sample_max_frames=sample_max_frames,
        artifact_max_frames=artifact_max_frames,
        interlude_trim_fallback_used=interlude_trim_fallback_used,
        interlude_trim_window=interlude_trim_window,
        min_cal_x=min_cal_x,
        max_cal_x=max_cal_x,
        min_cal_y=min_cal_y,
        max_cal_y=max_cal_y,
        config_length=config_length,
        config_width=config_width,
    )

def _stage_persist_tracks(
    ctx: _StageContext,
    vp: _VideoAndPhasesResult,
    tr: _TrackingResult,
) -> _PersistTracksResult:
    # Delete stale rows and write RobotTrack observations, video and frame artifacts.
    #
    # Clears previous analysis artifacts for this match/run, persists each
    # tracking observation as a RobotTrack row, and stores source-video and
    # sample-frame artifact records.
    ctx.db.query(models.TeamMatchThroughput).filter(
        models.TeamMatchThroughput.match_key == ctx.match_key
    ).delete(synchronize_session=False)
    ctx.db.query(models.TeamMatchFinding).filter(
        models.TeamMatchFinding.match_key == ctx.match_key
    ).delete(synchronize_session=False)
    ctx.db.query(models.RobotTrack).filter(
        models.RobotTrack.match_key == ctx.match_key
    ).delete(synchronize_session=False)
    ctx.db.query(models.MatchEvent).filter(
        models.MatchEvent.match_key == ctx.match_key
    ).delete(synchronize_session=False)
    ctx.db.query(models.AnalysisQuality).filter(
        models.AnalysisQuality.match_key == ctx.match_key
    ).delete(synchronize_session=False)
    ctx.db.query(models.Artifact).filter(
        models.Artifact.analysis_run_id == ctx.run.id
    ).delete(synchronize_session=False)

    for observation in tr.observations:
        track_id = int(observation["track_id"])
        team_key = tr.track_team_map.get(track_id)
        ctx.db.add(
            models.RobotTrack(
                analysis_run_id=ctx.run.id,
                match_key=ctx.match_key,
                event_key=ctx.match.event_key,
                team_key=team_key,
                track_id=track_id,
                frame_index=int(observation["frame_index"]),
                time_sec=float(observation["time_sec"]),
                bbox_x1=float(observation["bbox_x1"]),
                bbox_y1=float(observation["bbox_y1"]),
                bbox_x2=float(observation["bbox_x2"]),
                bbox_y2=float(observation["bbox_y2"]),
                centroid_x=float(observation["centroid_x"]),
                centroid_y=float(observation["centroid_y"]),
                field_x=float(observation["field_x"]) if observation["field_x"] is not None else None,
                field_y=float(observation["field_y"]) if observation["field_y"] is not None else None,
                zone_key=observation.get("zone_key"),
                speed_mps=(
                    float(observation["speed_mps"])
                    if observation.get("speed_mps") is not None
                    else (
                        float(observation["speed_px"]) if observation.get("speed_px") is not None else None
                    )
                ),
                confidence=float(observation["confidence"]) if observation.get("confidence") is not None else None,
                source=tr.tracking_backend,
            )
        )
    ctx.mark_stage("persist_tracks")

    video_source_label = ctx.youtube_video.url
    source_video_meta: dict[str, Any] = {
        "youtube_url": ctx.youtube_video.url,
        "source_mode": vp.video_source_mode,
        "streamed": vp.streamed_video,
        "resolved_source": vp.resolved_video_source,
        "stream_fallback_reason": vp.stream_fallback_reason,
        "interlude_trim_window": tr.interlude_trim_window,
        "analysis_duration_sec": _round(vp.duration_sec, 3),
        "raw_video_duration_sec": _round(vp.raw_video_duration_sec, 3),
        **vp.video_meta,
    }
    if vp.prepared_video_source.local_video_path is not None:
        source_video_meta["fallback_download_path"] = str(
            vp.prepared_video_source.local_video_path
        )
    ctx.db.add(
        models.Artifact(
            analysis_run_id=ctx.run.id,
            kind="source_video",
            path=video_source_label,
            meta=source_video_meta,
        )
    )
    artifact_frames = tr.sample_frames[:tr.artifact_max_frames]
    for index, frame_path in enumerate(artifact_frames, start=1):
        ctx.db.add(
            models.Artifact(
                analysis_run_id=ctx.run.id,
                kind="sample_frame",
                path=frame_path.relative_to(MEDIA_ROOT).as_posix(),
                meta={"index": index, "time_sec": _round((index - 1) * tr.sample_interval_sec, 2)},
            )
        )
    ctx.db.add(
        models.Artifact(
            analysis_run_id=ctx.run.id,
            kind="sampling_manifest",
            path=video_source_label,
            meta={
                "sample_every_sec": tr.sample_interval_sec,
                "frames_extracted": len(tr.sample_frames),
                "frames_saved_as_artifacts": len(artifact_frames),
                "detections": len(tr.observations),
                "tracks": len(tr.track_summaries),
                "run_id": ctx.run.id,
                "tracking_backend": tr.tracking_backend,
                "tracking_backend_meta": tr.tracking_meta,
                "bumper_ocr_meta": tr.ocr_meta,
                "assignment_meta": tr.assignment_meta,
                "perimeter_type": ctx.perimeter_type,
                "perimeter_source": ctx.perimeter_source,
                "interlude_trim_window": tr.interlude_trim_window,
            },
        )
    )

    return _PersistTracksResult(
        artifact_frames=artifact_frames,
        source_video_meta=source_video_meta,
    )

def _stage_compute_team_metrics(
    ctx: _StageContext,
    vp: _VideoAndPhasesResult,
    tr: _TrackingResult,
    pt: _PersistTracksResult,
) -> _TeamMetricsResult:
    # Classify events, detect defense, compute and persist per-team metrics.
    #
    # Loads the transition-event model, groups observations by team, detects
    # defensive interactions, then iterates each team to compute metrics,
    # apply cycle fallbacks, persist MatchEvent / TeamMatchFinding /
    # TeamMatchThroughput rows, and collect quality signals.
    event_model: TransitionEventModel | None = None
    event_model_meta: dict[str, Any] = {
        "enabled": bool(settings.video_event_classifier_enabled),
        "loaded": False,
        "model_path": str(settings.video_event_classifier_model_path or ""),
        "model_version": None,
        "threshold": _round(float(settings.video_event_classifier_conf_threshold), 4),
        "margin_threshold": _round(float(settings.video_event_classifier_margin_threshold), 4),
        "prefer_model": bool(settings.video_event_classifier_prefer_model),
    }
    if bool(settings.video_event_classifier_enabled):
        try:
            event_model = load_transition_event_model(settings.video_event_classifier_model_path)
            if event_model is not None:
                event_model_meta["loaded"] = True
                event_model_meta["model_version"] = event_model.version
                if isinstance(event_model.class_thresholds, dict) and event_model.class_thresholds:
                    event_model_meta["class_thresholds"] = {
                        key: _round(float(value), 4)
                        for key, value in event_model.class_thresholds.items()
                    }
                if float(event_model.min_top2_margin or 0.0) > 0.0:
                    event_model_meta["model_margin_threshold"] = _round(
                        float(event_model.min_top2_margin),
                        4,
                    )
            else:
                event_model_meta["reason"] = "model_not_found_or_invalid"
        except (RuntimeError, OSError, ValueError) as exc:
            event_model_meta["reason"] = str(exc)

    observations_by_team: dict[str, list[dict]] = defaultdict(list)
    for observation in tr.observations:
        team_key = tr.track_team_map.get(int(observation["track_id"]))
        if team_key:
            observations_by_team[team_key].append(observation)

    # Analyze defensive interactions from all robot position data
    defensive_interactions_by_team = _detect_defensive_interactions(
        observations_by_team,
        ctx.match_teams,
        duration_sec=vp.duration_sec,
        sample_interval_sec=tr.sample_interval_sec,
    )
    ctx.mark_stage("event_model_and_defense_signals")

    auto_window_sec = float(vp.phase_windows["auto_end_sec"])
    endgame_window_sec = float(vp.phase_windows["endgame_window_sec"])
    teleop_scoring_start_sec = float(vp.phase_windows["teleop_scoring_start_sec"])
    teleop_scoring_end_sec = float(vp.phase_windows["teleop_scoring_end_sec"])
    teleop_scoring_duration_sec = float(vp.phase_windows["teleop_scoring_duration_sec"])
    phase_confidence = float(vp.phase_windows["confidence_0_1"])
    phase_source = str(vp.phase_windows["source"])
    team_coverage_scores: list[float] = []
    team_detection_counts: list[int] = []
    team_missing_reason_counts: Counter[str] = Counter()

    artifact_frames = pt.artifact_frames

    for match_team in ctx.match_teams:
        team_key = match_team.team_key
        team_observations = observations_by_team.get(team_key, [])
        team_alliance = str(match_team.alliance or "").strip().lower()
        team_active_windows: list[tuple[float, float]] | None = None
        team_active_window_duration_sec = (
            max(1e-6, float(vp.cycle_fallback_duration_sec))
            if isinstance(vp.season_year, int) and vp.season_year >= 2026
            else max(1e-6, float(teleop_scoring_duration_sec))
        )
        team_hub_activity_source = "phase_window_fallback"
        team_hub_shift1_active_alliance: str | None = None
        if isinstance(vp.hub_activity_payload, dict):
            hub_shift1_active = str(vp.hub_activity_payload.get("shift1_active_alliance") or "").strip().lower()
            windows_by_alliance = (
                vp.hub_activity_payload.get("windows_attributed_by_alliance")
                if isinstance(vp.hub_activity_payload.get("windows_attributed_by_alliance"), dict)
                else {}
            )
            duration_by_alliance = (
                vp.hub_activity_payload.get("attributed_duration_by_alliance")
                if isinstance(vp.hub_activity_payload.get("attributed_duration_by_alliance"), dict)
                else {}
            )
            if hub_shift1_active in {"red", "blue"}:
                raw_windows = windows_by_alliance.get(team_alliance)
                if isinstance(raw_windows, list):
                    parsed_windows: list[tuple[float, float]] = []
                    for item in raw_windows:
                        if (
                            isinstance(item, (tuple, list))
                            and len(item) == 2
                            and isinstance(item[0], (int, float))
                            and isinstance(item[1], (int, float))
                        ):
                            parsed_windows.append((float(item[0]), float(item[1])))
                    team_active_windows = _merge_time_windows(
                        parsed_windows,
                        floor_sec=max(0.0, auto_window_sec),
                        ceil_sec=max(0.0, vp.duration_sec) + REBUILT_HUB_SCORE_GRACE_SEC,
                    )
                duration_candidate = _as_float(duration_by_alliance.get(team_alliance))
                if duration_candidate is not None and duration_candidate > 0.0:
                    team_active_window_duration_sec = max(1e-6, float(duration_candidate))
            team_hub_activity_source = str(vp.hub_activity_payload.get("source") or "manual_schedule_default")
            team_hub_shift1_active_alliance = hub_shift1_active if hub_shift1_active in {"red", "blue"} else None

        metrics, events, event_inference = _compute_team_metrics_and_events(
            team_key=team_key,
            observations=team_observations,
            duration_sec=vp.duration_sec,
            auto_window_sec=auto_window_sec,
            endgame_window_sec=endgame_window_sec,
            sample_interval_sec=tr.sample_interval_sec,
            event_model=event_model,
            event_model_threshold=float(settings.video_event_classifier_conf_threshold),
            event_model_margin_threshold=float(settings.video_event_classifier_margin_threshold),
            event_model_prefer_model=bool(settings.video_event_classifier_prefer_model),
            active_scoring_windows_sec=team_active_windows,
            active_scoring_duration_sec=team_active_window_duration_sec,
        )
        cycle_fallback = (
            vp.sparse_cycle_fallbacks_by_team.get(team_key)
            if isinstance(vp.sparse_cycle_fallbacks_by_team, dict)
            else None
        )
        cycle_fallback_applied = False
        fallback_score_events_estimate = 0.0
        if isinstance(cycle_fallback, dict):
            fallback_cycle = _as_float(cycle_fallback.get("cycle_time_sec"))
            fallback_fuel_rate = _as_float(cycle_fallback.get("fuel_scoring_rate"))
            fallback_score_events_raw = _as_float(cycle_fallback.get("score_count_estimate"))
            fallback_score_events_estimate = (
                float(fallback_score_events_raw)
                if fallback_score_events_raw is not None and fallback_score_events_raw > 0.0
                else 0.0
            )
            if (metrics.get("cycle_time_sec") is None) and fallback_cycle is not None and fallback_cycle > 0.0:
                metrics["cycle_time_sec"] = _round(float(fallback_cycle), 3)
                cycle_fallback_applied = True
            if (
                float(metrics.get("fuel_scoring_rate") or 0.0) <= 0.0
                and fallback_fuel_rate is not None
                and fallback_fuel_rate > 0.0
            ):
                metrics["fuel_scoring_rate"] = _round(float(fallback_fuel_rate), 3)
                cycle_fallback_applied = True

        for event in events:
            ctx.db.add(
                models.MatchEvent(
                    analysis_run_id=ctx.run.id,
                    match_key=ctx.match_key,
                    event_key=ctx.match.event_key,
                    team_key=team_key,
                    track_id=int(event["track_id"]) if event.get("track_id") is not None else None,
                    frame_index=int(event["frame_index"]) if event.get("frame_index") is not None else None,
                    time_sec=float(event["time_sec"]),
                    event_type=str(event["event_type"]),
                    confidence=float(event["confidence"]) if event.get("confidence") is not None else None,
                    field_x=float(event["field_x"]) if event.get("field_x") is not None else None,
                    field_y=float(event["field_y"]) if event.get("field_y") is not None else None,
                    meta=event.get("meta") or {},
                )
            )

        event_counter = Counter([str(event["event_type"]) for event in events])
        scoring_event_times = [
            float(event["time_sec"])
            for event in events
            if str(event.get("event_type")) == "teleop_fuel_score_attempt"
        ]
        score_events = len(scoring_event_times)
        if score_events <= 0 and fallback_score_events_estimate > 0.0:
            score_events = max(1, int(round(float(fallback_score_events_estimate))))
        active_scoring_event_times = [
            time_sec
            for time_sec in scoring_event_times
            if _time_in_windows(time_sec, team_active_windows)
        ]
        active_window_start_sec = (
            float(team_active_windows[0][0])
            if team_active_windows
            else (
                float(auto_window_sec)
                if isinstance(vp.season_year, int) and vp.season_year >= 2026
                else float(teleop_scoring_start_sec)
            )
        )
        active_window_end_sec = (
            float(team_active_windows[-1][1])
            if team_active_windows
            else (
                float(vp.duration_sec)
                if isinstance(vp.season_year, int) and vp.season_year >= 2026
                else float(teleop_scoring_end_sec)
            )
        )
        active_window_duration_sec = (
            max(1e-6, float(team_active_window_duration_sec))
            if team_active_window_duration_sec > 0.0
            else max(1e-6, float(teleop_scoring_duration_sec))
        )
        balls_shot_active = len(active_scoring_event_times)
        if balls_shot_active <= 0 and score_events > 0 and cycle_fallback_applied:
            balls_shot_active = score_events
        shooting_time_total_seconds = (
            float(metrics["cycle_time_sec"]) * float(score_events)
            if metrics["cycle_time_sec"] is not None and score_events > 0
            else (
                max(1e-6, float(active_window_duration_sec)) * 0.35
                if score_events > 0 and active_window_duration_sec > 0
                else (float(vp.duration_sec - auto_window_sec) * 0.2 if score_events > 0 else None)
            )
        )
        bps_total = (
            (float(score_events) / max(1e-6, shooting_time_total_seconds))
            if shooting_time_total_seconds is not None and shooting_time_total_seconds > 0
            else (
                (float(metrics["fuel_scoring_rate"]) / 60.0)
                if metrics["fuel_scoring_rate"] is not None
                else None
            )
        )
        shooting_time_active_seconds = (
            (
                shooting_time_total_seconds
                * (_clamp(float(balls_shot_active) / max(1.0, float(score_events)), 0.0, 1.0))
            )
            if shooting_time_total_seconds is not None and score_events > 0
            else (
                (
                    max(1e-6, float(active_window_duration_sec))
                    * _clamp(float(balls_shot_active) / max(1.0, float(score_events)), 0.0, 1.0)
                )
                if score_events > 0 and active_window_duration_sec > 0
                else None
            )
        )
        active_bps = (
            (float(balls_shot_active) / max(1e-6, shooting_time_active_seconds))
            if shooting_time_active_seconds is not None and shooting_time_active_seconds > 0
            else bps_total
        )
        coverage_score = _clamp(
            (
                0.35 if score_events > 0 else 0.0
            )
            + (0.25 if metrics["cycle_time_sec"] is not None else 0.0)
            + (0.2 if len(team_observations) >= 8 else 0.0)
            + (0.1 * phase_confidence)
            + (0.1 * float(metrics["reliability_score"] or 0.0)),
            0.0,
            1.0,
        )
        missing_reasons: list[str] = []
        if score_events == 0:
            missing_reasons.append("no_scoring_events_detected")
        if balls_shot_active == 0 and score_events > 0:
            missing_reasons.append("no_scoring_events_in_active_window")
        if metrics["cycle_time_sec"] is None:
            missing_reasons.append("cycle_time_unavailable")
        if len(team_observations) < 6:
            missing_reasons.append("insufficient_tracking_observations")
        team_coverage_scores.append(float(coverage_score))
        team_detection_counts.append(int(len(team_observations)))
        for reason in missing_reasons:
            team_missing_reason_counts[str(reason)] += 1
        tracking_note = (
            "Full-match video sampled and tracked with YOLO + ByteTrack."
            if tr.tracking_backend == "yolo_bytetrack"
            else "Full-match video sampled and tracked with motion-based CV baseline."
        )
        ocr_note = (
            "Track identity used bumper-number OCR hints when confidence passed threshold."
            if tr.ocr_meta.get("enabled")
            else "Bumper-number OCR disabled; identity used spatial assignment only."
        )
        event_model_note = (
            "Event transitions are inferred by a learned classifier with heuristic fallback."
            if bool(event_model_meta.get("loaded"))
            else "Event transitions currently use zone/timing heuristics (learned classifier unavailable)."
        )
        cycle_fallback_note = (
            (
                "Cycle-time fallback applied from official score breakdown "
                "(game-piece scoring only; excludes climb/penalties)."
            )
            if cycle_fallback_applied
            else None
        )
        active_windows_payload = (
            [
                {
                    "start_sec": _round(float(start_sec), 3),
                    "end_sec": _round(float(end_sec), 3),
                }
                for start_sec, end_sec in (team_active_windows or [])
            ]
            if team_active_windows
            else [
                {
                    "start_sec": _round(
                        float(auto_window_sec)
                        if isinstance(vp.season_year, int) and vp.season_year >= 2026
                        else float(teleop_scoring_start_sec),
                        3,
                    ),
                    "end_sec": _round(
                        float(vp.duration_sec)
                        if isinstance(vp.season_year, int) and vp.season_year >= 2026
                        else float(teleop_scoring_end_sec),
                        3,
                    ),
                }
            ]
        )
        summary = {
            "source": ctx.analysis_version,
            "calibration_frame_time_sec": ctx.calibration.frame_time_sec,
            "analysis_context": {
                "analysis_version": ctx.analysis_version,
                "params_hash": ctx.params_hash or "",
                "calibration_id": ctx.calibration_id,
                "perimeter_type": ctx.perimeter_type,
                "perimeter_source": ctx.perimeter_source,
                "tracking_backend": tr.tracking_backend,
                "assignment_meta": tr.assignment_meta,
                "event_classifier_meta": event_model_meta,
            },
            "video": {
                "youtube_url": ctx.youtube_video.url,
                "streamed": vp.streamed_video,
                "source_mode": vp.video_source_mode,
                "resolved_source": vp.resolved_video_source,
                "stream_fallback_reason": vp.stream_fallback_reason,
                "interlude_trim_window": tr.interlude_trim_window,
                "analysis_duration_sec": _round(vp.duration_sec, 3),
                "raw_video_duration_sec": _round(vp.raw_video_duration_sec, 3),
                **vp.video_meta,
            },
            "sampling": {
                "sample_every_sec": tr.sample_interval_sec,
                "frames_extracted": len(tr.sample_frames),
                "detections": len(team_observations),
                "sample_preview_urls": [media_url_for_path(path) for path in artifact_frames[:8]],
                "tracking_backend": tr.tracking_backend,
                "tracking_backend_meta": tr.tracking_meta,
                "bumper_ocr_meta": tr.ocr_meta,
            },
            "event_counts": dict(event_counter),
            "phase_windows": {
                "source": phase_source,
                "confidence_0_1": _round(phase_confidence, 4),
                "auto_end_sec": _round(auto_window_sec, 3),
                "teleop_scoring_start_sec": _round(teleop_scoring_start_sec, 3),
                "teleop_scoring_end_sec": _round(teleop_scoring_end_sec, 3),
                "endgame_start_sec": _round(float(vp.phase_windows["endgame_start_sec"]), 3),
                "hub_activity_source": team_hub_activity_source,
                "hub_shift1_active_alliance": team_hub_shift1_active_alliance,
                "hub_active_windows": active_windows_payload,
            },
            "track_summary": {
                "team_tracks": len(
                    {
                        row["track_id"]
                        for row in team_observations
                    }
                ),
                "average_track_speed_mps": _round(
                    sum(
                        row["speed_mps"]
                        for row in team_observations
                        if row.get("speed_mps") is not None
                    )
                    / max(
                        1,
                        len(
                            [row for row in team_observations if row.get("speed_mps") is not None]
                        ),
                    ),
                    4,
                )
                if any(row.get("speed_mps") is not None for row in team_observations)
                else None,
            },
            "throughput_metrics": {
                "balls_shot_total": int(score_events),
                "shooting_time_total_seconds": _round(shooting_time_total_seconds, 3) if shooting_time_total_seconds else None,
                "bps_total": _round(float(bps_total), 4) if bps_total is not None else None,
                "balls_shot_active": int(balls_shot_active),
                "shooting_time_active_seconds": _round(shooting_time_active_seconds, 3) if shooting_time_active_seconds else None,
                "active_bps": _round(float(active_bps), 4) if active_bps is not None else None,
                "active_window_start_sec": _round(active_window_start_sec, 3),
                "active_window_end_sec": _round(active_window_end_sec, 3),
                "active_window_duration_sec": _round(active_window_duration_sec, 3),
                "phase_window_source": phase_source,
                "phase_window_confidence_0_1": _round(phase_confidence, 4),
                "hub_activity_source": team_hub_activity_source,
                "hub_shift1_active_alliance": team_hub_shift1_active_alliance,
                "hub_active_windows": active_windows_payload,
                "coverage_score": _round(coverage_score, 4),
                "missing_reasons": missing_reasons,
                "cycle_time_source": (
                    str(cycle_fallback.get("source") or "cv_load_to_score")
                    if cycle_fallback_applied and isinstance(cycle_fallback, dict)
                    else "cv_load_to_score"
                ),
                "score_events_source": (
                    str(cycle_fallback.get("source") or "cv_events")
                    if cycle_fallback_applied and isinstance(cycle_fallback, dict) and fallback_score_events_estimate > 0.0
                    else "cv_events"
                ),
                "fallback_score_count_estimate": (
                    _round(float(fallback_score_events_estimate), 3)
                    if cycle_fallback_applied and fallback_score_events_estimate > 0.0
                    else None
                ),
            },
            "event_inference": event_inference,
            "timeline": [
                {
                    "time_sec": event["time_sec"],
                    "label": event["event_type"],
                    "confidence": event.get("confidence"),
                }
                for event in events[:40]
            ],
            "defensive_interactions": defensive_interactions_by_team.get(team_key, {
                "proximity_engagement_sec": 0.0,
                "velocity_interrupt_events": 0,
                "blocking_potential_score": 0.0,
            }),
            "notes": [
                tracking_note,
                event_model_note,
                ocr_note,
                cycle_fallback_note,
            ]
            if cycle_fallback_note is not None
            else [
                tracking_note,
                event_model_note,
                ocr_note,
            ],
        }

        finding = models.TeamMatchFinding(
            analysis_run_id=ctx.run.id,
            match_key=ctx.match_key,
            event_key=ctx.match.event_key,
            team_key=team_key,
            alliance=match_team.alliance,
            station=match_team.station,
            source=ctx.analysis_version,
            fuel_scoring_rate=float(metrics["fuel_scoring_rate"]),
            cycle_time_sec=float(metrics["cycle_time_sec"]) if metrics["cycle_time_sec"] is not None else None,
            auto_contribution=float(metrics["auto_contribution"]),
            climb_success_prob=float(metrics["climb_success_prob"]),
            defensive_engagement_sec=float(metrics["defensive_engagement_sec"]),
            reliability_score=float(metrics["reliability_score"]),
            summary=summary,
        )
        ctx.db.add(finding)
        ctx.db.flush()

        ctx.db.merge(
            models.TeamMatchThroughput(
                finding_id=finding.id,
                analysis_run_id=ctx.run.id,
                match_key=ctx.match_key,
                event_key=ctx.match.event_key,
                team_key=team_key,
                balls_shot_total=int(score_events),
                shooting_time_total_seconds=(
                    float(shooting_time_total_seconds)
                    if shooting_time_total_seconds is not None
                    else None
                ),
                bps_total=float(bps_total) if bps_total is not None else None,
                balls_shot_active=int(balls_shot_active),
                shooting_time_active_seconds=(
                    float(shooting_time_active_seconds)
                    if shooting_time_active_seconds is not None
                    else None
                ),
                active_bps=float(active_bps) if active_bps is not None else None,
                metric_coverage={
                    "coverage_score": _round(coverage_score, 4),
                    "missing_reasons": missing_reasons,
                    "has_scoring_events": score_events > 0,
                    "has_active_window_scores": balls_shot_active > 0,
                    "has_cycle_time": metrics["cycle_time_sec"] is not None,
                    "active_window_start_sec": _round(active_window_start_sec, 3),
                    "active_window_end_sec": _round(active_window_end_sec, 3),
                    "active_window_duration_sec": _round(active_window_duration_sec, 3),
                    "phase_window_source": phase_source,
                    "phase_window_confidence_0_1": _round(phase_confidence, 4),
                    "hub_activity_source": team_hub_activity_source,
                    "hub_shift1_active_alliance": team_hub_shift1_active_alliance,
                    "hub_active_windows": active_windows_payload,
                    "tracking_observations": len(team_observations),
                    "cycle_time_source": (
                        str(cycle_fallback.get("source") or "cv_load_to_score")
                        if cycle_fallback_applied and isinstance(cycle_fallback, dict)
                        else "cv_load_to_score"
                    ),
                    "score_events_source": (
                        str(cycle_fallback.get("source") or "cv_events")
                        if cycle_fallback_applied and isinstance(cycle_fallback, dict) and fallback_score_events_estimate > 0.0
                        else "cv_events"
                    ),
                },
                source=ctx.analysis_version,
            )
        )

        if balls_shot_active > 0:
            ctx.db.add(
                models.MatchEvent(
                    analysis_run_id=ctx.run.id,
                    match_key=ctx.match_key,
                    event_key=ctx.match.event_key,
                    team_key=team_key,
                    track_id=None,
                    frame_index=None,
                    time_sec=vp.duration_sec,
                    event_type="teleop_fuel_score_success",
                    confidence=0.65,
                    field_x=None,
                    field_y=None,
                    meta={
                        "count_estimate": balls_shot_active,
                        "count_source": "active_window_only",
                    },
                )
            )
    ctx.mark_stage("persist_team_findings_and_events")

    return _TeamMetricsResult(
        team_coverage_scores=team_coverage_scores,
        team_detection_counts=team_detection_counts,
        team_missing_reason_counts=team_missing_reason_counts,
    )

def analyze_match(
    match_key: str,
    analysis_version: str = DEFAULT_ANALYSIS_VERSION,
    params_hash: str | None = None,
    calibration_id: int | None = None,
    sampling_override: dict[str, Any] | None = None,
):
    db = SessionLocal()
    run: models.AnalysisRun | None = None
    prepared_video_source = None
    stage_started_at = time.perf_counter()
    stage_last_mark = stage_started_at
    pipeline_stage_ms: dict[str, float] = {}

    def _mark_stage(stage_name: str) -> None:
        nonlocal stage_last_mark
        now = time.perf_counter()
        pipeline_stage_ms[stage_name] = _round((now - stage_last_mark) * 1000.0, 3)
        stage_last_mark = now

    try:
        # -- Setup / validation (inline – manages DB run lifecycle) ----------
        match = db.get(models.Match, match_key)
        if match is None:
            raise RuntimeError(f"Match {match_key} not found")

        calibration_query = db.query(models.FieldCalibration).filter(
            models.FieldCalibration.match_key == match_key
        )
        if calibration_id is not None:
            calibration_query = calibration_query.filter(models.FieldCalibration.id == calibration_id)
        calibration = calibration_query.one_or_none()
        if calibration is None:
            raise RuntimeError(f"Calibration missing for {match_key}")
        calibration_id = calibration.id
        event_profile = db.get(models.EventProfile, match.event_key)
        perimeter_type, perimeter_source = resolve_perimeter_type_for_event_profile(event_profile)
        normalized_sampling_override = _normalize_sampling_override(sampling_override)
        if params_hash is None:
            params_hash = build_analysis_params_hash(
                analysis_version=analysis_version,
                calibration_id=calibration_id,
                perimeter_type=perimeter_type,
                sampling_overrides=normalized_sampling_override,
            )

        existing = _latest_matching_run(
            db,
            match_key=match_key,
            analysis_version=analysis_version,
            params_hash=params_hash,
            calibration_id=calibration_id,
        )
        if existing is not None:
            existing_run, _ = existing
            return {
                "ok": True,
                "status": "skipped",
                "reason": "already_analyzed_or_in_progress",
                "match_key": match_key,
                "analysis_version": analysis_version,
                "params_hash": params_hash,
                "calibration_id": calibration_id,
                "perimeter_type": perimeter_type,
                "perimeter_source": perimeter_source,
                "existing_run_id": existing_run.id,
                "existing_status": existing_run.status,
            }

        youtube_video = (
            db.query(models.MatchVideo)
            .filter(models.MatchVideo.match_key == match_key, models.MatchVideo.video_type == "youtube")
            .order_by(models.MatchVideo.id.asc())
            .first()
        )
        if youtube_video is None or not youtube_video.url:
            raise RuntimeError(f"No YouTube video found for {match_key}")

        match_teams = (
            db.query(models.MatchTeam)
            .filter(models.MatchTeam.match_key == match_key)
            .all()
        )
        if not match_teams:
            raise RuntimeError(f"No team links found for {match_key}. Re-run event ingestion.")

        run = models.AnalysisRun(
            match_key=match_key,
            version=analysis_version,
            status="running",
            created_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        db.merge(
                models.AnalysisRunContext(
                    run_id=run.id,
                    match_key=match_key,
                    event_key=match.event_key,
                    analysis_version=analysis_version,
                params_hash=params_hash or "",
                calibration_id=calibration_id,
            )
        )
        db.commit()

        # -- Build shared context ------------------------------------------
        ctx = _StageContext(
            match_key=match_key,
            match=match,
            run=run,
            db=db,
            calibration=calibration,
            calibration_id=calibration_id,
            match_teams=match_teams,
            youtube_video=youtube_video,
            event_profile=event_profile,
            analysis_version=analysis_version,
            params_hash=params_hash,
            perimeter_type=perimeter_type,
            perimeter_source=perimeter_source,
            normalized_sampling_override=normalized_sampling_override,
            mark_stage=_mark_stage,
        )

        # -- Stage 1: Resolve video, phases, sampling plan -----------------
        vp = _stage_resolve_video_and_phases(ctx)
        prepared_video_source = vp.prepared_video_source

        # -- Stage 2: Detection, tracking, OCR, team assignment ------------
        tr = _stage_run_tracking(ctx, vp)

        # -- Stage 3: Persist observations and artifacts -------------------
        pt = _stage_persist_tracks(ctx, vp, tr)

        # -- Stage 4: Event classification, defense, per-team metrics ------
        tm = _stage_compute_team_metrics(ctx, vp, tr, pt)

        # -- Stage 5: Quality gate evaluation ------------------------------
        qr = _stage_evaluate_quality(ctx, tr, tm, pipeline_stage_ms)

        if qr.reject_low_quality:
            run_id = int(run.id)
            db.rollback()
            try:
                failed_run = db.get(models.AnalysisRun, run_id)
                if failed_run is not None:
                    failed_run.status = "failed"
                    db.add(failed_run)
                    db.commit()
            except SQLAlchemyError:
                db.rollback()
            print(
                (
                    f"Analyzing rejected: {match_key} (run={run_id}, "
                    f"reason={','.join(qr.rejection_reasons)}, overall={_round(qr.overall_quality_score, 4)}, "
                    f"coverage_avg={_round(qr.coverage_avg, 4)})"
                ),
                flush=True,
            )
            _run_media_cleanup_best_effort("analysis_post_run_rejected")
            return {
                "ok": False,
                "status": "rejected_low_quality",
                "match_key": match_key,
                "run_id": run_id,
                "rejection_reasons": qr.rejection_reasons,
                "overall_quality_score": round(qr.overall_quality_score, 4),
                "coverage_avg_0_1": round(qr.coverage_avg, 4),
                "min_detections_per_team": int(qr.detections_min) if qr.detections_min is not None else None,
            }

        run.status = "completed"
        db.commit()
        _mark_stage("commit_completed")

        # Best-effort: auto-generate scouting drafts for all assigned teams once the
        # run is committed. Never let draft generation flip run.status or fail the job.
        auto_scout_drafts_summary: dict[str, Any] | None = None
        if settings.auto_scout_generate_after_analysis_enabled:
            # Local import to avoid a circular dependency (scouting imports from jobs).
            from app.services.auto_scout.scouting import generate_auto_scout_drafts_for_match

            logger.info(
                "auto_scout.post_analysis_started event_key=%s match_key=%s run_id=%s",
                match.event_key,
                match_key,
                run.id,
            )
            try:
                auto_scout_drafts_summary = generate_auto_scout_drafts_for_match(
                    db,
                    event_key=match.event_key,
                    match_key=match_key,
                )
                logger.info(
                    "auto_scout.post_analysis_completed event_key=%s match_key=%s run_id=%s "
                    "team_count=%s created_count=%s ready_count=%s low_confidence_count=%s "
                    "failed_count=%s errors=%s",
                    match.event_key,
                    match_key,
                    run.id,
                    auto_scout_drafts_summary.get("team_count"),
                    auto_scout_drafts_summary.get("created_count"),
                    auto_scout_drafts_summary.get("ready_count"),
                    auto_scout_drafts_summary.get("low_confidence_count"),
                    auto_scout_drafts_summary.get("failed_count"),
                    len(auto_scout_drafts_summary.get("errors") or []),
                )
            except Exception:
                db.rollback()
                logger.exception(
                    "auto_scout.post_analysis_failed event_key=%s match_key=%s run_id=%s",
                    match.event_key,
                    match_key,
                    run.id,
                )
        _mark_stage("auto_scout_drafts_generated")

        if settings.storage_cleanup_post_analysis:
            try:
                cleanup_result = cleanup_match_media(match_key, aggressive=True, require_findings=False)
                if cleanup_result.get("ok"):
                    freed_gb = cleanup_result.get("deleted_gb", 0)
                    print(f"  → Storage cleanup: freed {freed_gb} GB", flush=True)
                else:
                    print(f"  ⚠ Storage cleanup skipped: {cleanup_result.get('reason', 'unknown')}", flush=True)
            except (RuntimeError, OSError, ValueError, SQLAlchemyError) as e:
                print(f"  ⚠ Storage cleanup failed: {e}", flush=True)

        total_pipeline_ms = _round(sum(pipeline_stage_ms.values()), 1)
        logger.info(
            "pipeline.completed match_key=%s run_id=%s "
            "total_ms=%.1f quality=%.4f "
            "calibration_q=%.4f tracking_q=%.4f identity_q=%.4f "
            "coverage_avg=%.4f frames=%d observations=%d tracks=%d "
            "teams_mapped=%d teams_expected=%d "
            "stream_fallback=%s interlude_trim_fallback=%s "
            "rejected=%s rejection_reasons=%s "
            "stages=%s",
            match_key,
            run.id,
            total_pipeline_ms,
            qr.overall_quality_score,
            qr.calibration_quality_score,
            qr.tracking_quality_score,
            qr.identity_quality_score,
            qr.coverage_avg,
            len(tr.sample_frames),
            len(tr.observations),
            len(tr.track_summaries),
            len(set(tr.track_team_map.values())),
            len(ctx.match_teams),
            vp.stream_fallback_reason or "none",
            tr.interlude_trim_fallback_used,
            bool(qr.reject_low_quality),
            ",".join(qr.rejection_reasons) if qr.rejection_reasons else "none",
            "|".join(f"{k}={v:.0f}" for k, v in pipeline_stage_ms.items()),
        )
        _run_media_cleanup_best_effort("analysis_post_run")
        model_degraded = bool(tr.tracking_meta.get("model_degraded"))
        return {
            "ok": True,
            "status": "completed",
            "match_key": match_key,
            "run_id": run.id,
            "quality_score_0_1": round(qr.overall_quality_score, 4),
            "model_degraded": model_degraded,
            "model_degraded_reason": tr.tracking_meta.get("model_degraded_reason"),
            "stage_timing_ms": pipeline_stage_ms,
            "auto_scout_drafts": auto_scout_drafts_summary,
        }

    except Exception as exc:
        logger.error(
            "pipeline.failed match_key=%s run_id=%s error=%s stages_completed=%s",
            match_key,
            run.id if run is not None else None,
            str(exc)[:200],
            "|".join(pipeline_stage_ms.keys()) if pipeline_stage_ms else "none",
        )
        db.rollback()
        if run is not None:
            try:
                run.status = "failed"
                db.add(run)
                db.commit()
            except SQLAlchemyError:
                db.rollback()
        raise
    finally:
        cleanup_prepared_youtube_source(prepared_video_source)
        _run_media_cleanup_best_effort("analysis_finally")
        db.close()
