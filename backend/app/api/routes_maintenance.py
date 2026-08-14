from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
import redis
from rq import Queue
from rq.registry import DeferredJobRegistry, FailedJobRegistry, ScheduledJobRegistry, StartedJobRegistry
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    ADMIN_SESSION_HEADER,
    admin_api_key_authorized,
    issue_admin_session_token,
    require_admin_access,
    require_write_access,
)
from app.db import models
from app.db.session import get_db
from app.services.climb.integrity import (
    load_last_climb_integrity_result,
    safe_run_climb_integrity_audit,
)
from app.services.climb.official_backfill import (
    climb_signal_coverage,
    run_official_climb_backfill,
)
from app.services.freshness_recovery import (
    build_event_team_freshness_rows,
    load_last_result as load_freshness_recovery_last_result,
    recover_event_freshness,
    recover_stale_events,
)
from app.services.calibration.fuel_rate import get_fuel_phase_calibration
from app.services.intel.snapshots import refresh_hot_intel_snapshots
from app.services.media.retention import media_usage_snapshot, run_media_retention
from app.services.auto_scout.ml import (
    AUTO_SCOUT_FIELD_MODEL_PREFIX,
    auto_scout_field_model_key,
    normalize_auto_scout_field_name,
)
from app.services.auto_scout.training import export_auto_scout_training_snapshots
from app.services.ml.shadow import (
    MATCH_OUTCOME_MODEL_KEY,
    TEAM_STRENGTH_MODEL_KEY,
    get_shadow_pipeline_status,
    materialize_shadow_predictions_for_event,
    rebuild_feature_snapshots,
    train_auto_scout_field_shadow_model,
    train_match_outcome_shadow_model,
    train_team_strength_shadow_model,
)
from app.services.scheduler import get_scheduler_runtime_metrics
from app.services.clients import statbotics as statbotics_client
from app.services.ml.synergy import QUALITY_THRESHOLD_DEFAULT, SYNERGY_MODEL_VERSION
from app.services.utils import (
    automation_redis_key as _automation_redis_key,
    decode_redis_float as _decode_redis_float,
)

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


class AdminSessionCreateRequest(BaseModel):
    admin_api_key: str = Field(default="", max_length=1024)
    ttl_sec: int | None = Field(default=None, ge=60, le=86400)


@router.get("/admin/auth-check")
def admin_auth_check(request: Request):
    require_admin_access(request, "Admin auth check")
    return {"ok": True, "authorized": True}


@router.post("/admin/session")
def create_admin_session(request: AdminSessionCreateRequest):
    candidate_key = str(request.admin_api_key or "").strip()
    if not admin_api_key_authorized(candidate_key):
        raise HTTPException(status_code=401, detail="Invalid admin API key.")

    token_payload = issue_admin_session_token(
        ttl_sec=request.ttl_sec,
        principal="scouting_frontend",
    )
    return {
        "ok": True,
        "authorized": True,
        "status": 200,
        "session_token": token_payload["token"],
        "expires_at": token_payload["expires_at"],
        "expires_at_unix": token_payload["expires_at_unix"],
        "ttl_sec": token_payload["ttl_sec"],
        "header": ADMIN_SESSION_HEADER,
    }


@router.get("/media/usage")
def get_media_usage(request: Request):
    require_admin_access(request, "Media usage")
    return {"ok": True, "usage": media_usage_snapshot()}


@router.post("/media/cleanup")
def cleanup_media(
    request: Request,
    force: bool = Query(default=True),
    reason: str = Query(default="manual"),
):
    require_admin_access(request, "Media cleanup")
    require_write_access("Media cleanup")
    result = run_media_retention(force=force, reason=reason)
    return {"ok": True, "cleanup": result}


@router.post("/intel/snapshots/refresh")
def refresh_intel_snapshots(request: Request):
    require_admin_access(request, "Intel snapshot refresh")
    require_write_access("Intel snapshot refresh")
    result = refresh_hot_intel_snapshots()
    return {"ok": True, "result": result}


def _safe_age_hours(now_utc: datetime, unix_ts: int | None) -> float | None:
    if not isinstance(unix_ts, int) or unix_ts <= 0:
        return None
    age = max(0.0, now_utc.timestamp() - float(unix_ts))
    return round(age / 3600.0, 3)


def _ops_alerts_from_metrics(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    runs = metrics.get("analysis_runs") if isinstance(metrics.get("analysis_runs"), dict) else {}
    run_counts = runs.get("status_counts") if isinstance(runs.get("status_counts"), dict) else {}
    total_runs = int(runs.get("total") or 0)
    failed_runs = int(run_counts.get("failed") or 0)
    failed_ratio = (failed_runs / total_runs) if total_runs > 0 else 0.0
    if total_runs >= 10 and failed_ratio >= 0.15:
        alerts.append(
            {
                "level": "warning",
                "code": "analysis_failure_ratio_high",
                "message": "Analysis failure ratio is above threshold.",
                "value": round(failed_ratio, 4),
                "threshold": 0.15,
            }
        )

    backend_block = metrics.get("tracking_backend") if isinstance(metrics.get("tracking_backend"), dict) else {}
    backend_counts = backend_block.get("counts") if isinstance(backend_block.get("counts"), dict) else {}
    total_findings = int(backend_block.get("total_findings") or 0)
    yolo_findings = int(backend_counts.get("yolo_bytetrack") or 0)
    yolo_ratio = (yolo_findings / total_findings) if total_findings > 0 else 0.0
    if total_findings >= 25 and yolo_ratio < 0.20:
        alerts.append(
            {
                "level": "info",
                "code": "yolo_coverage_low",
                "message": "YOLO+ByteTrack coverage is low; pipeline is relying on motion fallback.",
                "value": round(yolo_ratio, 4),
                "threshold": 0.20,
            }
        )

    freshness = metrics.get("freshness") if isinstance(metrics.get("freshness"), dict) else {}
    stale_event_count = int(freshness.get("stale_event_count") or 0)
    if stale_event_count > 0:
        alerts.append(
            {
                "level": "warning",
                "code": "stale_analysis_data",
                "message": "One or more events have stale analysis data.",
                "value": stale_event_count,
            }
        )

    automation = metrics.get("automation") if isinstance(metrics.get("automation"), dict) else {}
    last_result = automation.get("last_result") if isinstance(automation.get("last_result"), dict) else {}
    blocked_reason_counts = (
        last_result.get("blocked_reason_counts")
        if isinstance(last_result.get("blocked_reason_counts"), dict)
        else {}
    )
    blocked_totals = last_result.get("totals") if isinstance(last_result.get("totals"), dict) else {}
    missing_calibration_blocked = int(blocked_reason_counts.get("missing_calibration") or 0)
    blocked_matches = int(blocked_totals.get("blocked_matches") or 0)
    scheduled_matches = int(blocked_totals.get("scheduled_matches") or 0)
    missing_threshold = max(1, int(settings.ops_alert_automation_missing_calibration_threshold))
    blocked_ratio_threshold = max(0.05, min(0.95, float(settings.ops_alert_automation_blocked_ratio_threshold)))
    blocked_ratio = (blocked_matches / max(1, blocked_matches + scheduled_matches))

    if missing_calibration_blocked >= missing_threshold:
        alerts.append(
            {
                "level": "warning",
                "code": "automation_missing_calibration_spike",
                "message": "Regional automation is blocking many matches on calibration.",
                "value": missing_calibration_blocked,
                "threshold": missing_threshold,
            }
        )
    if blocked_matches >= 10 and blocked_ratio >= blocked_ratio_threshold:
        alerts.append(
            {
                "level": "warning",
                "code": "automation_blocked_ratio_high",
                "message": "Regional automation blocked ratio is high for the last tick.",
                "value": round(blocked_ratio, 4),
                "threshold": round(blocked_ratio_threshold, 4),
            }
        )

    scheduler_block = metrics.get("scheduler") if isinstance(metrics.get("scheduler"), dict) else {}
    scheduler_jobs = scheduler_block.get("jobs") if isinstance(scheduler_block.get("jobs"), dict) else {}
    intel_job = (
        scheduler_jobs.get("refresh_intel_snapshots")
        if isinstance(scheduler_jobs.get("refresh_intel_snapshots"), dict)
        else {}
    )
    intel_details = intel_job.get("last_details") if isinstance(intel_job.get("last_details"), dict) else {}
    if bool(intel_details.get("timed_out")):
        alerts.append(
            {
                "level": "warning",
                "code": "intel_snapshot_runtime_budget_exceeded",
                "message": "Intel snapshot refresh exceeded its runtime budget and reduced coverage.",
                "value": intel_details.get("runtime_sec"),
                "threshold": intel_details.get("runtime_budget_sec"),
            }
        )

    ops_smoke_job = (
        scheduler_jobs.get("ops_smoke_check")
        if isinstance(scheduler_jobs.get("ops_smoke_check"), dict)
        else {}
    )
    ops_smoke_details = (
        ops_smoke_job.get("last_details")
        if isinstance(ops_smoke_job.get("last_details"), dict)
        else {}
    )
    if bool(ops_smoke_details.get("regional_automation_stale")):
        alerts.append(
            {
                "level": "warning",
                "code": "regional_automation_stale",
                "message": "Regional automation has not completed successfully within the stale threshold.",
                "value": ops_smoke_details.get("regional_automation_last_run_ts"),
                "threshold": ops_smoke_details.get("regional_automation_stale_threshold_hours"),
            }
        )
    if bool(ops_smoke_details.get("queue_stuck")):
        alerts.append(
            {
                "level": "warning",
                "code": "analysis_queue_stuck",
                "message": "Analysis queue appears stuck (pending jobs not changing).",
                "value": ops_smoke_details.get("queue_stuck_minutes"),
                "threshold": ops_smoke_details.get("queue_stuck_threshold_minutes"),
            }
        )
    lock_streak_checks = int(ops_smoke_details.get("automation_lock_streak_checks") or 0)
    lock_streak_threshold = int(ops_smoke_details.get("automation_lock_streak_threshold") or 0)
    if lock_streak_threshold > 0 and lock_streak_checks >= lock_streak_threshold:
        alerts.append(
            {
                "level": "warning",
                "code": "automation_lock_spike",
                "message": "Regional automation lock appears repeatedly active across smoke checks.",
                "value": lock_streak_checks,
                "threshold": lock_streak_threshold,
            }
        )
    youtube_health = (
        ops_smoke_details.get("youtube_extraction_health")
        if isinstance(ops_smoke_details.get("youtube_extraction_health"), dict)
        else {}
    )
    fallback_ratio = float(youtube_health.get("stream_fallback_ratio_0_1") or 0.0)
    fallback_ratio_threshold = float(ops_smoke_details.get("youtube_stream_fallback_ratio_threshold_0_1") or 0.0)
    fallback_source_rows = int(youtube_health.get("source_video_rows") or 0)
    if fallback_source_rows >= 20 and fallback_ratio >= fallback_ratio_threshold > 0.0:
        alerts.append(
            {
                "level": "warning",
                "code": "youtube_stream_fallback_ratio_high",
                "message": "YouTube stream fallback ratio is above SLO target.",
                "value": round(fallback_ratio, 4),
                "threshold": round(fallback_ratio_threshold, 4),
            }
        )
    hard_failure_ratio = float(youtube_health.get("hard_extraction_failure_ratio_0_1") or 0.0)
    hard_failure_ratio_threshold = float(ops_smoke_details.get("youtube_hard_failure_ratio_threshold_0_1") or 0.0)
    youtube_sample_runs = int(youtube_health.get("sample_runs") or 0)
    if youtube_sample_runs >= 20 and hard_failure_ratio >= hard_failure_ratio_threshold > 0.0:
        alerts.append(
            {
                "level": "warning",
                "code": "youtube_hard_extraction_failure_ratio_high",
                "message": "YouTube hard extraction failure ratio is above SLO target.",
                "value": round(hard_failure_ratio, 4),
                "threshold": round(hard_failure_ratio_threshold, 4),
            }
        )

    statbotics_runtime = (
        metrics.get("statbotics_runtime")
        if isinstance(metrics.get("statbotics_runtime"), dict)
        else {}
    )
    circuits = statbotics_runtime.get("circuits") if isinstance(statbotics_runtime.get("circuits"), dict) else {}
    open_count = int(circuits.get("open_count") or 0)
    if open_count > 0:
        alerts.append(
            {
                "level": "warning",
                "code": "statbotics_circuit_open",
                "message": "Statbotics circuit breaker is open for one or more endpoints.",
                "value": open_count,
            }
        )
    return alerts


def _get_redis_conn() -> "redis.Redis":
    return redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)


def _queue_snapshot() -> dict[str, Any]:
    try:
        redis_conn = _get_redis_conn()
        queue = Queue("default", connection=redis_conn)
        started = StartedJobRegistry(queue=queue)
        deferred = DeferredJobRegistry(queue=queue)
        scheduled = ScheduledJobRegistry(queue=queue)
        failed = FailedJobRegistry(queue=queue)

        queued_ids = list(queue.job_ids)
        started_ids = started.get_job_ids()
        deferred_ids = deferred.get_job_ids()
        scheduled_ids = scheduled.get_job_ids()
        failed_ids = failed.get_job_ids()

        pending = len(queued_ids) + len(started_ids) + len(deferred_ids) + len(scheduled_ids)
        pending_cap = max(10, int(settings.analysis_queue_max_pending_jobs))
        pressure = min(1.0, float(pending) / float(pending_cap))
        return {
            "ok": True,
            "name": queue.name,
            "counts": {
                "queued": len(queued_ids),
                "started": len(started_ids),
                "deferred": len(deferred_ids),
                "scheduled": len(scheduled_ids),
                "failed": len(failed_ids),
                "pending_total": pending,
            },
            "caps": {
                "max_pending_jobs": pending_cap,
                "max_schedule_per_call": max(1, int(settings.analysis_queue_max_schedule_per_call)),
            },
            "pressure_0_1": round(pressure, 4),
        }
    except Exception as exc:
        return {
            "ok": False,
            "detail": str(exc),
        }


def _current_regional_automation_season() -> int:
    configured = int(getattr(settings, "automation_regional_halfday_season", 0) or 0)
    if 2015 <= configured <= 2100:
        return configured
    return int(datetime.now(timezone.utc).year)


def _regional_automation_snapshot() -> dict[str, Any]:
    season = _current_regional_automation_season()
    interval_minutes = max(1, int(settings.automation_regional_interval_minutes))
    interval_seconds = interval_minutes * 60
    try:
        redis_conn = _get_redis_conn()
        last_run_key = _automation_redis_key("regional", season, "last_run_ts")
        last_result_key = _automation_redis_key("regional", season, "last_result")
        lock_key = _automation_redis_key("regional", season, "lock")

        now_ts = datetime.now(timezone.utc).timestamp()
        last_run_ts = _decode_redis_float(redis_conn.get(last_run_key))
        next_run_ts = (last_run_ts + interval_seconds) if last_run_ts is not None else None
        due_now = next_run_ts is None or now_ts >= next_run_ts
        lock_active = redis_conn.get(lock_key) is not None

        last_result_payload = None
        last_result_raw = redis_conn.get(last_result_key)
        if isinstance(last_result_raw, bytes):
            last_result_raw = last_result_raw.decode("utf-8", errors="ignore")
        if isinstance(last_result_raw, str) and last_result_raw.strip():
            try:
                parsed = json.loads(last_result_raw)
                if isinstance(parsed, dict):
                    last_result_payload = parsed
            except json.JSONDecodeError:
                last_result_payload = {"raw": last_result_raw}

        return {
            "ok": True,
            "season": season,
            "enabled": bool(settings.automation_regional_enabled),
            "interval_minutes": interval_minutes,
            "locked": lock_active,
            "due_now": bool(due_now),
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
            "last_result": last_result_payload,
            "caps": {
                "max_events": int(settings.automation_regional_max_events),
                "max_teams": int(settings.automation_regional_max_teams),
                "max_matches_per_event": int(settings.automation_regional_max_matches_per_event),
                "max_new_jobs_per_tick": int(settings.automation_regional_max_new_jobs_per_tick),
                "max_pending_jobs": int(settings.analysis_queue_max_pending_jobs),
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "season": season,
            "detail": str(exc),
        }


def _ops_metrics_payload(db: Session, sample_days: int) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    days = max(1, min(sample_days, 90))
    since = now_utc - timedelta(days=days)

    run_status_rows = (
        db.query(models.AnalysisRun.status, func.count(models.AnalysisRun.id))
        .filter(models.AnalysisRun.created_at >= since)
        .group_by(models.AnalysisRun.status)
        .all()
    )
    run_status_counts = {str(status or "unknown"): int(count or 0) for status, count in run_status_rows}

    # Use a SQL GROUP BY on the source column instead of loading all ORM objects.
    # The tracking_backend is also embedded in the JSON summary, but source is a
    # reliable indexed column and avoids hydrating thousands of wide rows into Python.
    source_count_rows = (
        db.query(models.TeamMatchFinding.source, func.count(models.TeamMatchFinding.id))
        .filter(models.TeamMatchFinding.created_at >= since)
        .group_by(models.TeamMatchFinding.source)
        .all()
    )
    backend_counts: dict[str, int] = {
        str(src or "unknown").strip().lower(): int(cnt or 0)
        for src, cnt in source_count_rows
    }

    event_age_rows = (
        db.query(
            models.TeamMatchFinding.event_key,
            func.max(models.Match.time).label("latest_match_time"),
            func.count(models.TeamMatchFinding.id).label("finding_count"),
        )
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .group_by(models.TeamMatchFinding.event_key)
        .all()
    )
    event_ages = []
    for event_key, latest_match_time, finding_count in event_age_rows:
        age_hours = _safe_age_hours(now_utc, int(latest_match_time) if isinstance(latest_match_time, int) else None)
        event_ages.append(
            {
                "event_key": str(event_key),
                "finding_count": int(finding_count or 0),
                "latest_match_time": int(latest_match_time) if isinstance(latest_match_time, int) else None,
                "age_hours": age_hours,
            }
        )

    stale_hours_threshold = max(1, int(settings.freshness_sla_stale_hours))
    stale_events = [row for row in event_ages if isinstance(row.get("age_hours"), float) and row["age_hours"] > stale_hours_threshold]

    rating_span_rows = (
        db.query(
            models.EventTeamRating.event_key,
            func.min(models.EventTeamRating.updated_at).label("min_updated"),
            func.max(models.EventTeamRating.updated_at).label("max_updated"),
            func.count(models.EventTeamRating.team_key).label("row_count"),
        )
        .group_by(models.EventTeamRating.event_key)
        .order_by(func.max(models.EventTeamRating.updated_at).desc())
        .limit(25)
        .all()
    )
    rating_recompute_spans = []
    for event_key, min_updated, max_updated, row_count in rating_span_rows:
        span_sec = None
        if isinstance(min_updated, datetime) and isinstance(max_updated, datetime):
            span_sec = max(0.0, (max_updated - min_updated).total_seconds())
        rating_recompute_spans.append(
            {
                "event_key": str(event_key),
                "rows": int(row_count or 0),
                "updated_min": min_updated.isoformat() if isinstance(min_updated, datetime) else None,
                "updated_max": max_updated.isoformat() if isinstance(max_updated, datetime) else None,
                "duration_estimate_sec": round(span_sec, 3) if isinstance(span_sec, float) else None,
            }
        )

    freshness_recovery_last = load_freshness_recovery_last_result()
    scheduler_metrics = get_scheduler_runtime_metrics()
    statbotics_runtime = statbotics_client.runtime_metrics()
    fuel_rate_calibration = get_fuel_phase_calibration(force_refresh=False)

    return {
        "window_days": days,
        "generated_at": now_utc.isoformat(),
        "analysis_runs": {
            "status_counts": run_status_counts,
            "total": int(sum(run_status_counts.values())),
        },
        "tracking_backend": {
            "counts": backend_counts,
            "total_findings": int(sum(backend_counts.values())),
        },
        "freshness": {
            "stale_hours_threshold": stale_hours_threshold,
            "event_ages": event_ages,
            "stale_events": stale_events,
            "stale_event_count": len(stale_events),
        },
        "ratings": {
            "recent_recompute_spans": rating_recompute_spans,
        },
        "freshness_recovery": {
            "last_result": freshness_recovery_last,
            "last_result_available": bool(isinstance(freshness_recovery_last, dict)),
        },
        "fuel_rate_calibration": {
            "model_version": fuel_rate_calibration.get("model_version"),
            "generated_at": fuel_rate_calibration.get("generated_at"),
            "teleop": fuel_rate_calibration.get("teleop"),
            "auto": fuel_rate_calibration.get("auto"),
        },
        "scheduler": scheduler_metrics,
        "statbotics_runtime": statbotics_runtime,
        "automation": _regional_automation_snapshot(),
    }


@router.get("/ops/metrics")
def get_ops_metrics(
    request: Request,
    sample_days: int = Query(default=int(settings.ops_metrics_sample_days), ge=1, le=90),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "Ops metrics")
    payload = _ops_metrics_payload(db, sample_days)
    return {"ok": True, "metrics": payload}


@router.get("/ops/alerts")
def get_ops_alerts(
    request: Request,
    sample_days: int = Query(default=int(settings.ops_metrics_sample_days), ge=1, le=90),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "Ops alerts")
    metrics = _ops_metrics_payload(db, sample_days)
    alerts = _ops_alerts_from_metrics(metrics)

    return {
        "ok": True,
        "alerts": alerts,
        "alert_count": len(alerts),
        "sample_days": int(sample_days),
    }


@router.get("/ops/dashboard")
def get_ops_dashboard(
    request: Request,
    sample_days: int = Query(default=int(settings.ops_metrics_sample_days), ge=1, le=90),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "Ops dashboard")
    metrics = _ops_metrics_payload(db, sample_days)
    alerts = _ops_alerts_from_metrics(metrics)
    queue = _queue_snapshot()
    # metrics already contains automation from _ops_metrics_payload; reuse it instead
    # of making a second Redis round-trip.
    automation = metrics.get("automation") or {}
    media = media_usage_snapshot()
    climb_audit_last = load_last_climb_integrity_result()
    climb_coverage = climb_signal_coverage(db, event_key=None, season_year=None, max_events=3)

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_days": int(sample_days),
        "alerts": alerts,
        "alert_count": len(alerts),
        "metrics": metrics,
        "queue": queue,
        "automation": automation,
        "media_usage": media,
        "climb_integrity": {
            "enabled": bool(settings.climb_integrity_audit_enabled),
            "last_result": climb_audit_last,
            "last_result_available": bool(isinstance(climb_audit_last, dict)),
        },
        "climb_signal_coverage": climb_coverage,
    }


@router.get("/climb/audit/last")
def get_climb_audit_last_result(request: Request):
    require_admin_access(request, "Climb audit result")
    result = load_last_climb_integrity_result()
    return {
        "ok": True,
        "result": result,
        "available": bool(isinstance(result, dict)),
    }


@router.get("/fuel-rate/calibration")
def get_fuel_rate_calibration(request: Request, force_refresh: bool = Query(default=False)):
    require_admin_access(request, "Fuel rate calibration")
    calibration = get_fuel_phase_calibration(force_refresh=bool(force_refresh))
    return {
        "ok": True,
        "calibration": calibration,
    }


@router.post("/climb/audit/run")
def run_climb_audit(
    request: Request,
    lookback_days: int = Query(default=int(settings.climb_integrity_audit_lookback_days), ge=1, le=60),
    sample_limit: int = Query(default=int(settings.climb_integrity_audit_sample_limit), ge=100, le=200000),
    diff_threshold: float = Query(default=float(settings.climb_integrity_audit_diff_threshold), ge=0.05, le=1.0),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "Climb integrity audit")
    require_write_access("Climb integrity audit")
    result = safe_run_climb_integrity_audit(
        db,
        lookback_days=int(lookback_days),
        sample_limit=int(sample_limit),
        diff_threshold=float(diff_threshold),
    )
    return {"ok": bool(result.get("ok")), "result": result}


@router.post("/climb/official-backfill/run")
def run_climb_official_backfill(
    request: Request,
    event_key: str | None = None,
    max_events: int = Query(default=int(settings.climb_official_backfill_max_events_per_run), ge=1, le=40),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "Climb official backfill")
    require_write_access("Climb official backfill")
    result = run_official_climb_backfill(
        db,
        event_key=event_key,
        max_events=int(max_events),
    )
    return {"ok": bool(result.get("ok")), "result": result}


@router.get("/climb/signal-coverage")
def get_climb_signal_coverage(
    request: Request,
    event_key: str | None = None,
    season_year: int | None = Query(default=None, ge=2015, le=2100),
    max_events: int = Query(default=10, ge=1, le=40),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "Climb signal coverage")
    result = climb_signal_coverage(
        db,
        event_key=event_key,
        season_year=season_year,
        max_events=max_events,
    )
    return {"ok": True, "coverage": result}


@router.get("/freshness-sla")
def get_freshness_sla(
    request: Request,
    event_key: str | None = None,
    stale_hours: int | None = Query(default=None, ge=1, le=24 * 30),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "Freshness SLA")
    now_utc = datetime.now(timezone.utc)
    threshold_hours = int(stale_hours or settings.freshness_sla_stale_hours)

    normalized_event_key = event_key.strip().lower() if isinstance(event_key, str) and event_key.strip() else None
    if normalized_event_key:
        teams_payload = build_event_team_freshness_rows(
            db,
            normalized_event_key,
            stale_hours_threshold=threshold_hours,
            now_utc=now_utc,
        )

        return {
            "ok": True,
            "scope": "event",
            "event_key": normalized_event_key,
            "stale_hours_threshold": threshold_hours,
            "summary": {
                "teams": len(teams_payload),
                "fresh": sum(1 for row in teams_payload if row["status"] == "fresh"),
                "stale": sum(1 for row in teams_payload if row["status"] == "stale"),
                "missing": sum(1 for row in teams_payload if row["status"] == "missing"),
            },
            "teams": teams_payload,
        }

    event_rows = (
        db.query(
            models.TeamMatchFinding.event_key,
            func.count(models.TeamMatchFinding.id).label("finding_count"),
            func.max(models.Match.time).label("latest_match_time"),
        )
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .group_by(models.TeamMatchFinding.event_key)
        .all()
    )

    events_payload = []
    for found_event_key, finding_count, latest_match_time in event_rows:
        age_hours = _safe_age_hours(now_utc, int(latest_match_time) if isinstance(latest_match_time, int) else None)
        if int(finding_count or 0) <= 0:
            status = "missing"
        elif isinstance(age_hours, float) and age_hours > threshold_hours:
            status = "stale"
        else:
            status = "fresh"
        events_payload.append(
            {
                "event_key": str(found_event_key),
                "finding_count": int(finding_count or 0),
                "latest_match_time": int(latest_match_time) if isinstance(latest_match_time, int) else None,
                "age_hours": age_hours,
                "status": status,
            }
        )

    return {
        "ok": True,
        "scope": "global",
        "stale_hours_threshold": threshold_hours,
        "summary": {
            "events": len(events_payload),
            "fresh": sum(1 for row in events_payload if row["status"] == "fresh"),
            "stale": sum(1 for row in events_payload if row["status"] == "stale"),
            "missing": sum(1 for row in events_payload if row["status"] == "missing"),
        },
        "events": events_payload,
    }


@router.post("/freshness/recover")
def recover_freshness_coverage(
    request: Request,
    event_key: str | None = None,
    stale_hours: int | None = Query(default=None, ge=1, le=24 * 30),
    max_events: int = Query(default=3, ge=1, le=50),
    max_target_teams_per_event: int = Query(default=30, ge=1, le=300),
    force_analysis: bool = False,
    require_video: bool = True,
    require_calibration: bool = True,
    run_post_compute: bool = True,
    synergy_model_version: str = Query(default=SYNERGY_MODEL_VERSION, alias="model_version"),
    quality_threshold: float = Query(default=QUALITY_THRESHOLD_DEFAULT, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "Freshness recovery")
    require_write_access("Freshness recovery")
    threshold_hours = int(stale_hours or settings.freshness_sla_stale_hours)
    normalized_event_key = event_key.strip().lower() if isinstance(event_key, str) and event_key.strip() else None

    if normalized_event_key:
        result = recover_event_freshness(
            db,
            event_key=normalized_event_key,
            stale_hours_threshold=threshold_hours,
            force_analysis=force_analysis,
            require_video=require_video,
            require_calibration=require_calibration,
            run_post_compute=run_post_compute,
            model_version=synergy_model_version,
            quality_threshold=quality_threshold,
            max_target_teams=max_target_teams_per_event,
        )
        return {"ok": True, "mode": "event", "result": result}

    result = recover_stale_events(
        db,
        stale_hours_threshold=threshold_hours,
        max_events=max_events,
        max_target_teams_per_event=max_target_teams_per_event,
        force_analysis=force_analysis,
        require_video=require_video,
        require_calibration=require_calibration,
        run_post_compute=run_post_compute,
        model_version=synergy_model_version,
        quality_threshold=quality_threshold,
    )
    return {"ok": True, "mode": "global", "result": result}


@router.get("/freshness/recover/last")
def get_last_freshness_recovery(request: Request):
    require_admin_access(request, "Freshness recovery last result")
    return {
        "ok": True,
        "last_result": load_freshness_recovery_last_result(),
    }


@router.get("/ml/shadow/status")
def get_ml_shadow_status(
    request: Request,
    event_key: str | None = None,
    db: Session = Depends(get_db),
):
    require_admin_access(request, "ML shadow status")
    return get_shadow_pipeline_status(db, event_key=event_key)


@router.post("/ml/shadow/snapshots/rebuild")
def rebuild_ml_shadow_snapshots(
    request: Request,
    event_key: str | None = None,
    limit_events: int = Query(default=30, ge=1, le=250),
    replace_existing: bool = Query(default=True),
    source_version: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "ML shadow snapshot rebuild")
    require_write_access("ML shadow snapshot rebuild")
    result = rebuild_feature_snapshots(
        db,
        event_key=event_key,
        limit_events=int(limit_events),
        replace_existing=bool(replace_existing),
        source_version=source_version,
    )
    return result


def _normalize_train_model_key(raw_model_key: object, *, field_name: str | None = None) -> str:
    token = str(raw_model_key or "all").strip().lower()
    if token in {"all", TEAM_STRENGTH_MODEL_KEY, MATCH_OUTCOME_MODEL_KEY}:
        return token
    if token == AUTO_SCOUT_FIELD_MODEL_PREFIX:
        normalized_field = normalize_auto_scout_field_name(field_name)
        if not normalized_field:
            raise ValueError(
                "field_name is required when model_key=auto_scout_field."
            )
        return auto_scout_field_model_key(normalized_field)
    if token.startswith(f"{AUTO_SCOUT_FIELD_MODEL_PREFIX}:"):
        normalized_field = normalize_auto_scout_field_name(token.split(":", 1)[1])
        if not normalized_field:
            raise ValueError("auto_scout_field model_key must include a field name.")
        return auto_scout_field_model_key(normalized_field)
    raise ValueError(
        "Unsupported model_key. Use team_strength, match_outcome, all, or auto_scout_field:<field_name>."
    )


@router.post("/ml/shadow/train")
def train_ml_shadow_models(
    request: Request,
    model_key: str = Query(default="all"),
    field_name: str | None = Query(default=None),
    model_version: str | None = Query(default=None),
    source_version: str | None = Query(default=None),
    activate: bool = Query(default=False),
    train_ratio: float = Query(default=0.8, ge=0.5, le=0.95),
    epochs: int | None = Query(default=None, ge=1, le=5000),
    learning_rate: float | None = Query(default=None, gt=0.0, le=1.0),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "ML shadow model training")
    require_write_access("ML shadow model training")

    try:
        normalized_model_key = _normalize_train_model_key(model_key, field_name=field_name)
        common_kwargs: dict[str, Any] = {
            "model_version": model_version,
            "source_version": source_version,
            "activate": activate,
            "train_ratio": train_ratio,
        }
        if epochs is not None:
            common_kwargs["epochs"] = int(epochs)
        if learning_rate is not None:
            common_kwargs["learning_rate"] = float(learning_rate)

        if normalized_model_key == TEAM_STRENGTH_MODEL_KEY:
            return train_team_strength_shadow_model(
                db,
                **common_kwargs,
            )
        if normalized_model_key == MATCH_OUTCOME_MODEL_KEY:
            return train_match_outcome_shadow_model(
                db,
                **common_kwargs,
            )
        if normalized_model_key.startswith(f"{AUTO_SCOUT_FIELD_MODEL_PREFIX}:"):
            auto_field_name = normalize_auto_scout_field_name(normalized_model_key.split(":", 1)[1])
            return train_auto_scout_field_shadow_model(
                db,
                field_name=auto_field_name,
                **common_kwargs,
            )

        return {
            "ok": True,
            "results": {
                TEAM_STRENGTH_MODEL_KEY: train_team_strength_shadow_model(
                    db,
                    model_version=None,
                    source_version=source_version,
                    activate=activate,
                    train_ratio=train_ratio,
                    epochs=int(epochs) if epochs is not None else 320,
                    learning_rate=float(learning_rate) if learning_rate is not None else 0.003,
                ),
                MATCH_OUTCOME_MODEL_KEY: train_match_outcome_shadow_model(
                    db,
                    model_version=None,
                    source_version=source_version,
                    activate=activate,
                    train_ratio=train_ratio,
                    epochs=int(epochs) if epochs is not None else 320,
                    learning_rate=float(learning_rate) if learning_rate is not None else 0.003,
                ),
            },
        }
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ml/auto-scout/export")
def export_auto_scout_training_data(
    request: Request,
    source_version: str | None = Query(default=None),
    season_year: int | None = Query(default=None, ge=2020, le=2100),
    replace_existing: bool = Query(default=False),
    max_drafts: int | None = Query(default=None, ge=1, le=100000),
    field_names: str | None = Query(
        default=None,
        description="Comma-separated auto-scout field names. Defaults to Phase-2 round2 fields.",
    ),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "Auto-scout training export")
    require_write_access("Auto-scout training export")
    normalized_fields = None
    if isinstance(field_names, str) and field_names.strip():
        normalized_fields = [
            normalize_auto_scout_field_name(token)
            for token in field_names.split(",")
            if normalize_auto_scout_field_name(token)
        ]
    try:
        return export_auto_scout_training_snapshots(
            db,
            source_version=source_version,
            season_year=season_year,
            replace_existing=replace_existing,
            max_drafts=max_drafts,
            field_names=normalized_fields,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ml/shadow/predict")
def materialize_ml_shadow_predictions(
    request: Request,
    event_key: str,
    model_key: str = Query(default="all", pattern="^(team_strength|match_outcome|all)$"),
    team_strength_model_version: str | None = Query(default=None),
    match_outcome_model_version: str | None = Query(default=None),
    replace_existing: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "ML shadow prediction materialization")
    require_write_access("ML shadow prediction materialization")
    try:
        return materialize_shadow_predictions_for_event(
            db,
            event_key=event_key,
            model_key=model_key,
            team_strength_model_version=team_strength_model_version,
            match_outcome_model_version=match_outcome_model_version,
            replace_existing=replace_existing,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
