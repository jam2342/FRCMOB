import contextlib
import json
import logging
from datetime import datetime, timezone
import time
from typing import Any, Generator
import uuid

from apscheduler.jobstores.base import ConflictingIdError
from apscheduler.schedulers.background import BackgroundScheduler
import redis

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.auto_scout.backfill import backfill_missing_auto_scout_drafts
from app.services.auto_scout.training import export_auto_scout_training_snapshots
from app.services.climb.official_backfill import run_official_climb_backfill
from app.services.climb.integrity import safe_run_climb_integrity_audit
from app.services.freshness_recovery import recover_stale_events
from app.services.intel.snapshots import refresh_hot_intel_snapshots
from app.services.analysis.live_monitor import run_regional_live_auto_manager_tick
from app.services.push.match_alerts import run_match_alert_tick
from app.services.push.sender import push_configured
from app.services.scouting_rooms.maintenance import cleanup_inactive_scouting_rooms
from app.services.media.storage_cleanup import cleanup_old_media

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_JOB_RUNTIME_STATE_FALLBACK: dict[str, dict[str, Any]] = {}
_metrics_redis_client: redis.Redis | None = None

def _to_json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized[str(key)] = _to_json_safe_value(item)
        return normalized
    if isinstance(value, (list, tuple, set)):
        return [_to_json_safe_value(item) for item in value]
    return str(value)

def _runtime_metrics_hash_key() -> str:
    prefix = str(settings.scheduler.runtime_metrics_prefix or "scheduler:runtime:").strip()
    if not prefix.endswith(":"):
        prefix = f"{prefix}:"
    return f"{prefix}jobs"

def _runtime_metrics_ttl_sec() -> int:
    return max(300, int(settings.scheduler.runtime_metrics_ttl_sec or 604800))

def _runtime_metrics_redis() -> redis.Redis | None:
    global _metrics_redis_client
    if _metrics_redis_client is not None:
        return _metrics_redis_client
    try:
        _metrics_redis_client = redis.from_url(settings.redis_url)
        return _metrics_redis_client
    except (redis.RedisError, ValueError) as exc:
        logger.warning("scheduler runtime metrics redis unavailable: %s", exc)
        return None

def _load_job_runtime_state(job_key: str) -> dict[str, Any]:
    conn = _runtime_metrics_redis()
    if conn is None:
        return dict(_JOB_RUNTIME_STATE_FALLBACK.get(job_key) or {})
    try:
        payload = conn.hget(_runtime_metrics_hash_key(), job_key)
    except redis.RedisError as exc:
        logger.warning("scheduler runtime metrics read failed job=%s error=%s", job_key, exc)
        return dict(_JOB_RUNTIME_STATE_FALLBACK.get(job_key) or {})
    if not payload:
        return dict(_JOB_RUNTIME_STATE_FALLBACK.get(job_key) or {})
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        loaded = json.loads(str(payload))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return dict(_JOB_RUNTIME_STATE_FALLBACK.get(job_key) or {})
    return dict(loaded) if isinstance(loaded, dict) else {}

def _persist_job_runtime_state(job_key: str, state: dict[str, Any]) -> None:
    safe_state = _to_json_safe_value(state)
    if not isinstance(safe_state, dict):
        safe_state = {"value": safe_state}
    _JOB_RUNTIME_STATE_FALLBACK[job_key] = dict(safe_state)
    conn = _runtime_metrics_redis()
    if conn is None:
        return
    try:
        payload = json.dumps(safe_state, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "scheduler runtime metrics serialization failed job=%s error=%s",
            job_key,
            exc,
        )
        return
    try:
        conn.hset(_runtime_metrics_hash_key(), job_key, payload)
        conn.expire(_runtime_metrics_hash_key(), _runtime_metrics_ttl_sec())
    except redis.RedisError as exc:
        logger.warning("scheduler runtime metrics write failed job=%s error=%s", job_key, exc)

def _load_all_runtime_states() -> dict[str, dict[str, Any]]:
    conn = _runtime_metrics_redis()
    if conn is None:
        return {key: dict(value) for key, value in _JOB_RUNTIME_STATE_FALLBACK.items()}
    try:
        raw_map = conn.hgetall(_runtime_metrics_hash_key())
    except redis.RedisError as exc:
        logger.warning("scheduler runtime metrics bulk read failed: %s", exc)
        return {key: dict(value) for key, value in _JOB_RUNTIME_STATE_FALLBACK.items()}

    loaded: dict[str, dict[str, Any]] = {}
    for raw_key, raw_value in raw_map.items():
        try:
            key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
            value_text = raw_value.decode("utf-8") if isinstance(raw_value, bytes) else str(raw_value)
            state = json.loads(value_text)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(state, dict):
            loaded[key] = dict(state)
    if not loaded:
        return {key: dict(value) for key, value in _JOB_RUNTIME_STATE_FALLBACK.items()}
    return loaded

def _record_job_runtime(
    job_key: str,
    *,
    started_perf: float,
    ok: bool,
    details: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    duration_sec = max(0.0, time.perf_counter() - started_perf)
    state = _load_job_runtime_state(job_key) or {
        "runs_total": 0,
        "runs_ok": 0,
        "runs_failed": 0,
        "avg_duration_sec": None,
    }
    runs_total = int(state.get("runs_total") or 0) + 1
    runs_ok = int(state.get("runs_ok") or 0) + (1 if ok else 0)
    runs_failed = int(state.get("runs_failed") or 0) + (0 if ok else 1)
    prior_avg = state.get("avg_duration_sec")
    if isinstance(prior_avg, (int, float)):
        avg_duration = ((float(prior_avg) * (runs_total - 1)) + duration_sec) / float(runs_total)
    else:
        avg_duration = duration_sec
    state.update(
        {
            "job_key": job_key,
            "runs_total": runs_total,
            "runs_ok": runs_ok,
            "runs_failed": runs_failed,
            "avg_duration_sec": round(avg_duration, 4),
            "last_duration_sec": round(duration_sec, 4),
            "last_run_at": now.isoformat(),
            "last_status": "ok" if ok else "failed",
            "last_error": str(error or "").strip() if not ok else None,
            "last_details": details or {},
        }
    )
    _persist_job_runtime_state(job_key, state)

def get_scheduler_runtime_metrics() -> dict[str, Any]:
    runtime_states = _load_all_runtime_states()
    return {
        "jobs": {key: dict(value) for key, value in runtime_states.items()},
        "job_count": len(runtime_states),
    }

def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)
    return _scheduler

def _distributed_lock_key(job_key: str) -> str:
    prefix = str(settings.scheduler.distributed_lock_prefix or "scheduler:lock:").strip()
    if not prefix.endswith(":"):
        prefix = f"{prefix}:"
    return f"{prefix}{job_key}"

def _distributed_lock_ttl(resolved_ttl_sec: int | None = None) -> int:
    default_ttl = max(60, int(settings.scheduler.distributed_lock_ttl_sec or 900))
    if resolved_ttl_sec is None:
        return default_ttl
    return max(60, int(resolved_ttl_sec))

def _acquire_distributed_job_lock(
    job_key: str,
    *,
    ttl_sec: int | None = None,
) -> tuple[redis.Redis | None, str | None, str | None]:
    if not bool(settings.scheduler.distributed_lock_enabled):
        return None, None, None

    lock_key = _distributed_lock_key(job_key)
    token = uuid.uuid4().hex
    resolved_ttl = _distributed_lock_ttl(ttl_sec)
    try:
        redis_conn = redis.from_url(settings.redis_url)
        acquired = bool(redis_conn.set(lock_key, token, nx=True, ex=resolved_ttl))
    except (redis.RedisError, ValueError, TypeError) as exc:
        # Degrade gracefully if Redis is unavailable.
        logger.warning(
            "scheduler lock degraded job=%s reason=redis_unavailable error=%s",
            job_key,
            exc,
        )
        return None, None, None
    if not acquired:
        return redis_conn, lock_key, None
    return redis_conn, lock_key, token

def _release_distributed_job_lock(
    redis_conn: redis.Redis | None,
    lock_key: str | None,
    token: str | None,
) -> None:
    if redis_conn is None or not lock_key or not token:
        return
    try:
        current = redis_conn.get(lock_key)
        current_token = (
            current.decode("utf-8", errors="ignore")
            if isinstance(current, bytes)
            else str(current or "")
        )
        if current_token == token:
            redis_conn.delete(lock_key)
    except redis.RedisError:
        logger.debug("scheduler lock release skipped key=%s", lock_key, exc_info=True)

@contextlib.contextmanager
def _scheduled_job(
    job_key: str,
    *,
    lock_ttl_sec: int | None = None,
    use_db: bool = False,
) -> Generator[dict[str, Any] | None, None, None]:
    # Context manager that wraps the lock/record/log boilerplate for scheduled jobs.
    #
    # Yields a mutable ``details`` dict.  The caller populates it with
    # job-specific metrics; upon exit the dict is forwarded to
    # ``_record_job_runtime``.
    #
    # If ``use_db`` is True a ``SessionLocal`` session is created, stored in
    # ``details["_db"]``, and cleaned up on exit.
    started_perf = time.perf_counter()
    lock_conn, lock_key, lock_token = _acquire_distributed_job_lock(
        job_key, ttl_sec=lock_ttl_sec,
    )
    if lock_key and not lock_token:
        _record_job_runtime(
            job_key, started_perf=started_perf, ok=True,
            details={"status": "skipped_distributed_lock"},
        )
        logger.info("%s skipped due to active distributed lock", job_key)
        yield None
        return

    details: dict[str, Any] = {}
    db = None
    if use_db:
        db = SessionLocal()
        details["_db"] = db
    try:
        yield details
        _record_job_runtime(job_key, started_perf=started_perf, ok=True, details={
            k: v for k, v in details.items() if not k.startswith("_")
        })
    except Exception as exc:
        _record_job_runtime(job_key, started_perf=started_perf, ok=False, error=str(exc))
        logger.error("Scheduled %s failed: %s", job_key, exc, exc_info=True)
    finally:
        if db is not None:
            db.rollback()
            db.close()
        _release_distributed_job_lock(lock_conn, lock_key, lock_token)

def start_scheduler() -> None:
    scheduler = get_scheduler()
    registration_failures: list[str] = []
    registered_job_ids: list[str] = []

    def _register_job(
        *,
        job_fn,
        trigger: str,
        job_id: str,
        job_name: str,
        success_log: str,
        **kwargs: Any,
    ) -> None:
        try:
            scheduler.add_job(
                job_fn,
                trigger,
                id=job_id,
                name=job_name,
                max_instances=1,
                coalesce=True,
                **kwargs,
            )
            registered_job_ids.append(job_id)
            logger.info(success_log)
        except (ConflictingIdError, ValueError, TypeError, RuntimeError) as exc:
            registration_failures.append(f"{job_id}: {exc}")
            logger.error("Failed to schedule job %s (%s): %s", job_name, job_id, exc)

    try:
        scheduler.remove_all_jobs()
    except (LookupError, ValueError, RuntimeError) as exc:
        registration_failures.append(f"remove_all_jobs: {exc}")
        logger.error("Failed to clear existing scheduler jobs: %s", exc)

    if settings.storage_cleanup_enabled and settings.storage_cleanup_post_analysis:
        _register_job(
            job_fn=_scheduled_cleanup_old_media,
            trigger="interval",
            job_id="cleanup_old_media",
            job_name="Cleanup old media (>N days)",
            success_log=f"Scheduled cleanup job added (threshold: {settings.storage_cleanup_age_days_auto} days)",
            hours=24,
            misfire_grace_time=3600,
        )

    if settings.scouting_rooms_cleanup_enabled:
        interval_minutes = max(15, int(settings.scouting_rooms_cleanup_interval_minutes))
        _register_job(
            job_fn=_scheduled_cleanup_inactive_scouting_rooms,
            trigger="interval",
            job_id="cleanup_inactive_scouting_rooms",
            job_name="Cleanup inactive scouting rooms",
            success_log=(
                "Scheduled scouting room cleanup job added "
                f"(interval: {interval_minutes} min, inactive_days={int(settings.scouting_rooms_inactive_delete_days)})"
            ),
            minutes=interval_minutes,
            misfire_grace_time=max(120, interval_minutes * 60),
        )

    if settings.push_notifications_enabled and push_configured():
        interval_sec = max(30, int(settings.push_match_alert_interval_sec))
        _register_job(
            job_fn=_scheduled_push_match_alerts,
            trigger="interval",
            job_id="push_match_alerts",
            job_name="Send push match alerts",
            success_log=f"Scheduled push match alert job added (interval: {interval_sec} sec)",
            seconds=interval_sec,
            misfire_grace_time=max(30, interval_sec),
        )

    if settings.intel_snapshot_refresh_enabled:
        interval_minutes = max(1, int(settings.intel_snapshot_refresh_interval_minutes))
        _register_job(
            job_fn=_scheduled_refresh_intel_snapshots,
            trigger="interval",
            job_id="refresh_intel_snapshots",
            job_name="Refresh team/event intel snapshots",
            success_log=f"Scheduled intel snapshot refresh job added (interval: {interval_minutes} min)",
            minutes=interval_minutes,
            misfire_grace_time=300,
        )

    if settings.freshness_recovery_enabled:
        interval_minutes = max(5, int(settings.freshness_recovery_interval_minutes))
        _register_job(
            job_fn=_scheduled_freshness_recovery,
            trigger="interval",
            job_id="freshness_recovery",
            job_name="Recover stale/missing analysis coverage",
            success_log=f"Scheduled freshness recovery job added (interval: {interval_minutes} min)",
            minutes=interval_minutes,
            misfire_grace_time=300,
        )

    if settings.climb_integrity_audit_enabled:
        interval_minutes = max(30, int(settings.climb_integrity_audit_interval_minutes))
        _register_job(
            job_fn=_scheduled_climb_integrity_audit,
            trigger="interval",
            job_id="climb_integrity_audit",
            job_name="Climb integrity audit",
            success_log=f"Scheduled climb integrity audit job added (interval: {interval_minutes} min)",
            minutes=interval_minutes,
            misfire_grace_time=max(120, interval_minutes * 60),
        )

    if settings.climb_official_backfill_enabled:
        interval_minutes = max(30, int(settings.climb_official_backfill_interval_minutes))
        _register_job(
            job_fn=_scheduled_climb_official_backfill,
            trigger="interval",
            job_id="climb_official_backfill",
            job_name="Backfill official climb/endgame fields",
            success_log=f"Scheduled climb official backfill job added (interval: {interval_minutes} min)",
            minutes=interval_minutes,
            misfire_grace_time=max(120, interval_minutes * 60),
        )

    if settings.automation_regional_enabled and settings.automation_regional_halfday_scheduler_enabled:
        interval_hours = max(1, int(settings.automation_regional_halfday_interval_hours))
        _register_job(
            job_fn=_scheduled_regional_post_event_breakdowns,
            trigger="interval",
            job_id="regional_post_event_halfday",
            job_name="Regional post-event automation (half-day)",
            success_log=f"Scheduled regional post-event automation job added (interval: {interval_hours} hours)",
            hours=interval_hours,
            misfire_grace_time=max(300, interval_hours * 60 * 60),
        )

    if settings.live_analysis_enabled and settings.live_analysis_regional_auto_enabled:
        interval_sec = max(30, int(settings.live_analysis_regional_auto_interval_sec))
        _register_job(
            job_fn=_scheduled_live_regional_auto_manager,
            trigger="interval",
            job_id="live_regional_auto_manager",
            job_name="Auto-manage regional live stream monitors",
            success_log=f"Scheduled regional live auto-manager job added (interval: {interval_sec} sec)",
            seconds=interval_sec,
            misfire_grace_time=max(30, interval_sec),
            next_run_time=datetime.now(timezone.utc),
        )

    if settings.analysis_low_quality_reprocess_enabled:
        interval_minutes = max(15, int(settings.analysis_low_quality_reprocess_interval_minutes))
        _register_job(
            job_fn=_scheduled_low_quality_reprocess,
            trigger="interval",
            job_id="low_quality_reprocess",
            job_name="Reprocess low-quality matches with denser sampling",
            success_log=f"Scheduled low-quality reprocess job added (interval: {interval_minutes} min)",
            minutes=interval_minutes,
            misfire_grace_time=max(120, interval_minutes * 60),
        )

    if settings.ops_smoke_check_enabled:
        interval_minutes = max(5, int(settings.ops_smoke_check_interval_minutes))
        _register_job(
            job_fn=_scheduled_ops_smoke_check,
            trigger="interval",
            job_id="ops_smoke_check",
            job_name="Ops smoke check (queue + automation health)",
            success_log=f"Scheduled ops smoke check job added (interval: {interval_minutes} min)",
            minutes=interval_minutes,
            misfire_grace_time=max(120, interval_minutes * 60),
        )

    if settings.auto_scout_backfill_enabled:
        interval_minutes = max(5, int(settings.auto_scout_backfill_interval_minutes))
        _register_job(
            job_fn=_scheduled_auto_scout_draft_backfill,
            trigger="interval",
            job_id="auto_scout_draft_backfill",
            job_name="Backfill missing auto-scout drafts for completed analyses",
            success_log=f"Scheduled auto-scout draft backfill job added (interval: {interval_minutes} min)",
            minutes=interval_minutes,
            misfire_grace_time=max(120, interval_minutes * 60),
        )

    if settings.ml_auto_scout_training_export_enabled:
        interval_hours = max(6, int(settings.ml_auto_scout_training_export_interval_hours))
        _register_job(
            job_fn=_scheduled_export_auto_scout_training_data,
            trigger="interval",
            job_id="auto_scout_training_export",
            job_name="Export auto-scout approved labels to ML snapshots",
            success_log=f"Scheduled auto-scout training export job added (interval: {interval_hours} hours)",
            hours=interval_hours,
            misfire_grace_time=max(300, interval_hours * 3600),
        )

    fail_startup = bool(settings.is_production_like) or bool(settings.strict_startup_env_validation)
    if registration_failures:
        joined = "; ".join(registration_failures)
        message = f"Scheduler job registration failures: {joined}"
        if fail_startup:
            raise RuntimeError(message)
        logger.warning(message)

    if not registered_job_ids:
        logger.info("All scheduler jobs disabled, scheduler not started")
        return

    if not scheduler.running:
        try:
            scheduler.start()
            logger.info("Background scheduler started successfully")
        except (RuntimeError, ValueError, OSError) as exc:
            logger.error("Failed to start scheduler: %s", exc)
            if fail_startup:
                raise

def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("Background scheduler stopped")
        except (RuntimeError, ValueError, OSError) as exc:
            logger.error("Error stopping scheduler: %s", exc)
        finally:
            _scheduler = None

def _scheduled_cleanup_old_media() -> None:
    with _scheduled_job("cleanup_old_media", lock_ttl_sec=4 * 60 * 60) as details:
        if details is None:
            return
        logger.info("Starting scheduled cleanup of media older than %s days", settings.storage_cleanup_age_days_auto)
        result = cleanup_old_media(older_than_days=settings.storage_cleanup_age_days_auto, aggressive=True)
        if result.get("ok"):
            logger.info("Scheduled cleanup completed: freed %s GB, deleted %s files",
                        result.get("total_deleted_gb", 0), result.get("total_deleted_files", 0))
        else:
            logger.warning("Scheduled cleanup encountered issues: %s", result.get("reason", "unknown"))
        details.update(
            deleted_files=int(result.get("total_deleted_files") or 0),
            deleted_gb=float(result.get("total_deleted_gb") or 0.0),
            reason=result.get("reason"),
        )

def _scheduled_cleanup_inactive_scouting_rooms() -> None:
    interval_minutes = max(15, int(settings.scouting_rooms_cleanup_interval_minutes))
    with _scheduled_job("cleanup_inactive_scouting_rooms", lock_ttl_sec=max(120, interval_minutes * 120)) as details:
        if details is None:
            return
        result = cleanup_inactive_scouting_rooms()
        logger.info(
            "Scouting room cleanup completed deleted_rooms=%s deleted_entries=%s "
            "deleted_assignments=%s deleted_leaders=%s cutoff=%s",
            int(result.get("deleted_rooms") or 0), int(result.get("deleted_entries") or 0),
            int(result.get("deleted_assignments") or 0), int(result.get("deleted_leaders") or 0),
            result.get("cutoff"),
        )
        details.update(
            deleted_rooms=int(result.get("deleted_rooms") or 0),
            deleted_entries=int(result.get("deleted_entries") or 0),
            deleted_assignments=int(result.get("deleted_assignments") or 0),
            deleted_leaders=int(result.get("deleted_leaders") or 0),
            cutoff=result.get("cutoff"),
        )

def _scheduled_push_match_alerts() -> None:
    interval_sec = max(30, int(settings.push_match_alert_interval_sec))
    with _scheduled_job(
        "push_match_alerts",
        lock_ttl_sec=max(60, interval_sec * 3),
        use_db=True,
    ) as details:
        if details is None:
            return
        db = details["_db"]
        result = run_match_alert_tick(db)
        details.update(
            subscriptions=int(result.get("subscriptions") or 0),
            sent=int(result.get("sent") or 0),
            skipped=result.get("skipped"),
        )

def _scheduled_refresh_intel_snapshots() -> None:
    interval_minutes = max(1, int(settings.intel_snapshot_refresh_interval_minutes))
    with _scheduled_job("refresh_intel_snapshots", lock_ttl_sec=max(120, interval_minutes * 120)) as details:
        if details is None:
            return
        logger.info("Starting scheduled intel snapshot refresh")
        result = refresh_hot_intel_snapshots()
        logger.info(
            "Intel snapshot refresh completed events=%s event_snapshots=%s team_snapshots=%s "
            "runtime_sec=%s budget_sec=%s timed_out=%s errors=%s",
            result.get("processed_events"), result.get("event_snapshots_written"),
            result.get("team_snapshots_written"), result.get("runtime_sec"),
            result.get("runtime_budget_sec"), result.get("timed_out"),
            len(result.get("errors") or []),
        )
        details.update(
            processed_events=int(result.get("processed_events") or 0),
            event_snapshots_written=int(result.get("event_snapshots_written") or 0),
            team_snapshots_written=int(result.get("team_snapshots_written") or 0),
            runtime_sec=result.get("runtime_sec"),
            timed_out=bool(result.get("timed_out")),
            error_count=len(result.get("errors") or []),
        )

def _scheduled_freshness_recovery() -> None:
    interval_minutes = max(5, int(settings.freshness_recovery_interval_minutes))
    with _scheduled_job("freshness_recovery", lock_ttl_sec=max(180, interval_minutes * 120), use_db=True) as details:
        if details is None:
            return
        db = details["_db"]
        logger.info("Starting scheduled freshness recovery")
        result = recover_stale_events(
            db,
            stale_hours_threshold=int(settings.freshness_sla_stale_hours),
            max_events=int(settings.freshness_recovery_max_events_per_run),
            max_target_teams_per_event=int(settings.freshness_recovery_max_target_teams_per_event),
            force_analysis=bool(settings.freshness_recovery_force_analysis),
            require_video=bool(settings.freshness_recovery_require_video),
            require_calibration=bool(settings.freshness_recovery_require_calibration),
            run_post_compute=bool(settings.freshness_recovery_run_post_compute),
        )
        totals = result.get("totals") if isinstance(result, dict) else {}
        logger.info(
            "Freshness recovery completed events=%s targeted_teams=%s scheduled=%s skipped=%s blocked=%s",
            result.get("processed_event_count") if isinstance(result, dict) else None,
            totals.get("targeted_teams") if isinstance(totals, dict) else None,
            totals.get("scheduled_matches") if isinstance(totals, dict) else None,
            totals.get("skipped_matches") if isinstance(totals, dict) else None,
            totals.get("blocked_matches") if isinstance(totals, dict) else None,
        )
        try:
            snapshot_refresh = refresh_hot_intel_snapshots()
            logger.info(
                "Freshness recovery intel refresh events=%s team_snapshots=%s backfilled_fields=%s errors=%s",
                snapshot_refresh.get("processed_events"), snapshot_refresh.get("team_snapshots_written"),
                snapshot_refresh.get("missing_fields_backfilled"), len(snapshot_refresh.get("errors") or []),
            )
        except (RuntimeError, ValueError, TypeError, OSError) as snapshot_exc:
            logger.error("Freshness recovery intel refresh failed: %s", snapshot_exc, exc_info=True)
        details.update(
            processed_event_count=int(result.get("processed_event_count") or 0) if isinstance(result, dict) else 0,
            targeted_teams=int(totals.get("targeted_teams") or 0) if isinstance(totals, dict) else 0,
            scheduled_matches=int(totals.get("scheduled_matches") or 0) if isinstance(totals, dict) else 0,
            blocked_matches=int(totals.get("blocked_matches") or 0) if isinstance(totals, dict) else 0,
        )

def _scheduled_climb_integrity_audit() -> None:
    interval_minutes = max(30, int(settings.climb_integrity_audit_interval_minutes))
    with _scheduled_job("climb_integrity_audit", lock_ttl_sec=max(300, interval_minutes * 120), use_db=True) as details:
        if details is None:
            return
        db = details["_db"]
        logger.info("Starting scheduled climb integrity audit")
        result = safe_run_climb_integrity_audit(
            db,
            lookback_days=int(settings.climb_integrity_audit_lookback_days),
            sample_limit=int(settings.climb_integrity_audit_sample_limit),
            diff_threshold=float(settings.climb_integrity_audit_diff_threshold),
        )
        totals = result.get("totals") if isinstance(result, dict) else {}
        logger.info(
            "Climb integrity audit ok=%s compared=%s mismatched=%s mismatch_rate=%s severity=%s",
            bool(result.get("ok")) if isinstance(result, dict) else False,
            totals.get("compared_pairs") if isinstance(totals, dict) else None,
            totals.get("mismatched_pairs") if isinstance(totals, dict) else None,
            totals.get("mismatch_rate") if isinstance(totals, dict) else None,
            result.get("severity") if isinstance(result, dict) else None,
        )
        details.update(
            severity=result.get("severity") if isinstance(result, dict) else None,
            compared_pairs=totals.get("compared_pairs") if isinstance(totals, dict) else None,
            mismatched_pairs=totals.get("mismatched_pairs") if isinstance(totals, dict) else None,
            mismatch_rate=totals.get("mismatch_rate") if isinstance(totals, dict) else None,
        )

def _scheduled_climb_official_backfill() -> None:
    interval_minutes = max(30, int(settings.climb_official_backfill_interval_minutes))
    with _scheduled_job("climb_official_backfill", lock_ttl_sec=max(300, interval_minutes * 120), use_db=True) as details:
        if details is None:
            return
        db = details["_db"]
        logger.info("Starting scheduled climb official backfill")
        result = run_official_climb_backfill(
            db, event_key=None, max_events=int(settings.climb_official_backfill_max_events_per_run),
        )
        totals = result.get("totals") if isinstance(result, dict) else {}
        logger.info(
            "Climb official backfill completed events=%s official_rows=%s updated=%s inserted=%s errors=%s",
            result.get("event_count") if isinstance(result, dict) else None,
            totals.get("official_rows") if isinstance(totals, dict) else None,
            totals.get("updated_findings") if isinstance(totals, dict) else None,
            totals.get("inserted_findings") if isinstance(totals, dict) else None,
            totals.get("errored_events") if isinstance(totals, dict) else None,
        )
        details.update(
            event_count=result.get("event_count") if isinstance(result, dict) else None,
            totals=totals if isinstance(totals, dict) else {},
        )

def _current_regional_automation_season() -> int:
    configured = int(settings.automation_regional_halfday_season or 0)
    if 2015 <= configured <= 2100:
        return configured
    return int(datetime.now(timezone.utc).year)

def _scheduled_regional_post_event_breakdowns() -> None:
    interval_hours = max(1, int(settings.automation_regional_halfday_interval_hours))
    with _scheduled_job("regional_post_event_halfday", lock_ttl_sec=max(600, interval_hours * 2 * 3600), use_db=True) as details:
        if details is None:
            return
        db = details["_db"]
        from app.services.events.regional_automation import run_regional_automation_tick
        from app.services.ml.synergy import QUALITY_THRESHOLD_DEFAULT, SYNERGY_MODEL_VERSION

        season = _current_regional_automation_season()
        result = run_regional_automation_tick(
            season=season, db=db, force_tick=False,
            min_interval_minutes=interval_hours * 60, force_analysis=False,
            include_out_of_region_events_for_in_region_teams=bool(settings.automation_regional_halfday_include_out_of_region_events),
            include_ended_today=bool(settings.automation_regional_halfday_include_ended_today),
            all_matches_in_region_events=bool(settings.automation_regional_halfday_all_matches_in_region_events),
            max_events=int(settings.automation_regional_max_events),
            max_teams=int(settings.automation_regional_max_teams),
            clone_event_calibration=bool(settings.automation_regional_clone_event_calibration),
            require_video=bool(settings.automation_regional_require_video),
            require_calibration=bool(settings.automation_regional_require_calibration),
            run_post_compute=bool(settings.automation_regional_run_post_compute),
            synergy_model_version=SYNERGY_MODEL_VERSION,
            quality_threshold=QUALITY_THRESHOLD_DEFAULT,
        )
        inner = (result.get("result") or {}) if isinstance(result, dict) else {}
        logger.info(
            "Regional post-event automation status=%s season=%s processed_events=%s completed_events=%s fallback=%s",
            result.get("status") if isinstance(result, dict) else None, season,
            inner.get("processed_event_count"), inner.get("completed_event_count"),
            inner.get("season_fallback_used"),
        )
        details.update(
            season=season,
            status=result.get("status") if isinstance(result, dict) else None,
            processed_event_count=inner.get("processed_event_count"),
            completed_event_count=inner.get("completed_event_count"),
        )

def _scheduled_live_regional_auto_manager() -> None:
    interval_sec = max(30, int(settings.live_analysis_regional_auto_interval_sec))
    with _scheduled_job("live_regional_auto_manager", lock_ttl_sec=max(120, interval_sec * 6)) as details:
        if details is None:
            return
        result = run_regional_live_auto_manager_tick(force=False)
        logger.info(
            "Regional live auto-manager status=%s discovered=%s started=%s updated=%s kept=%s stopped=%s errors=%s",
            result.get("status") if isinstance(result, dict) else None,
            len(result.get("discovered_active_events") or []) if isinstance(result, dict) else None,
            len(result.get("started_events") or []) if isinstance(result, dict) else None,
            len(result.get("updated_events") or []) if isinstance(result, dict) else None,
            len(result.get("kept_events") or []) if isinstance(result, dict) else None,
            len(result.get("stopped_events") or []) if isinstance(result, dict) else None,
            len(result.get("errored_events") or []) if isinstance(result, dict) else None,
        )
        details.update(
            status=result.get("status") if isinstance(result, dict) else None,
            discovered_count=len(result.get("discovered_active_events") or []) if isinstance(result, dict) else None,
            started_count=len(result.get("started_events") or []) if isinstance(result, dict) else None,
            errored_count=len(result.get("errored_events") or []) if isinstance(result, dict) else None,
        )

def _scheduled_low_quality_reprocess() -> None:
    interval_minutes = max(15, int(settings.analysis_low_quality_reprocess_interval_minutes))
    with _scheduled_job("low_quality_reprocess", lock_ttl_sec=max(300, interval_minutes * 120), use_db=True) as details:
        if details is None:
            return
        from app.services.low_quality_reprocess import reprocess_low_quality_matches
        result = reprocess_low_quality_matches(details["_db"])
        details.update(result if isinstance(result, dict) else {})

def _scheduled_ops_smoke_check() -> None:
    interval_minutes = max(5, int(settings.ops_smoke_check_interval_minutes))
    with _scheduled_job("ops_smoke_check", lock_ttl_sec=max(120, interval_minutes * 120)) as details:
        if details is None:
            return
        from app.services.ops_monitoring import run_ops_smoke_check
        result = run_ops_smoke_check()
        details.update(result if isinstance(result, dict) else {})

def _scheduled_auto_scout_draft_backfill() -> None:
    interval_minutes = max(5, int(settings.auto_scout_backfill_interval_minutes))
    with _scheduled_job(
        "auto_scout_draft_backfill",
        lock_ttl_sec=max(300, interval_minutes * 120),
        use_db=True,
    ) as details:
        if details is None:
            return
        db = details["_db"]
        result = backfill_missing_auto_scout_drafts(
            db,
            max_runs=int(settings.auto_scout_backfill_max_runs),
            max_drafts=int(settings.auto_scout_backfill_max_drafts),
            lookback_hours=int(settings.auto_scout_backfill_lookback_hours),
        )
        details.update(
            runs_processed=int(result.get("runs_processed") or 0),
            drafts_created=int(result.get("drafts_created") or 0),
            matches_with_missing=int(result.get("matches_with_missing") or 0),
            errors=len(result.get("errors") or []),
        )


def _scheduled_export_auto_scout_training_data() -> None:
    interval_hours = max(6, int(settings.ml_auto_scout_training_export_interval_hours))
    with _scheduled_job(
        "auto_scout_training_export",
        lock_ttl_sec=max(600, interval_hours * 2 * 3600),
        use_db=True,
    ) as details:
        if details is None:
            return
        db = details["_db"]
        result = export_auto_scout_training_snapshots(
            db,
            source_version=(
                str(settings.ml_auto_scout_feature_source_version or "").strip() or None
            ),
            replace_existing=bool(settings.ml_auto_scout_training_export_replace_existing),
            max_drafts=int(settings.ml_auto_scout_training_export_max_drafts),
            season_year=None,
        )
        logger.info(
            "Auto-scout training export completed approved_drafts=%s rows_written=%s skipped_context=%s skipped_target=%s",
            result.get("approved_drafts"),
            result.get("rows_written"),
            result.get("skipped_missing_context"),
            result.get("skipped_missing_target"),
        )
        details.update(
            approved_drafts=int(result.get("approved_drafts") or 0),
            rows_written=int(result.get("rows_written") or 0),
            skipped_missing_context=int(result.get("skipped_missing_context") or 0),
            skipped_missing_target=int(result.get("skipped_missing_target") or 0),
        )
