# Ops smoke check: regional automation staleness and lock streaks. Emits warnings
# on threshold breaches.
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import redis

from app.core.config import settings
from app.services.utils import (
    automation_redis_key as _automation_redis_key,
    decode_redis_float as _decode_redis_float,
)

logger = logging.getLogger(__name__)

_MIN_VALID_SEASON_YEAR = 2000  # FRC seasons began in 1992; values <= this indicate "not configured"


def _current_regional_automation_season() -> int:
    configured = int(settings.automation_regional_halfday_season or 0)
    if configured > _MIN_VALID_SEASON_YEAR:
        return configured
    return datetime.now(timezone.utc).year


def run_ops_smoke_check() -> dict[str, Any]:
    redis_conn = redis.from_url(
        settings.redis_url,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    try:
        season = _current_regional_automation_season()
        now_ts = datetime.now(timezone.utc).timestamp()

        last_run_ts = _decode_redis_float(redis_conn.get(_automation_redis_key("regional", season, "last_run_ts")))
        stale_hours_threshold = max(1, int(settings.ops_alert_regional_automation_stale_hours))
        regional_automation_stale = (last_run_ts is None) or (
            (now_ts - float(last_run_ts)) > float(stale_hours_threshold * 3600)
        )

        lock_active = redis_conn.get(_automation_redis_key("regional", season, "lock")) is not None
        lock_streak_key = _automation_redis_key("regional", season, "ops_lock_streak")
        lock_streak_threshold = max(1, int(settings.ops_alert_automation_lock_spike_threshold))
        if lock_active:
            lock_streak_checks = int(redis_conn.incr(lock_streak_key))
            redis_conn.expire(lock_streak_key, 7 * 24 * 3600)
        else:
            redis_conn.delete(lock_streak_key)
            lock_streak_checks = 0

        logger.info(
            "Ops smoke check regional_stale=%s lock_streak=%s",
            regional_automation_stale,
            lock_streak_checks,
        )
        if regional_automation_stale:
            logger.warning(
                "Ops smoke warning: regional automation run is stale (threshold=%sh, last_run_ts=%s)",
                stale_hours_threshold,
                last_run_ts,
            )
        if lock_streak_checks >= lock_streak_threshold:
            logger.warning(
                "Ops smoke warning: regional automation lock active too often streak_checks=%s threshold=%s",
                lock_streak_checks,
                lock_streak_threshold,
            )

        return {
            "regional_automation_stale": bool(regional_automation_stale),
            "regional_automation_stale_threshold_hours": int(stale_hours_threshold),
            "regional_automation_last_run_ts": last_run_ts,
            "automation_lock_active": bool(lock_active),
            "automation_lock_streak_checks": int(lock_streak_checks),
            "automation_lock_streak_threshold": int(lock_streak_threshold),
        }
    finally:
        redis_conn.close()
