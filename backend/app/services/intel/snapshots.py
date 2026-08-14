from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.season_config import CURRENT_SEASON_YEAR
from app.db import models
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _merge_missing_fields_only(
    existing_payload: dict[str, Any],
    fresh_payload: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    merged = dict(existing_payload)
    filled = 0
    for key, fresh_value in fresh_payload.items():
        existing_value = merged.get(key)
        if isinstance(existing_value, dict) and isinstance(fresh_value, dict):
            child, child_filled = _merge_missing_fields_only(existing_value, fresh_value)
            merged[key] = child
            filled += int(child_filled)
            continue
        if _is_missing_value(existing_value) and not _is_missing_value(fresh_value):
            merged[key] = fresh_value
            filled += 1
    return merged, filled


def read_snapshot(
    db: Session,
    *,
    snapshot_key: str,
) -> tuple[dict[str, Any] | None, int | None]:
    if not bool(settings.intel_snapshot_enabled):
        return None, None

    try:
        row = db.get(models.IntelSnapshot, snapshot_key)
    except Exception as exc:
        db.rollback()
        logger.warning("intel snapshot read failed key=%s error=%s", snapshot_key, exc)
        return None, None
    if row is None:
        return None, None

    now = _utc_now()
    expires_at = _normalize_dt(row.expires_at)
    if expires_at is not None and expires_at <= now:
        return None, None

    payload = row.payload if isinstance(row.payload, dict) else None
    generated_at = _normalize_dt(row.generated_at)
    age_sec: int | None = None
    if generated_at is not None:
        age_sec = max(0, int((now - generated_at).total_seconds()))
    return payload, age_sec


def write_snapshot(
    db: Session,
    *,
    snapshot_key: str,
    scope: str,
    payload: dict[str, Any],
    ttl_sec: int,
    event_key: str | None,
    team_key: str | None,
    source_version: str = "intel_snapshot_v1",
) -> models.IntelSnapshot:
    now = _utc_now()
    expires_at = now + timedelta(seconds=max(20, int(ttl_sec)))

    row = db.get(models.IntelSnapshot, snapshot_key)
    if row is None:
        row = models.IntelSnapshot(
            snapshot_key=snapshot_key,
            scope=scope,
            event_key=event_key,
            team_key=team_key,
            payload=payload,
            source_version=source_version,
            generated_at=now,
            expires_at=expires_at,
        )
        db.add(row)
    else:
        row.scope = scope
        row.event_key = event_key
        row.team_key = team_key
        row.payload = payload
        row.source_version = source_version
        row.generated_at = now
        row.expires_at = expires_at

    return row


def cleanup_expired_snapshots(db: Session) -> int:
    now = _utc_now()
    try:
        deleted = (
            db.query(models.IntelSnapshot)
            .filter(models.IntelSnapshot.expires_at.isnot(None))
            .filter(models.IntelSnapshot.expires_at < now)
            .delete(synchronize_session=False)
        )
        return int(deleted or 0)
    except Exception as exc:
        db.rollback()
        logger.warning("intel snapshot cleanup failed: %s", exc)
        return 0


def refresh_hot_intel_snapshots() -> dict[str, Any]:
    started_perf = time.perf_counter()
    if not bool(settings.intel_snapshot_enabled):
        return {
            "ok": True,
            "enabled": False,
            "reason": "intel_snapshot_disabled",
        }

    if not bool(settings.intel_snapshot_refresh_enabled):
        return {
            "ok": True,
            "enabled": False,
            "reason": "intel_snapshot_refresh_disabled",
        }

    from app.services.intel.builders import (
        build_event_teams_intel_payload,
        build_team_intel_payload,
    )
    from app.services.intel.helpers import (
        EVENT_INTEL_CACHE_TTL_SEC,
        TEAM_INTEL_CACHE_TTL_SEC,
        _cache_key,
        _event_intel_cache_token,
        _team_intel_cache_token,
    )

    configured_event_limit = max(1, min(int(settings.intel_snapshot_max_events_per_run), 25))
    min_event_limit = max(1, min(int(settings.intel_snapshot_min_events_per_run), configured_event_limit))
    event_limit = configured_event_limit
    configured_team_limit = int(settings.intel_snapshot_max_teams_per_event)
    team_limit = min(configured_team_limit, 200) if configured_team_limit > 0 else 0
    interval_sec = max(60, int(settings.intel_snapshot_refresh_interval_minutes) * 60)
    target_ratio = max(0.3, min(0.95, float(settings.intel_snapshot_target_runtime_ratio)))
    runtime_budget_sec = max(20.0, interval_sec * target_ratio)

    db = SessionLocal()
    processed_events = 0
    team_snapshots = 0
    event_snapshots = 0
    missing_fields_backfilled = 0
    events_skipped_due_budget = 0
    timed_out = False
    per_event_runtime_sec: list[float] = []
    errors: list[str] = []

    try:
        # Prioritize events with recent match activity and non-empty team rosters.
        event_rows = (
            db.query(
                models.Event.event_key,
                models.Event.year,
                func.max(models.Match.time).label("last_match_time"),
                func.count(models.EventTeam.team_key).label("team_count"),
            )
            .outerjoin(models.Match, models.Match.event_key == models.Event.event_key)
            .outerjoin(models.EventTeam, models.EventTeam.event_key == models.Event.event_key)
            .group_by(models.Event.event_key, models.Event.year)
            .order_by(
                func.coalesce(func.max(models.Match.time), 0).desc(),
                models.Event.year.desc(),
                models.Event.event_key.asc(),
            )
            .limit(event_limit)
            .all()
        )

        for event_key, event_year, _, team_count in event_rows:
            elapsed_before_event = time.perf_counter() - started_perf
            if elapsed_before_event >= runtime_budget_sec and processed_events >= min_event_limit:
                timed_out = True
                events_skipped_due_budget += 1
                break

            event_started_perf = time.perf_counter()
            if int(team_count or 0) <= 0:
                continue
            processed_events += 1
            normalized_event_key = str(event_key).strip().lower()
            preferred_year = int(event_year) if isinstance(event_year, int) else CURRENT_SEASON_YEAR
            fallback_year = max(2015, preferred_year - 1)

            try:
                event_payload = build_event_teams_intel_payload(
                    db=db,
                    event_key=normalized_event_key,
                    include_tba=True,
                    include_statbotics=True,
                    auto_heal_ratings=True,
                    include_season_fallback=True,
                    include_rating_details=False,
                    include_rating_signals=False,
                )
                event_token = _event_intel_cache_token(
                    event_key=normalized_event_key,
                    include_tba=True,
                    include_statbotics=True,
                    auto_heal_ratings=True,
                    include_season_fallback=True,
                    include_rating_details=False,
                    include_rating_signals=False,
                )
                event_cache_key = _cache_key("event", event_token)
                event_payload_to_store = event_payload
                if bool(settings.intel_snapshot_backfill_missing_fields_only):
                    existing_event_snapshot = db.get(models.IntelSnapshot, event_cache_key)
                    if existing_event_snapshot is not None and isinstance(existing_event_snapshot.payload, dict):
                        event_payload_to_store, event_backfilled = _merge_missing_fields_only(
                            existing_event_snapshot.payload,
                            event_payload,
                        )
                        missing_fields_backfilled += int(event_backfilled)
                write_snapshot(
                    db,
                    snapshot_key=event_cache_key,
                    scope="event",
                    payload=event_payload_to_store,
                    ttl_sec=int(EVENT_INTEL_CACHE_TTL_SEC),
                    event_key=normalized_event_key,
                    team_key=None,
                )
                event_snapshots += 1

                teams = event_payload.get("teams") if isinstance(event_payload, dict) else []
                team_keys = [
                    str(item.get("team_key")).strip().lower()
                    for item in (teams if isinstance(teams, list) else [])
                    if isinstance(item, dict) and isinstance(item.get("team_key"), str)
                ]
                if team_limit > 0:
                    team_keys = team_keys[:team_limit]

                for team_key in team_keys:
                    team_payload = build_team_intel_payload(
                        db=db,
                        team_key=team_key,
                        event_key=normalized_event_key,
                        preferred_year=preferred_year,
                        fallback_year=fallback_year,
                        include_tba=True,
                        include_statbotics=True,
                        allow_season_fallback=True,
                        auto_heal_ratings=True,
                    )
                    team_token = _team_intel_cache_token(
                        team_key=team_key,
                        event_key=normalized_event_key,
                        preferred_year=preferred_year,
                        fallback_year=fallback_year,
                        include_tba=True,
                        include_statbotics=True,
                        allow_season_fallback=True,
                        auto_heal_ratings=True,
                    )
                    team_cache_key = _cache_key("team", team_token)
                    team_payload_to_store = team_payload
                    if bool(settings.intel_snapshot_backfill_missing_fields_only):
                        existing_team_snapshot = db.get(models.IntelSnapshot, team_cache_key)
                        if existing_team_snapshot is not None and isinstance(existing_team_snapshot.payload, dict):
                            team_payload_to_store, team_backfilled = _merge_missing_fields_only(
                                existing_team_snapshot.payload,
                                team_payload,
                            )
                            missing_fields_backfilled += int(team_backfilled)
                    write_snapshot(
                        db,
                        snapshot_key=team_cache_key,
                        scope="team",
                        payload=team_payload_to_store,
                        ttl_sec=int(TEAM_INTEL_CACHE_TTL_SEC),
                        event_key=normalized_event_key,
                        team_key=team_key,
                    )
                    team_snapshots += 1

                db.commit()
            except Exception as exc:  # pragma: no cover - defensive scheduler path
                db.rollback()
                message = f"{normalized_event_key}: {exc}"
                errors.append(message)
                logger.warning("intel snapshot refresh failed for %s: %s", normalized_event_key, exc)
            finally:
                per_event_runtime_sec.append(max(0.0, time.perf_counter() - event_started_perf))

        deleted_expired = cleanup_expired_snapshots(db)
        if deleted_expired > 0:
            db.commit()

        runtime_sec = max(0.0, time.perf_counter() - started_perf)
        if runtime_sec > runtime_budget_sec and processed_events >= min_event_limit:
            timed_out = True
        avg_event_runtime_sec = (
            (sum(per_event_runtime_sec) / len(per_event_runtime_sec))
            if per_event_runtime_sec
            else None
        )
        recommended_event_limit = configured_event_limit
        if isinstance(avg_event_runtime_sec, float) and avg_event_runtime_sec > 0.0:
            projected = int(max(min_event_limit, min(25, runtime_budget_sec / avg_event_runtime_sec)))
            recommended_event_limit = max(min_event_limit, min(configured_event_limit, projected))

        return {
            "ok": True,
            "enabled": True,
            "processed_events": int(processed_events),
            "configured_event_limit": int(configured_event_limit),
            "effective_event_limit": int(event_limit),
            "recommended_event_limit_next_run": int(recommended_event_limit),
            "timed_out": bool(timed_out),
            "events_skipped_due_budget": int(events_skipped_due_budget),
            "event_snapshots_written": int(event_snapshots),
            "team_snapshots_written": int(team_snapshots),
            "missing_fields_backfilled": int(missing_fields_backfilled),
            "expired_deleted": int(deleted_expired),
            "runtime_sec": round(runtime_sec, 3),
            "runtime_budget_sec": round(runtime_budget_sec, 3),
            "runtime_budget_used_0_1": round(min(1.0, runtime_sec / max(1e-6, runtime_budget_sec)), 4),
            "avg_event_runtime_sec": round(float(avg_event_runtime_sec), 3)
            if isinstance(avg_event_runtime_sec, float)
            else None,
            "errors": errors,
        }
    finally:
        db.close()
