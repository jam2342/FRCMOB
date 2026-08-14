# Low-quality match reprocess service.
#
# Finds recent analysis runs that were rejected by the quality gate and
# enqueues them for reprocessing with denser sampling overrides.
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import redis
from rq import Queue
from rq.registry import DeferredJobRegistry, ScheduledJobRegistry, StartedJobRegistry
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models

logger = logging.getLogger(__name__)

DEFAULT_REASON_TOKENS: set[str] = {
    "detections_below_threshold",
    "average_coverage_below_threshold",
    "overall_quality_below_threshold",
}

def _parse_reason_tokens(raw: str) -> set[str]:
    return {token.strip().lower() for token in str(raw or "").split(",") if token.strip()}

def _compute_queue_pressure(queue: Queue) -> tuple[int, float]:
    # Return (pending_total, pressure_0_1) for the given queue.
    queued = len(queue.job_ids)
    started = len(StartedJobRegistry(queue=queue).get_job_ids())
    deferred = len(DeferredJobRegistry(queue=queue).get_job_ids())
    scheduled = len(ScheduledJobRegistry(queue=queue).get_job_ids())
    pending_total = int(queued + started + deferred + scheduled)
    pending_cap = max(10, int(settings.analysis_queue_max_pending_jobs))
    pressure = float(pending_total) / float(pending_cap)
    return pending_total, pressure

def reprocess_low_quality_matches(db: Session) -> dict[str, Any]:
    # Scan recent low-quality runs and enqueue reprocessing jobs.
    #
    # Returns a summary dict with counts and scheduled match details.
    from app.services.events.pipeline import ANALYSIS_VERSION, _enqueue_analysis_job, get_queue
    from app.services.jobs import build_analysis_params_hash
    from app.services.vision.perimeter_resolver import resolve_perimeter_type_for_event_profile

    redis_conn = redis.from_url(settings.redis_url)
    queue = get_queue()
    pending_total, pressure = _compute_queue_pressure(queue)
    pressure_ceiling = max(
        0.2, min(0.98, float(settings.analysis_low_quality_reprocess_queue_pressure_ceiling))
    )

    if pressure >= pressure_ceiling:
        logger.info(
            "Low-quality reprocess skipped queue_pressure=%.3f ceiling=%.3f pending=%s",
            pressure,
            pressure_ceiling,
            pending_total,
        )
        return {
            "status": "skipped_high_queue_pressure",
            "queue_pending": int(pending_total),
            "queue_pressure_0_1": round(float(pressure), 4),
            "pressure_ceiling_0_1": round(float(pressure_ceiling), 4),
        }

    now_utc = datetime.now(timezone.utc)
    lookback_hours = max(1, int(settings.analysis_low_quality_reprocess_lookback_hours))
    max_matches_per_run = max(1, min(40, int(settings.analysis_low_quality_reprocess_max_matches_per_run)))
    cooldown_hours = max(1, int(settings.analysis_low_quality_reprocess_cooldown_hours))
    reason_tokens = _parse_reason_tokens(settings.analysis_low_quality_reprocess_reason_tokens)
    if not reason_tokens:
        reason_tokens = set(DEFAULT_REASON_TOKENS)
    sampling_override = {
        "sample_interval_sec": float(settings.analysis_low_quality_reprocess_sample_interval_sec),
        "max_frames": int(settings.analysis_low_quality_reprocess_max_frames),
        "max_artifact_frames": int(settings.analysis_low_quality_reprocess_max_artifact_frames),
    }
    since = now_utc - timedelta(hours=lookback_hours)
    query_limit = max(50, max_matches_per_run * 50)

    candidate_rows = (
        db.query(
            models.AnalysisRun.id,
            models.AnalysisRun.match_key,
            models.AnalysisRun.status,
            models.AnalysisRunContext.calibration_id,
            models.Match.event_key,
            models.AnalysisQuality.details,
            models.AnalysisQuality.updated_at,
        )
        .join(models.AnalysisRunContext, models.AnalysisRunContext.run_id == models.AnalysisRun.id)
        .join(models.AnalysisQuality, models.AnalysisQuality.run_id == models.AnalysisRun.id)
        .join(models.Match, models.Match.match_key == models.AnalysisRun.match_key)
        .filter(models.AnalysisQuality.updated_at >= since)
        .order_by(models.AnalysisQuality.updated_at.desc(), models.AnalysisRun.id.desc())
        .limit(query_limit)
        .all()
    )

    scheduled_rows: list[dict[str, Any]] = []
    skipped_non_reason = 0
    skipped_no_calibration = 0
    skipped_cooldown = 0
    skipped_already_queued = 0
    seen_match_keys: set[str] = set()
    cooldown_sec = int(cooldown_hours * 60 * 60)

    for row in candidate_rows:
        (
            run_id,
            match_key,
            run_status,
            calibration_id,
            event_key,
            quality_details,
            updated_at,
        ) = row
        match_token = str(match_key or "").strip()
        if not match_token or match_token in seen_match_keys:
            continue
        seen_match_keys.add(match_token)

        if calibration_id is None:
            skipped_no_calibration += 1
            continue

        quality_payload = quality_details if isinstance(quality_details, dict) else {}
        quality_gate = (
            quality_payload.get("quality_gate")
            if isinstance(quality_payload.get("quality_gate"), dict)
            else {}
        )
        rejection_reasons_raw = quality_gate.get("rejection_reasons")
        rejection_reasons = (
            [str(reason).strip().lower() for reason in rejection_reasons_raw if str(reason).strip()]
            if isinstance(rejection_reasons_raw, list)
            else []
        )
        if not reason_tokens.intersection(set(rejection_reasons)):
            skipped_non_reason += 1
            continue

        cooldown_key = f"automation:reprocess:match:{match_token}:calibration:{int(calibration_id)}"
        if redis_conn.get(cooldown_key) is not None:
            skipped_cooldown += 1
            continue

        event_profile = db.get(models.EventProfile, event_key)
        perimeter_type, _ = resolve_perimeter_type_for_event_profile(event_profile)
        params_hash = build_analysis_params_hash(
            analysis_version=ANALYSIS_VERSION,
            calibration_id=int(calibration_id),
            perimeter_type=perimeter_type,
            sampling_overrides=sampling_override,
        )
        job, job_id, job_status = _enqueue_analysis_job(
            queue,
            match_key=match_token,
            analysis_version=ANALYSIS_VERSION,
            params_hash=params_hash,
            calibration_id=int(calibration_id),
            analyze_kwargs={"sampling_override": sampling_override},
        )
        if job is None:
            skipped_already_queued += 1
            continue
        redis_conn.setex(cooldown_key, cooldown_sec, now_utc.isoformat())
        scheduled_rows.append(
            {
                "match_key": match_token,
                "job_id": str(job_id),
                "job_status": str(job_status),
                "source_run_id": int(run_id),
                "source_run_status": str(run_status or ""),
                "source_updated_at": (
                    updated_at.isoformat() if isinstance(updated_at, datetime) else None
                ),
                "rejection_reasons": rejection_reasons,
            }
        )
        if len(scheduled_rows) >= max_matches_per_run:
            break

    logger.info(
        "Low-quality reprocess candidates=%s scheduled=%s "
        "skipped_non_reason=%s skipped_no_calibration=%s "
        "skipped_cooldown=%s skipped_already_queued=%s",
        len(candidate_rows),
        len(scheduled_rows),
        skipped_non_reason,
        skipped_no_calibration,
        skipped_cooldown,
        skipped_already_queued,
    )
    return {
        "status": "ran",
        "lookback_hours": int(lookback_hours),
        "candidate_count": int(len(candidate_rows)),
        "scheduled_count": int(len(scheduled_rows)),
        "scheduled_matches": scheduled_rows[:int(max_matches_per_run)],
        "skipped_non_reason": int(skipped_non_reason),
        "skipped_no_calibration": int(skipped_no_calibration),
        "skipped_cooldown": int(skipped_cooldown),
        "skipped_already_queued": int(skipped_already_queued),
        "queue_pending": int(pending_total),
        "queue_pressure_0_1": round(float(pressure), 4),
        "sampling_override": sampling_override,
        "reason_tokens": sorted(reason_tokens),
    }
