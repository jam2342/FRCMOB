from datetime import datetime, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, Query
import redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_write_access
from app.db.session import get_db
from app.services.events.regional_automation import (
    RegionalAutomationError,
    run_regional_automation_tick,
)
from app.services.ml.synergy import QUALITY_THRESHOLD_DEFAULT, SYNERGY_MODEL_VERSION
from app.services.utils import (
    automation_redis_key as _automation_redis_key,
    decode_redis_float as _decode_redis_float,
)

router = APIRouter(prefix="/automation", tags=["automation"])


def _ensure_automation_write_enabled() -> None:
    require_write_access("Automation write endpoints")


@router.post("/regional/season/{season}/tick")
def automate_regional_post_event_breakdowns_tick(
    season: int,
    force_tick: bool = False,
    min_interval_minutes: int | None = None,
    include_all_events: bool | None = None,
    include_out_of_region_events_for_in_region_teams: bool | None = None,
    include_ended_today: bool | None = None,
    allow_previous_season_fallback: bool | None = None,
    max_events: int | None = None,
    max_teams: int | None = None,
    run_post_compute: bool | None = None,
    refresh_all: bool = False,
    synergy_model_version: str = Query(default=SYNERGY_MODEL_VERSION, alias="model_version"),
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    db: Session = Depends(get_db),
):
    _ensure_automation_write_enabled()
    try:
        return run_regional_automation_tick(
            season=season,
            force_tick=force_tick,
            min_interval_minutes=min_interval_minutes,
            include_all_events=include_all_events,
            include_out_of_region_events_for_in_region_teams=include_out_of_region_events_for_in_region_teams,
            include_ended_today=include_ended_today,
            allow_previous_season_fallback=allow_previous_season_fallback,
            max_events=max_events,
            max_teams=max_teams,
            run_post_compute=run_post_compute,
            refresh_all=refresh_all,
            synergy_model_version=synergy_model_version,
            quality_threshold=quality_threshold,
            db=db,
        )
    except RegionalAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/regional/season/{season}/status")
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
        },
    }
