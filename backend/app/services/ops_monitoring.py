# Ops smoke-check and SLO monitoring service.
#
# Checks queue health, regional automation staleness, YouTube extraction
# health, and interlude-trim weak-signal fallback rates.  Emits warnings
# on threshold breaches.
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis
from rq import Queue
from rq.registry import DeferredJobRegistry, ScheduledJobRegistry, StartedJobRegistry

from app.core.config import settings
from app.db import models
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

_MIN_VALID_SEASON_YEAR = 2000  # FRC seasons began in 1992; values <= this indicate "not configured"

def _ratio(numerator: float, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator > 0 else 0.0

# ── Helpers ────────────────────────────────────────────────────────────────────

def _automation_redis_key(scope: str, season: int, key_type: str) -> str:
    return f"automation:{scope}:season:{season}:{key_type}"

def _decode_json_payload(raw: bytes | str | None) -> dict | None:
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
    if not text.strip():
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None

def _decode_redis_float(raw: bytes | str | None) -> float | None:
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
    token = text.strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        return None

def _current_regional_automation_season() -> int:
    configured = int(settings.automation_regional_halfday_season or 0)
    if configured > _MIN_VALID_SEASON_YEAR:
        return configured
    return datetime.now(timezone.utc).year

# ── YouTube extraction health ─────────────────────────────────────────────────

def youtube_extraction_health_snapshot(*, sample_runs: int) -> dict[str, Any]:
    # Compute YouTube extraction health metrics over recent analysis runs.
    bounded_sample = max(20, min(int(sample_runs), 2000))
    db = SessionLocal()
    try:
        run_rows = (
            db.query(models.AnalysisRun.id, models.AnalysisRun.status)
            .order_by(models.AnalysisRun.id.desc())
            .limit(bounded_sample)
            .all()
        )
        if not run_rows:
            return {
                "sample_runs": 0,
                "source_video_rows": 0,
                "stream_fallback_rows": 0,
                "failed_without_source_rows": 0,
                "stream_fallback_ratio_0_1": 0.0,
                "hard_extraction_failure_ratio_0_1": 0.0,
            }

        run_ids = [int(run_id) for run_id, _ in run_rows]
        source_rows = (
            db.query(models.Artifact.analysis_run_id, models.Artifact.meta)
            .filter(
                models.Artifact.kind == "source_video",
                models.Artifact.analysis_run_id.in_(run_ids),
            )
            .all()
        )
        sampling_rows = (
            db.query(models.Artifact.analysis_run_id, models.Artifact.meta)
            .filter(
                models.Artifact.kind == "sampling_manifest",
                models.Artifact.analysis_run_id.in_(run_ids),
            )
            .all()
        )
        source_meta_by_run: dict[int, dict] = {}
        sampling_meta_by_run: dict[int, dict] = {}
        for run_id, meta in source_rows:
            if not isinstance(meta, dict):
                continue
            key = int(run_id)
            if key not in source_meta_by_run:
                source_meta_by_run[key] = meta
        for run_id, meta in sampling_rows:
            if not isinstance(meta, dict):
                continue
            key = int(run_id)
            if key not in sampling_meta_by_run:
                sampling_meta_by_run[key] = meta

        source_video_rows = 0
        stream_fallback_rows = 0
        failed_without_source_rows = 0
        sampling_manifest_rows = 0
        interlude_trim_weak_signal_fallback_rows = 0

        for run_id, status in run_rows:
            run_key = int(run_id)
            source_meta = source_meta_by_run.get(run_key)
            sampling_meta = sampling_meta_by_run.get(run_key)
            if isinstance(source_meta, dict):
                source_video_rows += 1
                source_mode = str(source_meta.get("source_mode") or "").strip().lower()
                fallback_reason = str(source_meta.get("stream_fallback_reason") or "").strip()
                if source_mode == "downloaded_video" or bool(fallback_reason):
                    stream_fallback_rows += 1
            elif str(status or "").strip().lower() == "failed":
                failed_without_source_rows += 1
            if isinstance(sampling_meta, dict):
                sampling_manifest_rows += 1
                backend_meta = (
                    sampling_meta.get("tracking_backend_meta")
                    if isinstance(sampling_meta.get("tracking_backend_meta"), dict)
                    else {}
                )
                weak_signal_payload = (
                    backend_meta.get("interlude_trim_weak_signal_fallback")
                    if isinstance(backend_meta.get("interlude_trim_weak_signal_fallback"), dict)
                    else {}
                )
                if bool(weak_signal_payload.get("applied")):
                    interlude_trim_weak_signal_fallback_rows += 1

        fallback_ratio = _ratio(stream_fallback_rows, source_video_rows)
        hard_failure_ratio = _ratio(failed_without_source_rows, len(run_rows))
        weak_signal_ratio = _ratio(interlude_trim_weak_signal_fallback_rows, sampling_manifest_rows)
        return {
            "sample_runs": int(len(run_rows)),
            "source_video_rows": int(source_video_rows),
            "stream_fallback_rows": int(stream_fallback_rows),
            "failed_without_source_rows": int(failed_without_source_rows),
            "sampling_manifest_rows": int(sampling_manifest_rows),
            "interlude_trim_weak_signal_fallback_rows": int(
                interlude_trim_weak_signal_fallback_rows
            ),
            "stream_fallback_ratio_0_1": round(fallback_ratio, 4),
            "hard_extraction_failure_ratio_0_1": round(hard_failure_ratio, 4),
            "interlude_trim_weak_signal_fallback_ratio_0_1": round(weak_signal_ratio, 4),
        }
    finally:
        db.rollback()
        db.close()

# ── Main smoke check ──────────────────────────────────────────────────────────

def run_ops_smoke_check() -> dict[str, Any]:
    # Run the full ops smoke check and return a details dict.
    #
    # Emits warnings for any threshold breaches.  The returned dict is
    # suitable for recording via ``_record_job_runtime``.
    redis_conn = redis.from_url(
        settings.redis_url,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    queue = Queue("default", connection=redis_conn)

    # ── Queue pressure ────────────────────────────────────────────────
    pending = (
        len(queue.job_ids)
        + len(StartedJobRegistry(queue=queue).get_job_ids())
        + len(DeferredJobRegistry(queue=queue).get_job_ids())
        + len(ScheduledJobRegistry(queue=queue).get_job_ids())
    )
    pending_cap = max(10, int(settings.analysis_queue_max_pending_jobs))
    pressure = float(pending) / float(pending_cap)
    pressure_threshold = max(0.1, min(0.99, float(settings.ops_alert_queue_pressure_threshold)))

    # ── Regional automation staleness ─────────────────────────────────
    season = _current_regional_automation_season()
    last_run_key = _automation_redis_key("regional", season, "last_run_ts")
    last_result_key = _automation_redis_key("regional", season, "last_result")
    automation_lock_key = _automation_redis_key("regional", season, "lock")
    last_result = _decode_json_payload(redis_conn.get(last_result_key)) or {}
    blocked_counts = (
        last_result.get("blocked_reason_counts")
        if isinstance(last_result.get("blocked_reason_counts"), dict)
        else {}
    )
    totals = last_result.get("totals") if isinstance(last_result.get("totals"), dict) else {}
    missing_calibration_blocked = int(blocked_counts.get("missing_calibration") or 0)
    blocked_total = int(totals.get("blocked_matches") or 0)
    scheduled_total = int(totals.get("scheduled_matches") or 0)
    blocked_ratio = blocked_total / max(1, blocked_total + scheduled_total)
    now_ts = datetime.now(timezone.utc).timestamp()

    last_run_ts = _decode_redis_float(redis_conn.get(last_run_key))
    stale_hours_threshold = max(1, int(settings.ops_alert_regional_automation_stale_hours))
    regional_automation_stale = (last_run_ts is None) or (
        (now_ts - float(last_run_ts)) > float(stale_hours_threshold * 3600)
    )

    # ── Queue stuck detection ─────────────────────────────────────────
    queue_pending_count_key = "ops:queue:pending_count"
    queue_pending_change_key = "ops:queue:pending_last_change_ts"
    previous_pending = _decode_redis_float(redis_conn.get(queue_pending_count_key))
    previous_change_ts = _decode_redis_float(redis_conn.get(queue_pending_change_key))
    if pending <= 0:
        previous_change_ts = now_ts
    elif previous_pending is None or int(previous_pending) != int(pending):
        previous_change_ts = now_ts
    if previous_change_ts is None:
        previous_change_ts = now_ts
    redis_conn.set(queue_pending_count_key, str(int(pending)), ex=7 * 24 * 3600)
    redis_conn.set(queue_pending_change_key, str(float(previous_change_ts)), ex=7 * 24 * 3600)
    queue_stuck_minutes = max(0.0, (now_ts - float(previous_change_ts)) / 60.0)
    queue_stuck_threshold_minutes = max(5, int(settings.ops_alert_queue_stuck_minutes))
    queue_stuck = pending > 0 and queue_stuck_minutes >= float(queue_stuck_threshold_minutes)

    # ── Lock streak detection ─────────────────────────────────────────
    lock_active = redis_conn.get(automation_lock_key) is not None
    lock_streak_key = _automation_redis_key("regional", season, "ops_lock_streak")
    lock_streak_threshold = max(1, int(settings.ops_alert_automation_lock_spike_threshold))
    if lock_active:
        lock_streak_checks = int(redis_conn.incr(lock_streak_key))
        redis_conn.expire(lock_streak_key, 7 * 24 * 3600)
    else:
        redis_conn.delete(lock_streak_key)
        lock_streak_checks = 0
    lock_spike = lock_streak_checks >= lock_streak_threshold

    # ── YouTube / interlude health ────────────────────────────────────
    youtube_health = youtube_extraction_health_snapshot(
        sample_runs=int(settings.ops_youtube_slo_lookback_runs)
    )
    fallback_ratio = float(youtube_health.get("stream_fallback_ratio_0_1") or 0.0)
    hard_failure_ratio = float(youtube_health.get("hard_extraction_failure_ratio_0_1") or 0.0)
    fallback_ratio_threshold = max(
        0.0, min(1.0, float(settings.ops_alert_youtube_stream_fallback_ratio_threshold))
    )
    hard_failure_ratio_threshold = max(
        0.0, min(1.0, float(settings.ops_alert_youtube_hard_failure_ratio_threshold))
    )
    weak_signal_ratio = float(
        youtube_health.get("interlude_trim_weak_signal_fallback_ratio_0_1") or 0.0
    )
    weak_signal_ratio_threshold = max(
        0.0, min(1.0, float(settings.ops_alert_interlude_trim_weak_signal_ratio_threshold))
    )
    weak_signal_min_samples = max(
        5, int(settings.ops_alert_interlude_trim_weak_signal_min_samples)
    )
    fallback_ratio_high = (
        int(youtube_health.get("source_video_rows") or 0) >= 20
        and fallback_ratio >= fallback_ratio_threshold
    )
    hard_failure_ratio_high = (
        int(youtube_health.get("sample_runs") or 0) >= 20
        and hard_failure_ratio >= hard_failure_ratio_threshold
    )
    weak_signal_ratio_high = (
        int(youtube_health.get("sampling_manifest_rows") or 0) >= weak_signal_min_samples
        and weak_signal_ratio >= weak_signal_ratio_threshold
    )

    # ── Summary log ───────────────────────────────────────────────────
    logger.info(
        "Ops smoke check queue_pending=%s queue_cap=%s pressure=%.3f "
        "blocked=%s scheduled=%s missing_calibration=%s regional_stale=%s "
        "queue_stuck=%s lock_streak=%s yt_fallback_ratio=%.3f "
        "yt_hard_failure_ratio=%.3f trim_weak_signal_ratio=%.3f",
        pending,
        pending_cap,
        pressure,
        blocked_total,
        scheduled_total,
        missing_calibration_blocked,
        regional_automation_stale,
        queue_stuck,
        lock_streak_checks,
        fallback_ratio,
        hard_failure_ratio,
        weak_signal_ratio,
    )

    # ── Threshold breach warnings ─────────────────────────────────────
    if pressure >= pressure_threshold:
        logger.warning(
            "Ops smoke warning: queue pressure is high (%.3f >= %.3f)",
            pressure,
            pressure_threshold,
        )

    missing_threshold = max(
        1, int(settings.ops_alert_automation_missing_calibration_threshold)
    )
    blocked_ratio_threshold_val = max(
        0.05, min(0.95, float(settings.ops_alert_automation_blocked_ratio_threshold))
    )
    if missing_calibration_blocked >= missing_threshold:
        logger.warning(
            "Ops smoke warning: missing_calibration blocked=%s (threshold=%s)",
            missing_calibration_blocked,
            missing_threshold,
        )
    if blocked_total >= 10 and blocked_ratio >= blocked_ratio_threshold_val:
        logger.warning(
            "Ops smoke warning: blocked ratio high ratio=%.3f threshold=%.3f",
            blocked_ratio,
            blocked_ratio_threshold_val,
        )
    if regional_automation_stale:
        logger.warning(
            "Ops smoke warning: regional automation run is stale (threshold=%sh, last_run_ts=%s)",
            stale_hours_threshold,
            last_run_ts,
        )
    if queue_stuck:
        logger.warning(
            "Ops smoke warning: queue appears stuck pending=%s stuck_minutes=%.1f threshold=%s",
            pending,
            queue_stuck_minutes,
            queue_stuck_threshold_minutes,
        )
    if lock_spike:
        logger.warning(
            "Ops smoke warning: regional automation lock active too often streak_checks=%s threshold=%s",
            lock_streak_checks,
            lock_streak_threshold,
        )
    if fallback_ratio_high:
        logger.warning(
            "Ops smoke warning: youtube stream fallback ratio high ratio=%.3f threshold=%.3f source_rows=%s",
            fallback_ratio,
            fallback_ratio_threshold,
            youtube_health.get("source_video_rows"),
        )
    if hard_failure_ratio_high:
        logger.warning(
            "Ops smoke warning: youtube hard extraction failure ratio high ratio=%.3f threshold=%.3f sample_runs=%s",
            hard_failure_ratio,
            hard_failure_ratio_threshold,
            youtube_health.get("sample_runs"),
        )
    if weak_signal_ratio_high:
        logger.warning(
            "Ops smoke warning: interlude trim weak-signal fallback ratio high ratio=%.3f threshold=%.3f sample_rows=%s",
            weak_signal_ratio,
            weak_signal_ratio_threshold,
            youtube_health.get("sampling_manifest_rows"),
        )

    result = {
        "queue_pending": int(pending),
        "queue_pressure_0_1": round(float(pressure), 4),
        "queue_pressure_threshold_0_1": round(float(pressure_threshold), 4),
        "queue_stuck": bool(queue_stuck),
        "queue_stuck_minutes": round(float(queue_stuck_minutes), 2),
        "queue_stuck_threshold_minutes": int(queue_stuck_threshold_minutes),
        "blocked_matches": int(blocked_total),
        "missing_calibration_blocked": int(missing_calibration_blocked),
        "blocked_ratio_0_1": round(float(blocked_ratio), 4),
        "regional_automation_stale": bool(regional_automation_stale),
        "regional_automation_stale_threshold_hours": int(stale_hours_threshold),
        "regional_automation_last_run_ts": last_run_ts,
        "automation_lock_active": bool(lock_active),
        "automation_lock_streak_checks": int(lock_streak_checks),
        "automation_lock_streak_threshold": int(lock_streak_threshold),
        "youtube_extraction_health": youtube_health,
        "youtube_stream_fallback_ratio_threshold_0_1": round(
            float(fallback_ratio_threshold), 4
        ),
        "youtube_hard_failure_ratio_threshold_0_1": round(
            float(hard_failure_ratio_threshold), 4
        ),
        "interlude_trim_weak_signal_ratio_threshold_0_1": round(
            float(weak_signal_ratio_threshold), 4
        ),
        "interlude_trim_weak_signal_min_samples": int(weak_signal_min_samples),
    }
    redis_conn.close()
    return result
