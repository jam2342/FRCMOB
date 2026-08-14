from datetime import datetime, timezone
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from rq.registry import DeferredJobRegistry, FailedJobRegistry, ScheduledJobRegistry, StartedJobRegistry
import redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin_access, require_write_access, sanitize_external_error
from app.db import models
from app.db.session import get_db
from app.services.calibration.auto import AutoCalibrationError, auto_calibrate_from_video
from app.services.jobs import build_analysis_params_hash
from app.services.analysis.live_monitor import (
    live_monitor_status,
    manual_live_monitor_tick,
    run_regional_live_auto_manager_tick,
    start_live_monitor,
    stop_live_monitor,
    regional_live_auto_manager_status,
)
from app.services.ml.synergy import QUALITY_THRESHOLD_DEFAULT, SYNERGY_MODEL_VERSION
from app.services.events.regional_automation import (
    RegionalAutomationError,
    run_regional_automation_tick,
    run_regional_post_event_breakdowns,
)
from app.services.vision.video_extraction import (
    VideoExtractionError,
    cleanup_prepared_youtube_source,
    prepare_youtube_video_source,
)
from app.services.utils import (
    automation_redis_key as _automation_redis_key,
    decode_redis_float as _decode_redis_float,
)

from app.services.events.pipeline import (
    get_queue,
    run_event_pipeline as _run_event_pipeline,
    ANALYSIS_VERSION,
    ANALYSIS_JOB_FAILURE_TTL_SEC,
    ANALYSIS_JOB_RESULT_TTL_SEC,
    ANALYSIS_JOB_RETRY_MAX,
    ANALYSIS_JOB_TIMEOUT_SEC,
    _build_synthetic_calibration_payload,
    _clone_template_calibration_to_match,
    _enqueue_analysis_job,
    _latest_event_calibration_template,
    _latest_matching_run,
    _resolve_event_perimeter_type,
    _upsert_field_calibration_record,
)

router = APIRouter(prefix="/analyze", tags=["analysis"])
ACTIVE_STATUSES = {"queued", "running"}


def _ensure_analysis_write_enabled() -> None:
    require_write_access("Analysis write endpoints")


@router.post("/{match_key}")
def enqueue_analysis(
    match_key: str,
    force: bool = False,
    auto_calibrate_if_missing: bool = False,
    auto_calibration_sample_count: int = 18,
    auto_calibration_min_inliers: int = 4,
    auto_calibration_ransac_reproj_threshold_px: float = 3.0,
    auto_calibration_focus_time_sec: float | None = None,
    auto_calibration_refresh_video: bool = False,
    db: Session = Depends(get_db),
):
    _ensure_analysis_write_enabled()
    match = db.get(models.Match, match_key)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    if auto_calibration_sample_count < 1 or auto_calibration_sample_count > 80:
        raise HTTPException(status_code=400, detail="auto_calibration_sample_count must be between 1 and 80")
    if auto_calibration_min_inliers < 4 or auto_calibration_min_inliers > 64:
        raise HTTPException(status_code=400, detail="auto_calibration_min_inliers must be between 4 and 64")
    if (
        auto_calibration_ransac_reproj_threshold_px <= 0.1
        or auto_calibration_ransac_reproj_threshold_px > 25.0
    ):
        raise HTTPException(
            status_code=400,
            detail="auto_calibration_ransac_reproj_threshold_px must be > 0.1 and <= 25.0",
        )
    if auto_calibration_focus_time_sec is not None and auto_calibration_focus_time_sec < 0:
        raise HTTPException(status_code=400, detail="auto_calibration_focus_time_sec must be >= 0")

    perimeter_type, perimeter_source = _resolve_event_perimeter_type(db, match.event_key)

    calibration = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key == match_key)
        .one_or_none()
    )
    auto_calibration_info: dict[str, Any] | None = None
    if calibration is None and auto_calibrate_if_missing:
        youtube_video = (
            db.query(models.MatchVideo)
            .filter(models.MatchVideo.match_key == match_key, models.MatchVideo.video_type == "youtube")
            .order_by(models.MatchVideo.id.asc())
            .first()
        )
        if youtube_video is None or not youtube_video.url:
            raise HTTPException(
                status_code=400,
                detail="Calibration missing and no YouTube video is available for auto-calibration.",
            )
        prepared_source = None
        try:
            prepared_source = prepare_youtube_video_source(
                match_key=match_key,
                youtube_url=str(youtube_video.url),
                force_download_refresh=bool(auto_calibration_refresh_video),
            )
            metadata = prepared_source.video_metadata
            calibration_mode = "auto_apriltag_homography"
            fallback_reason: str | None = None
            try:
                auto_payload = auto_calibrate_from_video(
                    video_path=prepared_source.video_source,
                    perimeter_type=perimeter_type,
                    duration_sec=float(metadata.get("duration_sec") or 0.0),
                    sample_count=int(auto_calibration_sample_count),
                    focus_time_sec=auto_calibration_focus_time_sec,
                    min_inliers=int(auto_calibration_min_inliers),
                    ransac_reproj_threshold_px=float(auto_calibration_ransac_reproj_threshold_px),
                )
            except AutoCalibrationError as exc:
                if not bool(settings.automation_regional_auto_calibration_allow_synthetic_fallback):
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
                event_key=match.event_key,
                frame_time_sec=float(auto_payload["frame_time_sec"]),
                image_width=int(auto_payload["image_width"]),
                image_height=int(auto_payload["image_height"]),
                image_points=list(auto_payload["image_points"]),
                field_points=list(auto_payload["field_points"]),
            )
            diagnostics = auto_payload.get("diagnostics") if isinstance(auto_payload, dict) else {}
            selected = diagnostics.get("selected") if isinstance(diagnostics, dict) else {}
            auto_calibration_info = {
                "ran": True,
                "mode": calibration_mode,
                "write_mode": write_mode,
                "calibration_id": int(calibration.id),
                "video_metadata": metadata,
                "video_source_mode": prepared_source.source_mode,
                "stream_fallback_reason": prepared_source.stream_error,
                "selected": selected if isinstance(selected, dict) else {},
                "fallback_reason": fallback_reason,
            }
        except VideoExtractionError as exc:
            raise HTTPException(
                status_code=502,
                detail=sanitize_external_error(exc, default="Unable to prepare video for auto calibration."),
            ) from exc
        except AutoCalibrationError as exc:
            raise HTTPException(
                status_code=422,
                detail=sanitize_external_error(exc, default="Auto calibration failed."),
            ) from exc
        finally:
            cleanup_prepared_youtube_source(prepared_source)

    if calibration is None:
        raise HTTPException(
            status_code=400,
            detail="Calibration required before analysis. Save one at POST /calibrations/{match_key}.",
        )

    params_hash = build_analysis_params_hash(
        analysis_version=ANALYSIS_VERSION,
        calibration_id=calibration.id,
        perimeter_type=perimeter_type,
    )

    if not force:
        existing = _latest_matching_run(
            db,
            match_key=match_key,
            analysis_version=ANALYSIS_VERSION,
            params_hash=params_hash,
            calibration_id=calibration.id,
        )
        if existing is not None:
            run, _ = existing
            return {
                "ok": True,
                "status": "skipped",
                "reason": "already_analyzed_or_in_progress",
                "match_key": match_key,
                "analysis_version": ANALYSIS_VERSION,
                "params_hash": params_hash,
                "calibration_id": calibration.id,
                "perimeter_type": perimeter_type,
                "perimeter_source": perimeter_source,
                "run_id": run.id,
                "run_status": run.status,
                "auto_calibration": auto_calibration_info,
            }

    job, job_id, job_status = _enqueue_analysis_job(
        get_queue(),
        match_key=match_key,
        analysis_version=ANALYSIS_VERSION,
        params_hash=params_hash,
        calibration_id=calibration.id,
    )
    if job is None:
        return {
            "ok": True,
            "status": "skipped",
            "reason": "already_queued",
            "match_key": match_key,
            "analysis_version": ANALYSIS_VERSION,
            "params_hash": params_hash,
            "calibration_id": calibration.id,
            "perimeter_type": perimeter_type,
            "perimeter_source": perimeter_source,
            "job_id": job_id,
            "job_status": job_status,
            "auto_calibration": auto_calibration_info,
        }
    return {
        "ok": True,
        "status": "queued",
        "match_key": match_key,
        "analysis_version": ANALYSIS_VERSION,
        "params_hash": params_hash,
        "calibration_id": calibration.id,
        "perimeter_type": perimeter_type,
        "perimeter_source": perimeter_source,
        "job_id": job_id,
        "auto_calibration": auto_calibration_info,
    }


@router.post("/event/{event_key}")
def enqueue_event_analysis(
    event_key: str,
    force: bool = False,
    require_video: bool = True,
    require_calibration: bool = True,
    db: Session = Depends(get_db),
):
    _ensure_analysis_write_enabled()
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")

    match_rows = (
        db.query(models.Match)
        .filter(models.Match.event_key == event_key)
        .order_by(models.Match.comp_level.asc(), models.Match.set_number.asc(), models.Match.match_number.asc())
        .all()
    )
    if not match_rows:
        return {
            "ok": True,
            "event_key": event_key,
            "event_name": event.name,
            "analysis_version": ANALYSIS_VERSION,
            "scheduled": 0,
            "skipped": 0,
            "blocked": 0,
            "scheduled_matches": [],
            "skipped_matches": [],
            "blocked_matches": [],
        }

    match_keys = [match.match_key for match in match_rows]
    calibration_rows = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key.in_(match_keys))
        .all()
    )
    calibration_by_match = {
        row.match_key: row
        for row in calibration_rows
    }
    calibrated_match_keys = set(calibration_by_match.keys())
    template_calibration = _latest_event_calibration_template(db, event_key)
    template_clone_count = 0
    video_match_keys = {
        row[0]
        for row in (
            db.query(models.MatchVideo.match_key)
            .filter(
                models.MatchVideo.match_key.in_(match_keys),
                models.MatchVideo.video_type == "youtube",
                models.MatchVideo.url.isnot(None),
            )
            .all()
        )
    }

    queue = get_queue()
    scheduled_matches: list[dict] = []
    skipped_matches: list[dict] = []
    blocked_matches: list[dict] = []
    perimeter_type, perimeter_source = _resolve_event_perimeter_type(db, event_key)

    for match in match_rows:
        reasons: list[str] = []
        if require_video and match.match_key not in video_match_keys:
            reasons.append("missing_youtube_video")
        if require_calibration and match.match_key not in calibrated_match_keys:
            reasons.append("missing_calibration")
        if reasons:
            blocked_matches.append(
                {
                    "match_key": match.match_key,
                    "reasons": reasons,
                }
            )
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
            blocked_matches.append(
                {
                    "match_key": match.match_key,
                    "reasons": (
                        ["missing_calibration", "missing_calibration_template"]
                        if not require_calibration
                        else ["missing_calibration"]
                    ),
                }
            )
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
                        "latest_version": ANALYSIS_VERSION,
                        "params_hash": params_hash,
                        "calibration_id": calibration.id,
                        "perimeter_type": perimeter_type,
                        "perimeter_source": perimeter_source,
                        "reason": "already_analyzed_or_in_progress",
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
                    "latest_version": ANALYSIS_VERSION,
                    "params_hash": params_hash,
                    "calibration_id": calibration.id,
                    "perimeter_type": perimeter_type,
                    "perimeter_source": perimeter_source,
                    "reason": "already_queued",
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
        "ok": True,
        "event_key": event_key,
        "event_name": event.name,
        "analysis_version": ANALYSIS_VERSION,
        "scheduled": len(scheduled_matches),
        "skipped": len(skipped_matches),
        "blocked": len(blocked_matches),
        "template_clone_count": int(template_clone_count),
        "scheduled_matches": scheduled_matches,
        "skipped_matches": skipped_matches,
        "blocked_matches": blocked_matches,
    }


@router.post("/pipeline/event/{event_key}")
def run_event_pipeline(
    event_key: str,
    force_analysis: bool = False,
    clone_event_calibration: bool = False,
    require_video: bool = True,
    require_calibration: bool = True,
    run_post_compute: bool = True,
    auto_calibrate_missing: bool = True,
    auto_calibration_overwrite_existing: bool = False,
    auto_calibration_refresh_video: bool = False,
    auto_calibration_sample_count: int = 18,
    auto_calibration_min_inliers: int = 4,
    auto_calibration_ransac_reproj_threshold_px: float = 3.0,
    auto_calibration_focus_time_sec: float | None = None,
    synergy_model_version: str = Query(default=SYNERGY_MODEL_VERSION, alias="model_version"),
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    db: Session = Depends(get_db),
):
    _ensure_analysis_write_enabled()
    if quality_threshold < 0.0 or quality_threshold > 1.0:
        raise HTTPException(status_code=400, detail="quality_threshold must be between 0 and 1")
    if auto_calibration_sample_count < 1 or auto_calibration_sample_count > 80:
        raise HTTPException(status_code=400, detail="auto_calibration_sample_count must be between 1 and 80")
    if auto_calibration_min_inliers < 4 or auto_calibration_min_inliers > 64:
        raise HTTPException(status_code=400, detail="auto_calibration_min_inliers must be between 4 and 64")
    if (
        auto_calibration_ransac_reproj_threshold_px <= 0.1
        or auto_calibration_ransac_reproj_threshold_px > 25.0
    ):
        raise HTTPException(
            status_code=400,
            detail="auto_calibration_ransac_reproj_threshold_px must be > 0.1 and <= 25.0",
        )
    if auto_calibration_focus_time_sec is not None and auto_calibration_focus_time_sec < 0:
        raise HTTPException(status_code=400, detail="auto_calibration_focus_time_sec must be >= 0")

    queue = get_queue()
    result = _run_event_pipeline(
        db,
        queue,
        event_key=event_key,
        force_analysis=force_analysis,
        allowed_team_keys=None,
        clone_event_calibration=clone_event_calibration,
        require_video=require_video,
        require_calibration=require_calibration,
        run_post_compute=run_post_compute,
        synergy_model_version=synergy_model_version,
        quality_threshold=quality_threshold,
        auto_calibrate_missing=auto_calibrate_missing,
        auto_calibration_overwrite_existing=auto_calibration_overwrite_existing,
        auto_calibration_refresh_video=auto_calibration_refresh_video,
        auto_calibration_sample_count=int(auto_calibration_sample_count),
        auto_calibration_min_inliers=int(auto_calibration_min_inliers),
        auto_calibration_ransac_reproj_threshold_px=float(auto_calibration_ransac_reproj_threshold_px),
        auto_calibration_focus_time_sec=auto_calibration_focus_time_sec,
    )
    return {
        "ok": True,
        "event_key": event_key,
        "analysis_version": ANALYSIS_VERSION,
        "result": result,
    }


@router.post("/automation/regional/season/{season}")
def automate_regional_post_event_breakdowns(
    season: int,
    force_analysis: bool = False,
    include_all_events: bool = False,
    include_out_of_region_events_for_in_region_teams: bool = True,
    include_ended_today: bool = False,
    allow_previous_season_fallback: bool | None = None,
    all_matches_in_region_events: bool = False,
    auto_calibrate_missing: bool = True,
    auto_calibration_overwrite_existing: bool = False,
    auto_calibration_refresh_video: bool = False,
    auto_calibration_sample_count: int = 18,
    auto_calibration_min_inliers: int = 4,
    auto_calibration_ransac_reproj_threshold_px: float = 3.0,
    auto_calibration_focus_time_sec: float | None = None,
    max_events: int = 300,
    max_teams: int = 1000,
    max_matches_per_event: int = 42,
    max_new_jobs_per_tick: int = 220,
    max_queue_pending_jobs: int | None = None,
    clone_event_calibration: bool = True,
    require_video: bool = True,
    require_calibration: bool = False,
    run_post_compute: bool = True,
    synergy_model_version: str = Query(default=SYNERGY_MODEL_VERSION, alias="model_version"),
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    db: Session = Depends(get_db),
):
    _ensure_analysis_write_enabled()
    try:
        return run_regional_post_event_breakdowns(
            season=season,
            force_analysis=force_analysis,
            include_all_events=include_all_events,
            include_out_of_region_events_for_in_region_teams=include_out_of_region_events_for_in_region_teams,
            include_ended_today=include_ended_today,
            allow_previous_season_fallback=allow_previous_season_fallback,
            all_matches_in_region_events=all_matches_in_region_events,
            auto_calibrate_missing=auto_calibrate_missing,
            auto_calibration_overwrite_existing=auto_calibration_overwrite_existing,
            auto_calibration_refresh_video=auto_calibration_refresh_video,
            auto_calibration_sample_count=auto_calibration_sample_count,
            auto_calibration_min_inliers=auto_calibration_min_inliers,
            auto_calibration_ransac_reproj_threshold_px=auto_calibration_ransac_reproj_threshold_px,
            auto_calibration_focus_time_sec=auto_calibration_focus_time_sec,
            max_events=max_events,
            max_teams=max_teams,
            max_matches_per_event=max_matches_per_event,
            max_new_jobs_per_tick=max_new_jobs_per_tick,
            max_queue_pending_jobs=max_queue_pending_jobs,
            clone_event_calibration=clone_event_calibration,
            require_video=require_video,
            require_calibration=require_calibration,
            run_post_compute=run_post_compute,
            synergy_model_version=synergy_model_version,
            quality_threshold=quality_threshold,
            db=db,
        )
    except RegionalAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/automation/regional/season/{season}/tick")
def automate_regional_post_event_breakdowns_tick(
    season: int,
    force_tick: bool = False,
    min_interval_minutes: int | None = None,
    force_analysis: bool = False,
    include_all_events: bool | None = None,
    include_out_of_region_events_for_in_region_teams: bool | None = None,
    include_ended_today: bool | None = None,
    allow_previous_season_fallback: bool | None = None,
    all_matches_in_region_events: bool | None = None,
    auto_calibrate_missing: bool | None = None,
    auto_calibration_overwrite_existing: bool | None = None,
    auto_calibration_refresh_video: bool | None = None,
    auto_calibration_sample_count: int | None = None,
    auto_calibration_min_inliers: int | None = None,
    auto_calibration_ransac_reproj_threshold_px: float | None = None,
    auto_calibration_focus_time_sec: float | None = None,
    max_events: int | None = None,
    max_teams: int | None = None,
    max_matches_per_event: int | None = None,
    max_new_jobs_per_tick: int | None = None,
    max_queue_pending_jobs: int | None = None,
    clone_event_calibration: bool | None = None,
    require_video: bool | None = None,
    require_calibration: bool | None = None,
    run_post_compute: bool | None = None,
    synergy_model_version: str = Query(default=SYNERGY_MODEL_VERSION, alias="model_version"),
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    db: Session = Depends(get_db),
):
    _ensure_analysis_write_enabled()
    try:
        return run_regional_automation_tick(
            season=season,
            force_tick=force_tick,
            min_interval_minutes=min_interval_minutes,
            force_analysis=force_analysis,
            include_all_events=include_all_events,
            include_out_of_region_events_for_in_region_teams=include_out_of_region_events_for_in_region_teams,
            include_ended_today=include_ended_today,
            allow_previous_season_fallback=allow_previous_season_fallback,
            all_matches_in_region_events=all_matches_in_region_events,
            auto_calibrate_missing=auto_calibrate_missing,
            auto_calibration_overwrite_existing=auto_calibration_overwrite_existing,
            auto_calibration_refresh_video=auto_calibration_refresh_video,
            auto_calibration_sample_count=auto_calibration_sample_count,
            auto_calibration_min_inliers=auto_calibration_min_inliers,
            auto_calibration_ransac_reproj_threshold_px=auto_calibration_ransac_reproj_threshold_px,
            auto_calibration_focus_time_sec=auto_calibration_focus_time_sec,
            max_events=max_events,
            max_teams=max_teams,
            max_matches_per_event=max_matches_per_event,
            max_new_jobs_per_tick=max_new_jobs_per_tick,
            max_queue_pending_jobs=max_queue_pending_jobs,
            clone_event_calibration=clone_event_calibration,
            require_video=require_video,
            require_calibration=require_calibration,
            run_post_compute=run_post_compute,
            synergy_model_version=synergy_model_version,
            quality_threshold=quality_threshold,
            db=db,
        )
    except RegionalAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/automation/regional/season/{season}/status")
def get_regional_automation_status(
    season: int,
    min_interval_minutes: int | None = None,
):
    if season < 2015 or season > 2100:
        raise HTTPException(status_code=400, detail="season must be between 2015 and 2100")

    interval_minutes = int(min_interval_minutes or settings.automation_regional_interval_minutes)
    interval_seconds = max(60, interval_minutes * 60)
    redis_conn = redis.from_url(settings.redis_url)
    last_run_key = _automation_redis_key("regional", season, "last_run_ts")
    last_result_key = _automation_redis_key("regional", season, "last_result")
    lock_key = _automation_redis_key("regional", season, "lock")

    now_ts = datetime.now(timezone.utc).timestamp()
    last_run_ts = _decode_redis_float(redis_conn.get(last_run_key))
    next_run_ts = (last_run_ts + interval_seconds) if last_run_ts is not None else None
    last_result_raw = redis_conn.get(last_result_key)
    last_result_payload = None
    if isinstance(last_result_raw, bytes):
        last_result_raw = last_result_raw.decode("utf-8", errors="ignore")
    if isinstance(last_result_raw, str) and last_result_raw.strip():
        try:
            parsed = json.loads(last_result_raw)
            if isinstance(parsed, dict):
                last_result_payload = parsed
        except json.JSONDecodeError:
            last_result_payload = {"raw": last_result_raw}

    lock_active = redis_conn.get(lock_key) is not None
    due_now = next_run_ts is None or now_ts >= next_run_ts
    return {
        "ok": True,
        "season": season,
        "enabled": settings.automation_regional_enabled,
        "interval_minutes": interval_minutes,
        "locked": lock_active,
        "last_run_at": (
            datetime.fromtimestamp(last_run_ts, tz=timezone.utc).isoformat()
            if last_run_ts is not None
            else None
        ),
        "next_run_earliest_at": (
            datetime.fromtimestamp(next_run_ts, tz=timezone.utc).isoformat()
            if next_run_ts is not None
            else None
        ),
        "due_now": due_now,
        "last_result": last_result_payload,
        "caps": {
            "include_all_events": bool(settings.automation_regional_include_all_events),
            "max_events": int(settings.automation_regional_max_events),
            "max_teams": int(settings.automation_regional_max_teams),
            "max_matches_per_event": int(settings.automation_regional_max_matches_per_event),
            "max_new_jobs_per_tick": int(settings.automation_regional_max_new_jobs_per_tick),
            "max_pending_jobs": int(settings.analysis_queue_max_pending_jobs),
        },
    }


@router.get("/queue/status")
def get_analysis_queue_status(
    limit: int = 30,
):
    bounded_limit = max(1, min(int(limit), 200))
    queue = get_queue()
    started_registry = StartedJobRegistry(queue=queue)
    deferred_registry = DeferredJobRegistry(queue=queue)
    scheduled_registry = ScheduledJobRegistry(queue=queue)
    failed_registry = FailedJobRegistry(queue=queue)

    try:
        queued_ids = list(queue.job_ids)[:bounded_limit]
        started_ids = started_registry.get_job_ids()[:bounded_limit]
        deferred_ids = deferred_registry.get_job_ids()[:bounded_limit]
        scheduled_ids = scheduled_registry.get_job_ids()[:bounded_limit]
        failed_ids = failed_registry.get_job_ids()[:bounded_limit]
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Queue backend unavailable: {sanitize_external_error(exc, default='queue_unavailable')}",
        ) from exc

    def _summary(job_id: str) -> dict[str, Any]:
        job = queue.fetch_job(job_id)
        return {
            "job_id": job_id,
            "status": (job.get_status() if job is not None else "unknown"),
            "enqueued_at": (
                job.enqueued_at.isoformat()
                if job is not None and getattr(job, "enqueued_at", None) is not None
                else None
            ),
            "last_heartbeat": (
                job.last_heartbeat.isoformat()
                if job is not None and getattr(job, "last_heartbeat", None) is not None
                else None
            ),
            "exc_info_present": bool(getattr(job, "exc_info", None)) if job is not None else False,
        }

    return {
        "ok": True,
        "queue_name": queue.name,
        "counts": {
            "queued": len(queued_ids),
            "started": len(started_ids),
            "deferred": len(deferred_ids),
            "scheduled": len(scheduled_ids),
            "failed": len(failed_ids),
        },
        "jobs": {
            "queued": [_summary(job_id) for job_id in queued_ids],
            "started": [_summary(job_id) for job_id in started_ids],
            "deferred": [_summary(job_id) for job_id in deferred_ids],
            "scheduled": [_summary(job_id) for job_id in scheduled_ids],
            "failed": [_summary(job_id) for job_id in failed_ids],
        },
        "retry_policy": {
            "max_retries": ANALYSIS_JOB_RETRY_MAX,
            "job_timeout_sec": ANALYSIS_JOB_TIMEOUT_SEC,
            "result_ttl_sec": ANALYSIS_JOB_RESULT_TTL_SEC,
            "failure_ttl_sec": ANALYSIS_JOB_FAILURE_TTL_SEC,
        },
    }


@router.post("/queue/failed/{job_id}/requeue")
def requeue_failed_analysis_job(
    job_id: str,
):
    _ensure_analysis_write_enabled()
    queue = get_queue()
    failed_registry = FailedJobRegistry(queue=queue)
    try:
        failed_ids = set(failed_registry.get_job_ids())
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Queue backend unavailable: {sanitize_external_error(exc, default='queue_unavailable')}",
        ) from exc
    if job_id not in failed_ids:
        raise HTTPException(status_code=404, detail=f"Failed job {job_id} not found in failed registry.")

    job = queue.fetch_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Failed job {job_id} metadata not found.")

    try:
        job.requeue()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to requeue failed job {job_id}: {sanitize_external_error(exc, default='requeue_failed')}",
        ) from exc

    return {
        "ok": True,
        "job_id": job_id,
        "status": "requeued",
    }


@router.get("/live/status")
def get_live_analysis_status():
    return live_monitor_status()


@router.get("/live/event/{event_key}/status")
def get_live_analysis_event_status(event_key: str):
    return live_monitor_status(event_key=event_key)


@router.post("/live/event/{event_key}/start")
def start_live_analysis_event_monitor(
    event_key: str,
    request: Request,
    interval_sec: int | None = Query(default=None),
    clip_duration_sec: int | None = Query(default=None),
    stream_url: str | None = Query(default=None),
    auto_stream_discovery: bool = Query(default=True),
):
    require_admin_access(request, "Live analysis monitor start")
    _ensure_analysis_write_enabled()
    return start_live_monitor(
        event_key=event_key,
        interval_sec=interval_sec,
        clip_duration_sec=clip_duration_sec,
        stream_url=stream_url,
        auto_stream_discovery=auto_stream_discovery,
    )


@router.post("/live/event/{event_key}/tick")
def run_live_analysis_event_tick(
    event_key: str,
    request: Request,
):
    require_admin_access(request, "Live analysis monitor tick")
    _ensure_analysis_write_enabled()
    return manual_live_monitor_tick(event_key)


@router.post("/live/event/{event_key}/stop")
def stop_live_analysis_event_monitor(
    event_key: str,
    request: Request,
):
    require_admin_access(request, "Live analysis monitor stop")
    _ensure_analysis_write_enabled()
    return stop_live_monitor(event_key)


@router.get("/live/automation/regional/status")
def get_live_regional_auto_manager_status():
    return regional_live_auto_manager_status()


@router.post("/live/automation/regional/tick")
def run_live_regional_auto_manager_tick_endpoint(
    request: Request,
    season: int | None = Query(default=None, ge=2015, le=2100),
):
    require_admin_access(request, "Regional live auto-manager tick")
    _ensure_analysis_write_enabled()
    return run_regional_live_auto_manager_tick(force=True, season=season)


@router.get("/{match_key}/findings")
def get_latest_match_findings(match_key: str, db: Session = Depends(get_db)):
    run = (
        db.query(models.AnalysisRun)
        .filter(models.AnalysisRun.match_key == match_key)
        .order_by(models.AnalysisRun.created_at.desc(), models.AnalysisRun.id.desc())
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="No analysis run found for match")

    findings = (
        db.query(models.TeamMatchFinding)
        .filter(models.TeamMatchFinding.analysis_run_id == run.id)
        .order_by(models.TeamMatchFinding.team_key.asc())
        .all()
    )
    artifacts = (
        db.query(models.Artifact)
        .filter(models.Artifact.analysis_run_id == run.id)
        .order_by(models.Artifact.id.asc())
        .all()
    )
    track_count = (
        db.query(models.RobotTrack)
        .filter(models.RobotTrack.analysis_run_id == run.id)
        .count()
    )
    event_count = (
        db.query(models.MatchEvent)
        .filter(models.MatchEvent.analysis_run_id == run.id)
        .count()
    )
    run_context = (
        db.query(models.AnalysisRunContext)
        .filter(models.AnalysisRunContext.run_id == run.id)
        .first()
    )
    run_quality = db.get(models.AnalysisQuality, run.id)

    return {
        "ok": True,
        "run": {
            "id": run.id,
            "match_key": run.match_key,
            "status": run.status,
            "version": run.version,
            "analysis_version": run_context.analysis_version if run_context else run.version,
            "params_hash": run_context.params_hash if run_context else None,
            "calibration_id": run_context.calibration_id if run_context else None,
            "created_at": run.created_at,
            "track_count": track_count,
            "event_count": event_count,
            "quality": {
                "calibration_quality_score": run_quality.calibration_quality_score if run_quality else None,
                "tracking_quality_score": run_quality.tracking_quality_score if run_quality else None,
                "identity_quality_score": run_quality.identity_quality_score if run_quality else None,
                "overall_quality_score": run_quality.overall_quality_score if run_quality else None,
            },
        },
        "artifacts": [
            {
                "id": artifact.id,
                "kind": artifact.kind,
                "path": artifact.path,
                "url": f"/media/{artifact.path.lstrip('/')}",
                "meta": artifact.meta,
            }
            for artifact in artifacts
        ],
        "findings": [
            {
                "team_key": finding.team_key,
                "event_key": finding.event_key,
                "alliance": finding.alliance,
                "station": finding.station,
                "source": finding.source,
                "fuel_scoring_rate": finding.fuel_scoring_rate,
                "cycle_time_sec": finding.cycle_time_sec,
                "auto_contribution": finding.auto_contribution,
                "climb_success_prob": finding.climb_success_prob,
                "defensive_engagement_sec": finding.defensive_engagement_sec,
                "reliability_score": finding.reliability_score,
                "summary": finding.summary,
            }
            for finding in findings
        ],
    }
