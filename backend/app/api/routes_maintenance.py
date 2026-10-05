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
from app.services.climb.integrity import (
    load_last_climb_integrity_result,
    safe_run_climb_integrity_audit,
)
from app.services.climb.official_backfill import (
    climb_signal_coverage,
    run_official_climb_backfill,
)
from app.services.calibration.fuel_rate import get_fuel_phase_calibration
from app.services.intel.snapshots import refresh_hot_intel_snapshots
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
from app.services.utils import (
    MEDIA_ROOT,
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


@router.get("/media/usage")
def get_media_usage(request: Request):
    require_admin_access(request, "Media usage")
    return {"ok": True, "usage": media_usage_snapshot()}


@router.post("/intel/snapshots/refresh")
def refresh_intel_snapshots(request: Request):
    require_admin_access(request, "Intel snapshot refresh")
    require_write_access("Intel snapshot refresh")
    result = refresh_hot_intel_snapshots()
    return {"ok": True, "result": result}


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
