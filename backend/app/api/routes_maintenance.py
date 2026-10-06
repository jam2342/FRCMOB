from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
import redis
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.services.season_config import regional_automation_season
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
from app.services.climb.official_backfill import (
    climb_signal_coverage,
    run_official_climb_backfill,
)
from app.services.calibration.fuel_rate import get_fuel_phase_calibration
from app.services.scheduler import get_scheduler_runtime_metrics
from app.services.clients import statbotics as statbotics_client
from app.services.utils import (
    MEDIA_ROOT,
    automation_redis_key as _automation_redis_key,
    decode_redis_float as _decode_redis_float,
)

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


class AdminSessionCreateRequest(BaseModel):
    admin_api_key: str = Field(default="", max_length=1024)
    ttl_sec: int | None = Field(default=None, ge=60, le=86400)


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


def _to_gb(value: int) -> float:
    return round(float(value) / float(1024**3), 3)


def media_usage_snapshot() -> dict[str, Any]:
    # Media now holds pit photos and model files only.
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    for path in MEDIA_ROOT.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total_bytes += path.stat().st_size
        except OSError:
            continue
    disk = shutil.disk_usage(str(MEDIA_ROOT))
    return {
        "media_root": str(MEDIA_ROOT),
        "total_bytes": total_bytes,
        "total_gb": _to_gb(total_bytes),
        "disk_total_gb": _to_gb(int(disk.total)),
        "disk_used_gb": _to_gb(int(disk.used)),
        "disk_free_gb": _to_gb(int(disk.free)),
    }


def _ops_alerts_from_metrics(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

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


def _regional_automation_snapshot() -> dict[str, Any]:
    season = regional_automation_season()
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

    scheduler_metrics = get_scheduler_runtime_metrics()
    statbotics_runtime = statbotics_client.runtime_metrics()
    fuel_rate_calibration = get_fuel_phase_calibration(force_refresh=False)
    on_device_status_rows = (
        db.query(
            models.OnDeviceSession.status,
            func.count(models.OnDeviceSession.id),
            func.avg(models.OnDeviceSession.quality_score),
        )
        .filter(models.OnDeviceSession.created_at >= since)
        .group_by(models.OnDeviceSession.status)
        .all()
    )

    return {
        "window_days": days,
        "generated_at": now_utc.isoformat(),
        "on_device_sessions": {
            "status_counts": {
                str(status): int(count or 0)
                for status, count, _average_quality in on_device_status_rows
            },
            "average_quality_by_status": {
                str(status): round(float(average_quality or 0.0), 4)
                for status, _count, average_quality in on_device_status_rows
            },
            "total": int(sum(int(count or 0) for _status, count, _avg in on_device_status_rows)),
        },
        "ratings": {
            "recent_recompute_spans": rating_recompute_spans,
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


@router.get("/ops/dashboard")
def get_ops_dashboard(
    request: Request,
    sample_days: int = Query(default=int(settings.ops_metrics_sample_days), ge=1, le=90),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "Ops dashboard")
    metrics = _ops_metrics_payload(db, sample_days)
    alerts = _ops_alerts_from_metrics(metrics)
    # metrics already contains automation from _ops_metrics_payload; reuse it instead
    # of making a second Redis round-trip.
    automation = metrics.get("automation") or {}
    media = media_usage_snapshot()
    climb_coverage = climb_signal_coverage(db, event_key=None, season_year=None, max_events=3)

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_days": int(sample_days),
        "alerts": alerts,
        "alert_count": len(alerts),
        "metrics": metrics,
        "automation": automation,
        "media_usage": media,
        "climb_signal_coverage": climb_coverage,
    }


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


