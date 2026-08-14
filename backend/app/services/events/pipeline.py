# Core event analysis pipeline orchestration.
#
# Extracted from ``app.api.routes_analysis`` to resolve cross-layer dependency
# violations (services → routes).  All pipeline logic that does not depend on
# HTTP request/response semantics lives here.

from __future__ import annotations

import hashlib
import logging
import time
from contextlib import suppress
from typing import Any

import redis
from rq import Queue, Retry
from rq.job import Job
from rq.registry import (
    DeferredJobRegistry,
    ScheduledJobRegistry,
    StartedJobRegistry,
)
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import sanitize_external_error
from app.db import models
from app.services.calibration.auto import AutoCalibrationError, auto_calibrate_from_video
from app.services.calibration.homography import compute_homography
from app.services.game_config import load_game_config
from app.services.jobs import (
    DEFAULT_ANALYSIS_VERSION,
    analyze_match,
    build_analysis_params_hash,
)
from app.services.ml.shadow import auto_train_shadow_models_for_event_breakdown
from app.services.vision.perimeter_resolver import resolve_perimeter_type_for_event_profile
from app.services.ratings.model import recompute_event_ratings
from app.services.ml.synergy import precompute_event_synergy
from app.services.utils import COMP_LEVEL_ORDER
from app.services.vision.video_extraction import (
    VideoExtractionError,
    cleanup_prepared_youtube_source,
    prepare_youtube_video_source,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
ANALYSIS_VERSION = DEFAULT_ANALYSIS_VERSION
ACTIVE_STATUSES = {"queued", "running"}
AUTO_CALIBRATION_SEED_ATTEMPTS_WITH_CLONE = 8
ANALYSIS_JOB_TIMEOUT_SEC = max(120, int(getattr(settings, "analysis_job_timeout_sec", 3600)))
ANALYSIS_JOB_RESULT_TTL_SEC = max(300, int(getattr(settings, "analysis_job_result_ttl_sec", 86400)))
ANALYSIS_JOB_FAILURE_TTL_SEC = max(600, int(getattr(settings, "analysis_job_failure_ttl_sec", 604800)))
ANALYSIS_JOB_RETRY_MAX = max(0, int(getattr(settings, "analysis_job_retry_max", 2)))

# ── Queue helpers ──────────────────────────────────────────────────────────

def get_queue() -> Queue:
    # Return the default RQ queue backed by the configured Redis.
    connection = redis.from_url(settings.redis_url)
    return Queue("default", connection=connection)

def _analysis_job_id(
    match_key: str,
    analysis_version: str,
    params_hash: str,
    calibration_id: int,
) -> str:
    raw = f"{match_key}|{analysis_version}|{params_hash}|{calibration_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"analyze:{match_key}:{digest}"

def _job_is_pending_in_queue(queue: Queue, job_id: str, status: str) -> bool:
    connection = queue.connection
    normalized = (status or "").lower()
    if normalized == "queued":
        try:
            return connection.lpos(queue.key, job_id) is not None
        except Exception:
            return job_id in queue.job_ids
    if normalized == "started":
        key = StartedJobRegistry(queue=queue).key
        return connection.zscore(key, job_id) is not None
    if normalized == "deferred":
        key = DeferredJobRegistry(queue=queue).key
        return connection.zscore(key, job_id) is not None
    if normalized == "scheduled":
        key = ScheduledJobRegistry(queue=queue).key
        return connection.zscore(key, job_id) is not None
    return False

def _pending_job_count(queue: Queue) -> int:
    try:
        queued = len(queue.job_ids)
        started = len(StartedJobRegistry(queue=queue).get_job_ids())
        deferred = len(DeferredJobRegistry(queue=queue).get_job_ids())
        scheduled = len(ScheduledJobRegistry(queue=queue).get_job_ids())
        return int(queued + started + deferred + scheduled)
    except Exception:
        return 0

def _enqueue_analysis_job(
    queue: Queue,
    *,
    match_key: str,
    analysis_version: str,
    params_hash: str,
    calibration_id: int,
    analyze_kwargs: dict[str, Any] | None = None,
) -> tuple[Job | None, str, str]:
    job_id = _analysis_job_id(
        match_key=match_key,
        analysis_version=analysis_version,
        params_hash=params_hash,
        calibration_id=calibration_id,
    )
    existing_job = queue.fetch_job(job_id)
    if existing_job is not None:
        status = (existing_job.get_status() or "unknown").lower()
        if status in {"queued", "started", "deferred", "scheduled"} and _job_is_pending_in_queue(
            queue, job_id, status
        ):
            return None, job_id, status
        with suppress(Exception):
            existing_job.delete(delete_dependents=False)

    retry_intervals_raw = str(getattr(settings, "analysis_job_retry_intervals_sec", "45,180") or "").strip()
    retry_intervals: list[int] = []
    if retry_intervals_raw:
        for token in retry_intervals_raw.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                value = int(float(token))
            except ValueError:
                continue
            if value > 0:
                retry_intervals.append(value)
    retry_cfg = None
    if ANALYSIS_JOB_RETRY_MAX > 0:
        retry_cfg = Retry(max=ANALYSIS_JOB_RETRY_MAX, interval=retry_intervals or [45, 180])

    job = queue.enqueue_call(
        func=analyze_match,
        args=(match_key, analysis_version, params_hash, calibration_id),
        kwargs=(analyze_kwargs or {}),
        job_id=job_id,
        timeout=ANALYSIS_JOB_TIMEOUT_SEC,
        result_ttl=ANALYSIS_JOB_RESULT_TTL_SEC,
        failure_ttl=ANALYSIS_JOB_FAILURE_TTL_SEC,
        retry=retry_cfg,
    )
    return job, job_id, (job.get_status() or "queued")

# ── DB query helpers ───────────────────────────────────────────────────────

def _latest_matching_run(
    db: Session,
    *,
    match_key: str,
    analysis_version: str,
    params_hash: str,
    calibration_id: int,
) -> tuple[models.AnalysisRun, models.AnalysisRunContext] | None:
    return (
        db.query(models.AnalysisRun, models.AnalysisRunContext)
        .join(models.AnalysisRunContext, models.AnalysisRunContext.run_id == models.AnalysisRun.id)
        .filter(
            models.AnalysisRun.match_key == match_key,
            models.AnalysisRunContext.match_key == match_key,
            models.AnalysisRunContext.analysis_version == analysis_version,
            models.AnalysisRunContext.params_hash == params_hash,
            models.AnalysisRunContext.calibration_id == calibration_id,
            models.AnalysisRun.status.in_(ACTIVE_STATUSES | {"completed"}),
        )
        .order_by(models.AnalysisRun.created_at.desc(), models.AnalysisRun.id.desc())
        .first()
    )

def _resolve_event_perimeter_type(
    db: Session,
    event_key: str,
) -> tuple[str, str]:
    event_profile = db.get(models.EventProfile, event_key)
    return resolve_perimeter_type_for_event_profile(event_profile)

# ── Calibration helpers ────────────────────────────────────────────────────

def _clone_event_calibration_template(
    db: Session,
    event_key: str,
) -> dict:
    template = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.event_key == event_key)
        .order_by(models.FieldCalibration.updated_at.desc(), models.FieldCalibration.id.desc())
        .first()
    )
    if template is None:
        return {
            "template_found": False,
            "created": 0,
            "skipped_existing": 0,
        }

    match_rows = (
        db.query(models.Match.match_key)
        .filter(models.Match.event_key == event_key)
        .all()
    )
    event_match_keys = [row[0] for row in match_rows]
    if not event_match_keys:
        return {
            "template_found": True,
            "template_match_key": template.match_key,
            "created": 0,
            "skipped_existing": 0,
        }

    existing = {
        row[0]
        for row in (
            db.query(models.FieldCalibration.match_key)
            .filter(models.FieldCalibration.match_key.in_(event_match_keys))
            .all()
        )
    }
    created = 0
    skipped_existing = 0
    for match_key in event_match_keys:
        if match_key in existing:
            skipped_existing += 1
            continue
        db.add(
            models.FieldCalibration(
                match_key=match_key,
                event_key=event_key,
                frame_time_sec=template.frame_time_sec,
                image_width=template.image_width,
                image_height=template.image_height,
                image_points=template.image_points,
                field_points=template.field_points,
                homography=template.homography,
            )
        )
        created += 1

    if created > 0:
        db.commit()

    return {
        "template_found": True,
        "template_match_key": template.match_key,
        "created": created,
        "skipped_existing": skipped_existing,
    }

def _latest_event_calibration_template(
    db: Session,
    event_key: str,
) -> models.FieldCalibration | None:
    return (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.event_key == event_key)
        .order_by(models.FieldCalibration.updated_at.desc(), models.FieldCalibration.id.desc())
        .first()
    )

def _clone_template_calibration_to_match(
    db: Session,
    *,
    template: models.FieldCalibration | None,
    event_key: str,
    match_key: str,
) -> models.FieldCalibration | None:
    existing = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key == match_key)
        .one_or_none()
    )
    if existing is not None:
        return existing
    if template is None:
        return None

    clone = models.FieldCalibration(
        match_key=match_key,
        event_key=event_key,
        frame_time_sec=template.frame_time_sec,
        image_width=template.image_width,
        image_height=template.image_height,
        image_points=template.image_points,
        field_points=template.field_points,
        homography=template.homography,
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return clone

def _build_synthetic_calibration_payload(
    *,
    image_width: int,
    image_height: int,
    frame_time_sec: float | None,
) -> dict[str, Any]:
    if int(image_width) < 64 or int(image_height) < 64:
        raise AutoCalibrationError("Synthetic fallback unavailable: frame dimensions are too small.")

    config = load_game_config()
    field_length_m = float(getattr(config.field, "length_m", 0.0) or 0.0)
    field_width_m = float(getattr(config.field, "width_m", 0.0) or 0.0)
    if field_length_m <= 0.0 or field_width_m <= 0.0:
        raise AutoCalibrationError("Synthetic fallback unavailable: game field dimensions are missing.")

    margin_ratio = float(getattr(settings, "automation_regional_auto_calibration_synthetic_margin_ratio", 0.08) or 0.08)
    margin_ratio = max(0.0, min(margin_ratio, 0.25))
    margin_x = max(2.0, float(image_width) * margin_ratio)
    margin_y = max(2.0, float(image_height) * margin_ratio)

    image_points = [
        {"x": round(margin_x, 3), "y": round(margin_y, 3)},
        {"x": round(float(image_width) - margin_x, 3), "y": round(margin_y, 3)},
        {"x": round(float(image_width) - margin_x, 3), "y": round(float(image_height) - margin_y, 3)},
        {"x": round(margin_x, 3), "y": round(float(image_height) - margin_y, 3)},
    ]
    field_points = [
        {"x": 0.0, "y": 0.0},
        {"x": round(field_length_m, 4), "y": 0.0},
        {"x": round(field_length_m, 4), "y": round(field_width_m, 4)},
        {"x": 0.0, "y": round(field_width_m, 4)},
    ]

    return {
        "frame_time_sec": round(float(frame_time_sec or 0.0), 3),
        "image_width": int(image_width),
        "image_height": int(image_height),
        "image_points": image_points,
        "field_points": field_points,
        "homography": None,
        "diagnostics": {
            "mode": "synthetic_fallback",
            "field_length_m": round(field_length_m, 4),
            "field_width_m": round(field_width_m, 4),
            "margin_ratio": round(margin_ratio, 4),
            "sample_count_requested": 0,
            "sampled_frame_times_sec": [round(float(frame_time_sec or 0.0), 3)],
        },
    }

# ── Match target resolution ───────────────────────────────────────────────

def _event_match_targets(
    db: Session,
    *,
    event_key: str,
    allowed_team_keys: set[str] | None,
) -> tuple[list[models.Match], list[str], dict[str, set[str]]]:
    match_rows = (
        db.query(models.Match)
        .filter(models.Match.event_key == event_key)
        .order_by(models.Match.comp_level.asc(), models.Match.set_number.asc(), models.Match.match_number.asc())
        .all()
    )
    if not match_rows:
        return [], [], {}

    match_keys = [row.match_key for row in match_rows]
    match_team_rows = (
        db.query(models.MatchTeam)
        .filter(models.MatchTeam.match_key.in_(match_keys))
        .all()
    )
    teams_by_match: dict[str, set[str]] = {}
    for row in match_team_rows:
        teams_by_match.setdefault(row.match_key, set()).add(row.team_key)

    if allowed_team_keys is None:
        target_match_keys = list(match_keys)
    else:
        target_match_keys = [
            match_key
            for match_key in match_keys
            if teams_by_match.get(match_key, set()) & allowed_team_keys
        ]
    return match_rows, target_match_keys, teams_by_match

# ── Calibration upsert ─────────────────────────────────────────────────────

def _upsert_field_calibration_record(
    db: Session,
    *,
    match_key: str,
    event_key: str,
    frame_time_sec: float | None,
    image_width: int,
    image_height: int,
    image_points: list[dict[str, Any]],
    field_points: list[dict[str, Any]],
) -> tuple[models.FieldCalibration, str]:
    if len(image_points) < 4 or len(field_points) < 4:
        raise AutoCalibrationError("At least 4 image/field point pairs are required.")
    if len(image_points) != len(field_points):
        raise AutoCalibrationError("image_points and field_points must have equal length.")

    normalized_image_points: list[dict[str, float]] = []
    normalized_field_points: list[dict[str, float]] = []
    image_pairs: list[tuple[float, float]] = []
    field_pairs: list[tuple[float, float]] = []

    for image_point, field_point in zip(image_points, field_points, strict=True):
        ix = float(image_point.get("x"))
        iy = float(image_point.get("y"))
        fx = float(field_point.get("x"))
        fy = float(field_point.get("y"))
        normalized_image_points.append({"x": ix, "y": iy})
        normalized_field_points.append({"x": fx, "y": fy})
        image_pairs.append((ix, iy))
        field_pairs.append((fx, fy))

    try:
        homography = compute_homography(image_pairs, field_pairs)
    except Exception as exc:
        raise AutoCalibrationError(f"Failed to compute homography from auto-calibration points: {exc}") from exc

    calibration = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key == match_key)
        .one_or_none()
    )
    write_mode = "updated" if calibration is not None else "created"

    if calibration is None:
        calibration = models.FieldCalibration(
            match_key=match_key,
            event_key=event_key,
            frame_time_sec=frame_time_sec,
            image_width=int(image_width),
            image_height=int(image_height),
            image_points=normalized_image_points,
            field_points=normalized_field_points,
            homography=homography,
        )
        db.add(calibration)
    else:
        calibration.event_key = event_key
        calibration.frame_time_sec = frame_time_sec
        calibration.image_width = int(image_width)
        calibration.image_height = int(image_height)
        calibration.image_points = normalized_image_points
        calibration.field_points = normalized_field_points
        calibration.homography = homography

    db.commit()
    db.refresh(calibration)
    return calibration, write_mode

# ── Auto-calibration ───────────────────────────────────────────────────────

def _auto_calibrate_event_matches(
    db: Session,
    *,
    event_key: str,
    match_rows: list[models.Match],
    perimeter_type: str,
    focus_time_sec: float | None,
    sample_count: int,
    min_inliers: int,
    ransac_reproj_threshold_px: float,
    refresh_video: bool,
    overwrite_existing: bool,
    allow_synthetic_fallback: bool,
) -> dict:
    if not match_rows:
        return {
            "ran": True,
            "event_key": event_key,
            "perimeter_type": perimeter_type,
            "sample_count": int(sample_count),
            "min_inliers": int(min_inliers),
            "ransac_reproj_threshold_px": float(ransac_reproj_threshold_px),
            "refresh_video": bool(refresh_video),
            "overwrite_existing": bool(overwrite_existing),
            "total_matches": 0,
            "attempted_count": 0,
            "existing_count": 0,
            "missing_video_count": 0,
            "failed_count": 0,
            "created_count": 0,
            "updated_count": 0,
            "synthetic_fallback_count": 0,
            "results": [],
        }

    target_match_keys = [match.match_key for match in match_rows]
    existing_by_match = {
        row.match_key: row
        for row in (
            db.query(models.FieldCalibration)
            .filter(models.FieldCalibration.match_key.in_(target_match_keys))
            .all()
        )
    }
    youtube_by_match = {
        row.match_key: row
        for row in (
            db.query(models.MatchVideo)
            .filter(
                models.MatchVideo.match_key.in_(target_match_keys),
                models.MatchVideo.video_type == "youtube",
                models.MatchVideo.url.isnot(None),
            )
            .order_by(models.MatchVideo.id.asc())
            .all()
        )
    }

    attempted_count = 0
    existing_count = 0
    missing_video_count = 0
    failed_count = 0
    created_count = 0
    updated_count = 0
    synthetic_fallback_count = 0
    results: list[dict] = []

    for match in match_rows:
        match_key = match.match_key
        existing = existing_by_match.get(match_key)
        youtube_video = youtube_by_match.get(match_key)

        if existing is not None and not overwrite_existing:
            existing_count += 1
            results.append(
                {
                    "match_key": match_key,
                    "status": "existing_calibration",
                    "calibration_id": int(existing.id),
                    "video_status": "found" if youtube_video is not None else "missing",
                    "reason": None,
                }
            )
            continue

        if youtube_video is None:
            if existing is not None:
                existing_count += 1
                results.append(
                    {
                        "match_key": match_key,
                        "status": "existing_calibration",
                        "calibration_id": int(existing.id),
                        "video_status": "missing",
                        "reason": "youtube_video_missing; retained existing calibration",
                    }
                )
            else:
                missing_video_count += 1
                results.append(
                    {
                        "match_key": match_key,
                        "status": "missing_youtube_video",
                        "video_status": "missing",
                        "reason": "No YouTube video found for this match.",
                    }
                )
            continue

        attempted_count += 1
        prepared_source = None
        try:
            calibration_mode = "auto_apriltag_homography"
            fallback_reason: str | None = None
            prepared_source = prepare_youtube_video_source(
                match_key=match_key,
                youtube_url=str(youtube_video.url),
                force_download_refresh=bool(refresh_video),
            )
            metadata = prepared_source.video_metadata
            try:
                auto_payload = auto_calibrate_from_video(
                    video_path=prepared_source.video_source,
                    perimeter_type=perimeter_type,
                    duration_sec=float(metadata.get("duration_sec") or 0.0),
                    sample_count=int(sample_count),
                    focus_time_sec=focus_time_sec,
                    min_inliers=int(min_inliers),
                    ransac_reproj_threshold_px=float(ransac_reproj_threshold_px),
                )
            except AutoCalibrationError as exc:
                if not allow_synthetic_fallback:
                    raise
                fallback_reason = sanitize_external_error(exc, default="AprilTag homography failed.")
                auto_payload = _build_synthetic_calibration_payload(
                    image_width=int(metadata.get("width") or 0),
                    image_height=int(metadata.get("height") or 0),
                    frame_time_sec=float(metadata.get("duration_sec") or 0.0) * 0.5,
                )
                calibration_mode = "synthetic_fallback"
            calibration, write_mode = _upsert_field_calibration_record(
                db,
                match_key=match_key,
                event_key=event_key,
                frame_time_sec=float(auto_payload["frame_time_sec"]),
                image_width=int(auto_payload["image_width"]),
                image_height=int(auto_payload["image_height"]),
                image_points=list(auto_payload["image_points"]),
                field_points=list(auto_payload["field_points"]),
            )
            if write_mode == "created":
                created_count += 1
            else:
                updated_count += 1
            if calibration_mode == "synthetic_fallback":
                synthetic_fallback_count += 1

            diagnostics = auto_payload.get("diagnostics") if isinstance(auto_payload, dict) else {}
            selected = diagnostics.get("selected") if isinstance(diagnostics, dict) else {}
            detected_tag_ids = (
                selected.get("detected_tag_ids")
                if isinstance(selected, dict) and isinstance(selected.get("detected_tag_ids"), list)
                else []
            )

            results.append(
                {
                    "match_key": match_key,
                    "status": (
                        "auto_calibrated_synthetic_fallback"
                        if calibration_mode == "synthetic_fallback"
                        else "auto_calibrated"
                    ),
                    "calibration_mode": calibration_mode,
                    "write_mode": write_mode,
                    "calibration_id": int(calibration.id),
                    "video_status": "found",
                    "video_metadata": metadata,
                    "video_source_mode": prepared_source.source_mode,
                    "stream_fallback_reason": prepared_source.stream_error,
                    "tags_detected_count": len(detected_tag_ids),
                    "detected_tag_ids": detected_tag_ids,
                    "inlier_count": int(selected.get("inlier_count") or 0) if isinstance(selected, dict) else 0,
                    "inlier_ratio": float(selected.get("inlier_ratio") or 0.0)
                    if isinstance(selected, dict)
                    else 0.0,
                    "reprojection_rmse_m": float(selected.get("reprojection_rmse_m") or 0.0)
                    if isinstance(selected, dict)
                    else 0.0,
                    "frames_with_any_tags": int(diagnostics.get("frames_with_any_tags") or 0)
                    if isinstance(diagnostics, dict)
                    else 0,
                    "frames_with_known_tags": int(diagnostics.get("frames_with_known_tags") or 0)
                    if isinstance(diagnostics, dict)
                    else 0,
                    "max_correspondences": int(diagnostics.get("max_correspondences") or 0)
                    if isinstance(diagnostics, dict)
                    else 0,
                    "reason": None,
                    "fallback_reason": fallback_reason,
                }
            )
        except (VideoExtractionError, AutoCalibrationError) as exc:
            failed_count += 1
            results.append(
                {
                    "match_key": match_key,
                    "status": "auto_calibration_failed",
                    "video_status": "found",
                    "reason": sanitize_external_error(exc, default="Auto calibration failed."),
                }
            )
        finally:
            cleanup_prepared_youtube_source(prepared_source)

    logger.info(
        "auto_calibration.completed event=%s total=%d attempted=%d "
        "created=%d updated=%d failed=%d synthetic_fallback=%d",
        event_key,
        len(match_rows),
        attempted_count,
        created_count,
        updated_count,
        failed_count,
        synthetic_fallback_count,
    )
    return {
        "ran": True,
        "event_key": event_key,
        "perimeter_type": perimeter_type,
        "sample_count": int(sample_count),
        "min_inliers": int(min_inliers),
        "ransac_reproj_threshold_px": float(ransac_reproj_threshold_px),
        "refresh_video": bool(refresh_video),
        "overwrite_existing": bool(overwrite_existing),
        "total_matches": len(match_rows),
        "attempted_count": attempted_count,
        "existing_count": existing_count,
        "missing_video_count": missing_video_count,
        "failed_count": failed_count,
        "created_count": created_count,
        "updated_count": updated_count,
        "synthetic_fallback_count": synthetic_fallback_count,
        "results": results,
    }

# ── Queue scheduling ──────────────────────────────────────────────────────

def _queue_event_matches_for_team_keys(
    db: Session,
    queue: Queue,
    *,
    event_key: str,
    allowed_team_keys: set[str] | None,
    force: bool,
    require_video: bool,
    require_calibration: bool,
    max_new_jobs: int | None = None,
    max_pending_jobs: int | None = None,
) -> dict:
    max_schedule = int(max_new_jobs if max_new_jobs is not None else settings.analysis_queue_max_schedule_per_call)
    max_schedule = max(1, min(max_schedule, 5000))
    max_pending = int(max_pending_jobs if max_pending_jobs is not None else settings.analysis_queue_max_pending_jobs)
    max_pending = max(10, min(max_pending, 20000))
    queue_pending_before = _pending_job_count(queue)

    match_rows = (
        db.query(models.Match)
        .filter(models.Match.event_key == event_key)
        .order_by(models.Match.comp_level.asc(), models.Match.set_number.asc(), models.Match.match_number.asc())
        .all()
    )
    if not match_rows:
        return {
            "scheduled": [],
            "skipped": [],
            "blocked": [],
            "target_matches": 0,
            "queue_pending_before": int(queue_pending_before),
            "queue_pending_after_estimate": int(queue_pending_before),
            "max_pending_jobs": int(max_pending),
            "max_new_jobs": int(max_schedule),
        }

    match_keys = [row.match_key for row in match_rows]
    match_team_rows = (
        db.query(models.MatchTeam)
        .filter(models.MatchTeam.match_key.in_(match_keys))
        .all()
    )
    teams_by_match: dict[str, set[str]] = {}
    for row in match_team_rows:
        teams_by_match.setdefault(row.match_key, set()).add(row.team_key)

    if allowed_team_keys is None:
        target_match_keys = list(match_keys)
    else:
        target_match_keys = [
            match_key
            for match_key in match_keys
            if teams_by_match.get(match_key, set()) & allowed_team_keys
        ]

    calibration_rows = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key.in_(target_match_keys))
        .all()
    )
    calibration_by_match = {row.match_key: row for row in calibration_rows}
    calibrated_match_keys = set(calibration_by_match.keys())
    template_calibration = _latest_event_calibration_template(db, event_key)
    template_clone_count = 0

    video_match_keys = {
        row[0]
        for row in (
            db.query(models.MatchVideo.match_key)
            .filter(
                models.MatchVideo.match_key.in_(target_match_keys),
                models.MatchVideo.video_type == "youtube",
                models.MatchVideo.url.isnot(None),
            )
            .all()
        )
    }

    scheduled_matches: list[dict] = []
    skipped_matches: list[dict] = []
    blocked_matches: list[dict] = []
    perimeter_type, perimeter_source = _resolve_event_perimeter_type(db, event_key)

    for match in match_rows:
        if match.match_key not in target_match_keys:
            continue

        if len(scheduled_matches) >= max_schedule:
            blocked_matches.append(
                {
                    "match_key": match.match_key,
                    "reasons": ["queue_schedule_cap_reached"],
                }
            )
            continue
        if (queue_pending_before + len(scheduled_matches)) >= max_pending:
            blocked_matches.append(
                {
                    "match_key": match.match_key,
                    "reasons": ["queue_backpressure"],
                }
            )
            continue

        reasons: list[str] = []
        if require_video and match.match_key not in video_match_keys:
            reasons.append("missing_youtube_video")
        if require_calibration and match.match_key not in calibrated_match_keys:
            reasons.append("missing_calibration")
        if reasons:
            blocked_matches.append({"match_key": match.match_key, "reasons": reasons})
            continue

        calibration = calibration_by_match.get(match.match_key)
        if calibration is None and not require_calibration:
            calibration = _clone_template_calibration_to_match(
                db,
                template=template_calibration,
                event_key=event_key,
                match_key=match.match_key,
            )
            if calibration is not None:
                calibration_by_match[match.match_key] = calibration
                calibrated_match_keys.add(match.match_key)
                template_clone_count += 1
                if template_calibration is None:
                    template_calibration = calibration
        if calibration is None:
            missing_reasons = ["missing_calibration"]
            if not require_calibration:
                missing_reasons.append("missing_calibration_template")
            blocked_matches.append({"match_key": match.match_key, "reasons": missing_reasons})
            continue

        params_hash = build_analysis_params_hash(
            analysis_version=ANALYSIS_VERSION,
            calibration_id=calibration.id,
            perimeter_type=perimeter_type,
        )
        if not force:
            existing = _latest_matching_run(
                db,
                match_key=match.match_key,
                analysis_version=ANALYSIS_VERSION,
                params_hash=params_hash,
                calibration_id=calibration.id,
            )
            if existing is not None:
                latest, _ = existing
                skipped_matches.append(
                    {
                        "match_key": match.match_key,
                        "latest_run_id": latest.id,
                        "latest_status": latest.status,
                        "reason": "already_analyzed_or_in_progress",
                        "params_hash": params_hash,
                        "calibration_id": calibration.id,
                        "perimeter_type": perimeter_type,
                        "perimeter_source": perimeter_source,
                    }
                )
                continue

        job, job_id, job_status = _enqueue_analysis_job(
            queue,
            match_key=match.match_key,
            analysis_version=ANALYSIS_VERSION,
            params_hash=params_hash,
            calibration_id=calibration.id,
        )
        if job is None:
            skipped_matches.append(
                {
                    "match_key": match.match_key,
                    "latest_run_id": None,
                    "latest_status": job_status,
                    "reason": "already_queued",
                    "params_hash": params_hash,
                    "calibration_id": calibration.id,
                    "perimeter_type": perimeter_type,
                    "perimeter_source": perimeter_source,
                    "job_id": job_id,
                }
            )
            continue
        scheduled_matches.append(
            {
                "match_key": match.match_key,
                "job_id": job_id,
                "analysis_version": ANALYSIS_VERSION,
                "params_hash": params_hash,
                "calibration_id": calibration.id,
                "perimeter_type": perimeter_type,
                "perimeter_source": perimeter_source,
            }
        )

    return {
        "scheduled": scheduled_matches,
        "skipped": skipped_matches,
        "blocked": blocked_matches,
        "target_matches": len(target_match_keys),
        "queue_pending_before": int(queue_pending_before),
        "queue_pending_after_estimate": int(queue_pending_before + len(scheduled_matches)),
        "max_pending_jobs": int(max_pending),
        "max_new_jobs": int(max_schedule),
        "template_clone_count": int(template_clone_count),
    }

# ── Main event pipeline ───────────────────────────────────────────────────

def run_event_pipeline(
    db: Session,
    queue: Queue,
    *,
    event_key: str,
    force_analysis: bool,
    allowed_team_keys: set[str] | None,
    clone_event_calibration: bool,
    require_video: bool,
    require_calibration: bool,
    run_post_compute: bool,
    synergy_model_version: str,
    quality_threshold: float,
    auto_calibrate_missing: bool,
    auto_calibration_overwrite_existing: bool,
    auto_calibration_refresh_video: bool,
    auto_calibration_sample_count: int,
    auto_calibration_min_inliers: int,
    auto_calibration_ransac_reproj_threshold_px: float,
    auto_calibration_focus_time_sec: float | None,
    max_new_jobs: int | None = None,
    max_pending_jobs: int | None = None,
) -> dict:
    # Orchestrate the full analysis pipeline for a single event.
    #
    # Steps: ingest → team filtering → match targeting → calibration →
    # auto-calibration → job queueing → post-compute (synergy/ratings).
    from app.services.events.ingest import ingest_event as ingest_event_data
    from app.tba.client import TBAClient

    pipeline_t0 = time.perf_counter()
    logger.info("event_pipeline.start event=%s", event_key)

    ingest_result = {"ok": False, "detail": "skipped"}
    try:
        tba = TBAClient()
        ingest_result = ingest_event_data(event_key=event_key, tba=tba, db=db)
    except Exception as exc:
        logger.error("event_pipeline.ingest_failed event=%s error=%s", event_key, str(exc)[:200])
        return {
            "event_key": event_key,
            "status": "ingest_failed",
            "detail": sanitize_external_error(exc, default="Event ingest failed."),
        }

    local_event_team_keys = {
        row[0]
        for row in (
            db.query(models.EventTeam.team_key)
            .filter(models.EventTeam.event_key == event_key)
            .all()
        )
    }
    selected_team_keys = (
        local_event_team_keys
        if allowed_team_keys is None
        else (local_event_team_keys & allowed_team_keys)
    )
    if allowed_team_keys is not None and not selected_team_keys:
        return {
            "event_key": event_key,
            "status": "no_target_teams_detected",
            "ingest": ingest_result,
            "target_teams_in_event": 0,
        }

    match_rows, target_match_keys, _ = _event_match_targets(
        db,
        event_key=event_key,
        allowed_team_keys=selected_team_keys,
    )
    target_match_set = set(target_match_keys)
    target_match_rows = [match for match in match_rows if match.match_key in target_match_set]

    perimeter_type, perimeter_source = _resolve_event_perimeter_type(db, event_key)
    video_match_keys = {
        row[0]
        for row in (
            db.query(models.MatchVideo.match_key)
            .filter(
                models.MatchVideo.match_key.in_(target_match_keys),
                models.MatchVideo.video_type == "youtube",
                models.MatchVideo.url.isnot(None),
            )
            .all()
        )
    }
    calibration_match_keys_before = {
        row[0]
        for row in (
            db.query(models.FieldCalibration.match_key)
            .filter(models.FieldCalibration.match_key.in_(target_match_keys))
            .all()
        )
    }

    auto_calibration_result: dict[str, Any] = {
        "ran": False,
        "event_key": event_key,
        "perimeter_type": perimeter_type,
        "sample_count": int(auto_calibration_sample_count),
        "min_inliers": int(auto_calibration_min_inliers),
        "ransac_reproj_threshold_px": float(auto_calibration_ransac_reproj_threshold_px),
        "refresh_video": bool(auto_calibration_refresh_video),
        "overwrite_existing": bool(auto_calibration_overwrite_existing),
        "total_matches": len(target_match_rows),
        "attempted_count": 0,
        "existing_count": 0,
        "missing_video_count": 0,
        "failed_count": 0,
        "created_count": 0,
        "updated_count": 0,
        "results": [],
    }
    calibration_clone_passes: list[dict[str, Any]] = []
    if clone_event_calibration:
        calibration_clone_passes.append(_clone_event_calibration_template(db, event_key))

    calibration_match_keys_after_pre_clone = {
        row[0]
        for row in (
            db.query(models.FieldCalibration.match_key)
            .filter(models.FieldCalibration.match_key.in_(target_match_keys))
            .all()
        )
    }
    missing_calibration_rows = [
        match for match in target_match_rows if match.match_key not in calibration_match_keys_after_pre_clone
    ]

    auto_calibration_scope = "all_missing_targets"
    auto_calibration_targets = list(missing_calibration_rows)
    if clone_event_calibration:
        auto_calibration_scope = "seed_missing_targets_for_clone"
        rows_with_video = sorted(
            (match for match in missing_calibration_rows if match.match_key in video_match_keys),
            key=lambda match: (
                COMP_LEVEL_ORDER.get(str(match.comp_level or "").lower(), 99),
                int(match.set_number or 0),
                int(match.match_number or 0),
            ),
        )
        auto_calibration_targets = rows_with_video[:AUTO_CALIBRATION_SEED_ATTEMPTS_WITH_CLONE]

    if auto_calibrate_missing and auto_calibration_targets:
        auto_calibration_result = _auto_calibrate_event_matches(
            db,
            event_key=event_key,
            match_rows=auto_calibration_targets,
            perimeter_type=perimeter_type,
            focus_time_sec=auto_calibration_focus_time_sec,
            sample_count=int(auto_calibration_sample_count),
            min_inliers=int(auto_calibration_min_inliers),
            ransac_reproj_threshold_px=float(auto_calibration_ransac_reproj_threshold_px),
            refresh_video=bool(auto_calibration_refresh_video),
            overwrite_existing=bool(auto_calibration_overwrite_existing),
            allow_synthetic_fallback=bool(settings.automation_regional_auto_calibration_allow_synthetic_fallback),
        )
    auto_calibration_result["scope"] = auto_calibration_scope
    auto_calibration_result["candidate_missing_count"] = len(missing_calibration_rows)
    auto_calibration_result["selected_target_count"] = len(auto_calibration_targets)

    if clone_event_calibration and (
        int(auto_calibration_result.get("created_count") or 0) > 0
        or int(auto_calibration_result.get("updated_count") or 0) > 0
    ):
        calibration_clone_passes.append(_clone_event_calibration_template(db, event_key))

    calibration_clone_result: dict[str, Any] = {
        "template_found": None,
        "created": 0,
        "skipped_existing": 0,
        "passes": calibration_clone_passes,
    }
    if calibration_clone_passes:
        calibration_clone_result["template_found"] = any(
            bool(run_result.get("template_found")) for run_result in calibration_clone_passes
        )
        calibration_clone_result["created"] = sum(
            int(run_result.get("created") or 0) for run_result in calibration_clone_passes
        )
        calibration_clone_result["skipped_existing"] = sum(
            int(run_result.get("skipped_existing") or 0) for run_result in calibration_clone_passes
        )
        template_match_key = next(
            (
                str(run_result.get("template_match_key"))
                for run_result in calibration_clone_passes
                if isinstance(run_result.get("template_match_key"), str) and run_result.get("template_match_key")
            ),
            "",
        )
        if template_match_key:
            calibration_clone_result["template_match_key"] = template_match_key

    queue_result = _queue_event_matches_for_team_keys(
        db,
        queue,
        event_key=event_key,
        allowed_team_keys=selected_team_keys,
        force=force_analysis,
        require_video=require_video,
        require_calibration=require_calibration,
        max_new_jobs=max_new_jobs,
        max_pending_jobs=max_pending_jobs,
    )

    auto_result_by_match = {
        item.get("match_key"): item
        for item in auto_calibration_result.get("results", [])
        if isinstance(item, dict) and isinstance(item.get("match_key"), str)
    }
    auto_failure_by_match = {
        match_key: str(item.get("reason") or "Auto calibration failed.")
        for match_key, item in auto_result_by_match.items()
        if item.get("status") == "auto_calibration_failed"
    }
    for blocked in queue_result["blocked"]:
        match_key = blocked.get("match_key")
        if not isinstance(match_key, str):
            continue
        reasons = blocked.get("reasons") if isinstance(blocked.get("reasons"), list) else []
        if "missing_calibration" in reasons and match_key in auto_failure_by_match:
            if "auto_calibration_failed" not in reasons:
                reasons.append("auto_calibration_failed")
            blocked["reasons"] = reasons
            blocked["auto_calibration_error"] = auto_failure_by_match[match_key]

    post_compute = {"ran": False, "synergy": None, "ratings": None, "ml_shadow": None}
    if run_post_compute:
        synergy_result = None
        ratings_result = None
        ml_shadow_result: dict[str, Any] = {
            "triggered": False,
            "ok": False,
            "detail": "disabled",
        }
        try:
            synergy_result = precompute_event_synergy(
                db,
                event_key,
                model_version=synergy_model_version,
                quality_threshold=quality_threshold,
            )
        except Exception as exc:
            synergy_result = {
                "ok": False,
                "detail": sanitize_external_error(exc, default="Synergy precompute failed."),
            }

        try:
            ratings_result = recompute_event_ratings(db, event_key)
        except Exception as exc:
            ratings_result = {
                "ok": False,
                "detail": sanitize_external_error(exc, default="Ratings recompute failed."),
            }

        if bool(getattr(settings, "ml_shadow_auto_train_on_event_breakdown", False)):
            try:
                ml_shadow_result = auto_train_shadow_models_for_event_breakdown(
                    db,
                    event_key=event_key,
                    limit_events=int(getattr(settings, "ml_shadow_auto_train_limit_events", 40) or 40),
                    source_version=None,
                    activate=bool(getattr(settings, "ml_shadow_auto_train_activate", True)),
                    replace_predictions=True,
                )
            except Exception as exc:
                ml_shadow_result = {
                    "triggered": True,
                    "ok": False,
                    "detail": sanitize_external_error(exc, default="ML shadow auto-train failed."),
                }

            if bool(ml_shadow_result.get("ok")) and bool(
                getattr(settings, "ml_shadow_auto_train_recompute_ratings", True)
            ):
                try:
                    ratings_result = recompute_event_ratings(db, event_key)
                    ml_shadow_result["ratings_recomputed_after_train"] = True
                    ml_shadow_result["ratings_recompute_ok"] = bool(
                        (ratings_result or {}).get("ok")
                    )
                except Exception as exc:
                    ml_shadow_result["ratings_recomputed_after_train"] = False
                    ml_shadow_result["ratings_recompute_ok"] = False
                    ml_shadow_result["ratings_recompute_error"] = sanitize_external_error(
                        exc,
                        default="Ratings recompute after ML auto-train failed.",
                    )

        post_compute = {
            "ran": True,
            "synergy": synergy_result,
            "ratings": ratings_result,
            "ml_shadow": ml_shadow_result,
        }

    calibration_match_keys_after = {
        row[0]
        for row in (
            db.query(models.FieldCalibration.match_key)
            .filter(models.FieldCalibration.match_key.in_(target_match_keys))
            .all()
        )
    }

    scheduled_by_match = {
        item.get("match_key"): item
        for item in queue_result["scheduled"]
        if isinstance(item, dict) and isinstance(item.get("match_key"), str)
    }
    skipped_by_match = {
        item.get("match_key"): item
        for item in queue_result["skipped"]
        if isinstance(item, dict) and isinstance(item.get("match_key"), str)
    }
    blocked_by_match = {
        item.get("match_key"): item
        for item in queue_result["blocked"]
        if isinstance(item, dict) and isinstance(item.get("match_key"), str)
    }

    ratings_ok = bool((post_compute.get("ratings") or {}).get("ok")) if post_compute.get("ran") else False
    synergy_ok = bool((post_compute.get("synergy") or {}).get("ok")) if post_compute.get("ran") else False
    ratings_refresh_status = "not_run"
    if post_compute.get("ran"):
        ratings_refresh_status = "ok" if ratings_ok else "failed"
    synergy_refresh_status = "not_run"
    if post_compute.get("ran"):
        synergy_refresh_status = "ok" if synergy_ok else "failed"
    ml_shadow_refresh_status = "not_run"
    if post_compute.get("ran"):
        ml_shadow_auto_payload = post_compute.get("ml_shadow")
        if isinstance(ml_shadow_auto_payload, dict) and bool(ml_shadow_auto_payload.get("triggered")):
            ml_shadow_refresh_status = "trained" if bool(ml_shadow_auto_payload.get("ok")) else "train_failed"
        else:
            ml_shadow_payload = (post_compute.get("ratings") or {}).get("ml_shadow")
            team_strength_payload = (
                ml_shadow_payload.get("team_strength")
                if isinstance(ml_shadow_payload, dict)
                else None
            )
            if isinstance(ml_shadow_payload, dict):
                if not bool(ml_shadow_payload.get("enabled")):
                    ml_shadow_refresh_status = "disabled"
                elif isinstance(team_strength_payload, dict) and bool(team_strength_payload.get("prediction_ok")):
                    ml_shadow_refresh_status = "ok"
                else:
                    ml_shadow_refresh_status = "degraded"
            else:
                ml_shadow_refresh_status = "unavailable"

    match_pipeline: list[dict[str, Any]] = []
    for match in target_match_rows:
        match_key = match.match_key
        video_status = "found" if match_key in video_match_keys else "missing"

        auto_item = auto_result_by_match.get(match_key)
        calibration_status = "missing"
        calibration_source = "none"
        tags_detected_count: int | None = None
        detected_tag_ids: list[int] = []
        calibration_inlier_count: int | None = None
        calibration_inlier_ratio: float | None = None
        calibration_rmse_m: float | None = None
        failure_reason: str | None = None

        if isinstance(auto_item, dict):
            auto_status = str(auto_item.get("status") or "")
            if auto_status == "existing_calibration":
                calibration_status = "existing"
                calibration_source = "existing"
            elif auto_status in {"auto_calibrated", "auto_calibrated_synthetic_fallback"}:
                calibration_status = "auto_calibrated"
                calibration_source = (
                    "auto_synthetic"
                    if auto_status == "auto_calibrated_synthetic_fallback"
                    else "auto"
                )
                tags_detected_count = int(auto_item.get("tags_detected_count") or 0)
                detected_tag_ids = [
                    int(tag_id)
                    for tag_id in (auto_item.get("detected_tag_ids") or [])
                    if isinstance(tag_id, int)
                ]
                calibration_inlier_count = int(auto_item.get("inlier_count") or 0)
                calibration_inlier_ratio = float(auto_item.get("inlier_ratio") or 0.0)
                calibration_rmse_m = float(auto_item.get("reprojection_rmse_m") or 0.0)
            elif auto_status == "missing_youtube_video":
                calibration_status = "missing"
                calibration_source = "none"
                failure_reason = str(auto_item.get("reason") or "No YouTube video found for this match.")
            elif auto_status == "auto_calibration_failed":
                failure_reason = str(auto_item.get("reason") or "Auto calibration failed.")
                if match_key in calibration_match_keys_after:
                    calibration_status = "template_cloned"
                    calibration_source = "template_clone"
                else:
                    calibration_status = "failed"
                    calibration_source = "auto"

        if calibration_status == "missing" and match_key in calibration_match_keys_before:
            calibration_status = "existing"
            calibration_source = "existing"
        if calibration_status == "missing" and match_key in calibration_match_keys_after:
            calibration_status = (
                "template_cloned" if match_key not in calibration_match_keys_before else "existing"
            )
            calibration_source = "template_clone" if calibration_status == "template_cloned" else "existing"

        analysis_status = "not_queued"
        analysis_reason = ""
        analysis_job_id: str | None = None
        if match_key in scheduled_by_match:
            analysis_status = "queued"
            analysis_job_id = str(scheduled_by_match[match_key].get("job_id") or "")
        elif match_key in skipped_by_match:
            analysis_status = "skipped"
            analysis_reason = str(skipped_by_match[match_key].get("reason") or "")
            analysis_job_id = str(skipped_by_match[match_key].get("job_id") or "") or None
        elif match_key in blocked_by_match:
            analysis_status = "blocked"
            blocked_reasons = blocked_by_match[match_key].get("reasons")
            if isinstance(blocked_reasons, list):
                analysis_reason = ",".join(str(reason) for reason in blocked_reasons)
            else:
                analysis_reason = str(blocked_by_match[match_key].get("reason") or "blocked")
            if not failure_reason:
                failure_reason = str(blocked_by_match[match_key].get("auto_calibration_error") or "") or None

        match_pipeline.append(
            {
                "match_key": match_key,
                "video_status": video_status,
                "calibration_status": calibration_status,
                "calibration_source": calibration_source,
                "tags_detected_count": tags_detected_count,
                "detected_tag_ids": detected_tag_ids,
                "calibration_inlier_count": calibration_inlier_count,
                "calibration_inlier_ratio": calibration_inlier_ratio,
                "calibration_reprojection_rmse_m": calibration_rmse_m,
                "analysis_status": analysis_status,
                "analysis_reason": analysis_reason,
                "analysis_job_id": analysis_job_id,
                "ratings_refresh_status": ratings_refresh_status,
                "synergy_refresh_status": synergy_refresh_status,
                "ml_shadow_refresh_status": ml_shadow_refresh_status,
                "failure_reason": failure_reason,
            }
        )

    pipeline_status = {
        "target_matches": len(target_match_rows),
        "video_found_count": sum(1 for row in match_pipeline if row["video_status"] == "found"),
        "video_missing_count": sum(1 for row in match_pipeline if row["video_status"] == "missing"),
        "tags_detected_match_count": sum(
            1 for row in match_pipeline if isinstance(row.get("tags_detected_count"), int) and row["tags_detected_count"] > 0
        ),
        "calibrated_match_count": sum(
            1
            for row in match_pipeline
            if row["calibration_status"] in {"existing", "auto_calibrated", "template_cloned"}
        ),
        "calibration_failed_match_count": sum(1 for row in match_pipeline if row["calibration_status"] == "failed"),
        "analysis_queued_count": len(queue_result["scheduled"]),
        "analysis_skipped_count": len(queue_result["skipped"]),
        "analysis_blocked_count": len(queue_result["blocked"]),
        "queue_pending_before": int(queue_result.get("queue_pending_before") or 0),
        "queue_pending_after_estimate": int(queue_result.get("queue_pending_after_estimate") or 0),
        "queue_max_pending_jobs": int(queue_result.get("max_pending_jobs") or 0),
        "queue_max_new_jobs": int(queue_result.get("max_new_jobs") or 0),
        "ratings_refresh_status": ratings_refresh_status,
        "synergy_refresh_status": synergy_refresh_status,
        "ml_shadow_refresh_status": ml_shadow_refresh_status,
    }

    elapsed_ms = round((time.perf_counter() - pipeline_t0) * 1000, 1)
    logger.info(
        "event_pipeline.completed event=%s elapsed_ms=%.1f "
        "teams=%d target_matches=%d "
        "queued=%d skipped=%d blocked=%d "
        "auto_cal_created=%d auto_cal_failed=%d "
        "ratings=%s synergy=%s ml_shadow=%s",
        event_key,
        elapsed_ms,
        len(selected_team_keys),
        len(target_match_rows),
        len(queue_result["scheduled"]),
        len(queue_result["skipped"]),
        len(queue_result["blocked"]),
        int(auto_calibration_result.get("created_count") or 0),
        int(auto_calibration_result.get("failed_count") or 0),
        ratings_refresh_status,
        synergy_refresh_status,
        ml_shadow_refresh_status,
    )

    return {
        "event_key": event_key,
        "status": "processed",
        "ingest": ingest_result,
        "target_teams_in_event": len(selected_team_keys),
        "perimeter_type": perimeter_type,
        "perimeter_source": perimeter_source,
        "auto_calibration": auto_calibration_result,
        "calibration_clone": calibration_clone_result,
        "pipeline_status": pipeline_status,
        "match_pipeline": match_pipeline,
        "target_matches": queue_result["target_matches"],
        "scheduled": len(queue_result["scheduled"]),
        "skipped": len(queue_result["skipped"]),
        "blocked": len(queue_result["blocked"]),
        "scheduled_matches": queue_result["scheduled"],
        "skipped_matches": queue_result["skipped"],
        "blocked_matches": queue_result["blocked"],
        "queue_controls": {
            "pending_before": int(queue_result.get("queue_pending_before") or 0),
            "pending_after_estimate": int(queue_result.get("queue_pending_after_estimate") or 0),
            "max_pending_jobs": int(queue_result.get("max_pending_jobs") or 0),
            "max_new_jobs": int(queue_result.get("max_new_jobs") or 0),
        },
        "post_compute": post_compute,
        "elapsed_ms": elapsed_ms,
    }
