# Regional automation services used by API routes and scheduled jobs.

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

import redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import sanitize_external_error
from app.services.events.pipeline import post_compute_event, refresh_event, train_shadow_models_after_refresh
from app.services.ml.synergy import precompute_season_synergy
from app.services.utils import (
    automation_redis_key as _automation_redis_key,
    decode_redis_float as _decode_redis_float,
)
from app.tba.client import TBAClient

logger = logging.getLogger(__name__)

AUTOMATION_LOCK_TTL_SEC = 15 * 60
EVENT_REFRESHED_KEY_PREFIX = "automation:regional:event_refreshed:"

class RegionalAutomationError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = detail

_COUNTRY_ALIASES: dict[str, str] = {
    "usa": "usa",
    "us": "usa",
    "u.s.": "usa",
    "u.s.a.": "usa",
    "united states": "usa",
    "united states of america": "usa",
    "america": "usa",
    "canada": "canada",
    "ca": "canada",
    "can": "canada",
}

def _normalize_country_value(country: str | None) -> str:
    value = (country or "").strip().lower()
    return _COUNTRY_ALIASES.get(value, value)

def _supported_region_countries() -> set[str]:
    raw = str(getattr(settings, "automation_regional_countries", "USA,Canada") or "")
    tokens = {
        _normalize_country_value(token)
        for token in raw.split(",")
        if token.strip()
    }
    return {token for token in tokens if token} or {"usa", "canada"}

def _is_in_region_event_payload(event_payload: dict) -> bool:
    return _normalize_country_value(event_payload.get("country")) in _supported_region_countries()

def _is_in_region_team_payload(team_payload: dict) -> bool:
    return _normalize_country_value(team_payload.get("country")) in _supported_region_countries()

def _is_event_completed(event_payload: dict, today_utc: date, include_ended_today: bool) -> bool:
    end_date_raw = event_payload.get("end_date")
    if isinstance(end_date_raw, str):
        try:
            end_date = date.fromisoformat(end_date_raw)
            return end_date <= today_utc if include_ended_today else end_date < today_utc
        except ValueError:
            logger.debug("Ignoring invalid TBA event end_date=%r", end_date_raw)

    year = event_payload.get("year")
    if isinstance(year, int):
        if year < today_utc.year:
            return True
        if year > today_utc.year:
            return False
    return False

def _event_refreshed_at(redis_conn: redis.Redis, event_keys: list[str]) -> dict[str, float]:
    if not event_keys:
        return {}
    values = redis_conn.mget([f"{EVENT_REFRESHED_KEY_PREFIX}{key}" for key in event_keys])
    return {
        key: parsed
        for key, raw in zip(event_keys, values)
        if (parsed := _decode_redis_float(raw)) is not None
    }


def _mark_event_refreshed(event_key: str) -> None:
    try:
        redis.from_url(settings.redis_url).set(
            f"{EVENT_REFRESHED_KEY_PREFIX}{event_key}", str(datetime.now(timezone.utc).timestamp())
        )
    except redis.RedisError:
        logger.warning("regional_automation.mark_refreshed_failed event=%s", event_key)


def _events_due_for_refresh(events: list[dict], today_utc: date) -> tuple[list[dict], int]:
    # Refresh what can still change: never-refreshed events and ones that ended in the
    # last few days. Settled events get a capped re-check, longest-unchecked first, so
    # a full pass spreads over several ticks instead of re-ingesting everything each time.
    settle_days = max(0, int(settings.automation_regional_settle_days))
    keys = [str(event.get("key")) for event in events if isinstance(event.get("key"), str)]
    try:
        refreshed_at = _event_refreshed_at(redis.from_url(settings.redis_url), keys)
    except redis.RedisError:
        logger.warning("regional_automation.refresh_state_unavailable; refreshing all")
        return events, 0

    due: list[dict] = []
    settled: list[tuple[float, dict]] = []
    for event in events:
        key = event.get("key")
        if not isinstance(key, str):
            continue
        last = refreshed_at.get(key)
        end_raw = event.get("end_date")
        try:
            ended = date.fromisoformat(end_raw) if isinstance(end_raw, str) else None
        except ValueError:
            ended = None
        if last is None or ended is None or (today_utc - ended).days <= settle_days:
            due.append(event)
        else:
            settled.append((last, event))
    settled.sort(key=lambda item: item[0])
    recheck_cap = max(0, int(settings.automation_regional_max_rechecks_per_tick))
    rechecks = [event for _, event in settled[:recheck_cap]]
    return due + rechecks, len(settled) - len(rechecks)


def run_regional_post_event_breakdowns(
    *,
    season: int,
    db: Session,
    include_all_events: bool = False,
    include_out_of_region_events_for_in_region_teams: bool = True,
    include_ended_today: bool = False,
    allow_previous_season_fallback: bool | None = None,
    max_events: int = 300,
    max_teams: int = 1000,
    run_post_compute: bool = True,
    refresh_all: bool = False,
    synergy_model_version: str = "",
    quality_threshold: float = 0.35,
) -> dict[str, Any]:
    if season < 2015 or season > 2100:
        raise RegionalAutomationError(400, "season must be between 2015 and 2100")
    if quality_threshold < 0.0 or quality_threshold > 1.0:
        raise RegionalAutomationError(400, "quality_threshold must be between 0 and 1")

    tba = TBAClient()
    today_utc = datetime.now(timezone.utc).date()

    def collect_scope_for_season(target_season: int) -> tuple[set[str], dict[str, dict], list[dict]]:
        try:
            season_events = tba.events(target_season)
        except Exception as exc:
            raise RegionalAutomationError(
                502,
                f"Failed to fetch season events from TBA for {target_season}: {exc}",
            ) from exc
        if not isinstance(season_events, list):
            raise RegionalAutomationError(
                502,
                f"Unexpected TBA events payload for season {target_season}",
            )

        if include_all_events:
            candidate_events_local: dict[str, dict] = {}
            for event in season_events:
                event_key = event.get("key")
                if isinstance(event_key, str) and event_key:
                    candidate_events_local[event_key] = event
            completed_events_local: list[dict] = []
            for event in candidate_events_local.values():
                if _is_event_completed(event, today_utc, include_ended_today):
                    completed_events_local.append(event)
            completed_events_local.sort(
                key=lambda payload: (
                    payload.get("end_date") or "",
                    payload.get("key") or "",
                )
            )
            return set(), candidate_events_local, completed_events_local

        in_region_events = [
            event
            for event in season_events
            if _is_in_region_event_payload(event)
        ]

        in_region_team_keys_local: set[str] = set()
        for event in in_region_events:
            event_key = event.get("key")
            if not isinstance(event_key, str) or not event_key:
                continue
            try:
                teams = tba.event_teams(event_key)
            except Exception as exc:
                logger.warning(
                    "Regional automation failed to fetch teams for event %s: %s",
                    event_key,
                    exc,
                )
                continue
            for team in teams:
                team_key = team.get("key")
                if isinstance(team_key, str) and team_key and _is_in_region_team_payload(team):
                    in_region_team_keys_local.add(team_key)
            if len(in_region_team_keys_local) >= max_teams:
                break

        candidate_events_local: dict[str, dict] = {}
        for event in in_region_events:
            event_key = event.get("key")
            if isinstance(event_key, str) and event_key:
                candidate_events_local[event_key] = event

        if include_out_of_region_events_for_in_region_teams:
            for team_key in sorted(in_region_team_keys_local)[:max_teams]:
                try:
                    team_events = tba.team_events(team_key, target_season)
                except Exception as exc:
                    logger.warning(
                        "Regional automation failed to fetch season events for team %s: %s",
                        team_key,
                        exc,
                    )
                    continue
                if not isinstance(team_events, list):
                    continue
                for event in team_events:
                    event_key = event.get("key")
                    if isinstance(event_key, str) and event_key:
                        candidate_events_local.setdefault(event_key, event)

        completed_events_local: list[dict] = []
        for event in candidate_events_local.values():
            if _is_event_completed(event, today_utc, include_ended_today):
                completed_events_local.append(event)
        completed_events_local.sort(
            key=lambda payload: (
                payload.get("end_date") or "",
                payload.get("key") or "",
            )
        )
        return in_region_team_keys_local, candidate_events_local, completed_events_local

    requested_season = season
    effective_season = requested_season
    season_fallback_used = False
    fallback_from_season: int | None = None
    allow_season_fallback = (
        settings.automation_regional_season_fallback_enabled
        if allow_previous_season_fallback is None
        else allow_previous_season_fallback
    )

    in_region_team_keys, candidate_events, completed_events = collect_scope_for_season(requested_season)
    if allow_season_fallback and not completed_events and requested_season > 2015:
        previous_season = requested_season - 1
        prev_team_keys, prev_candidate_events, prev_completed_events = collect_scope_for_season(previous_season)
        if prev_completed_events:
            effective_season = previous_season
            season_fallback_used = True
            fallback_from_season = requested_season
            in_region_team_keys = prev_team_keys
            candidate_events = prev_candidate_events
            completed_events = prev_completed_events

    candidate_events = completed_events[: max(1, min(max_events, 1000))]
    selected_events, skipped_settled = (
        (candidate_events, 0) if refresh_all else _events_due_for_refresh(candidate_events, today_utc)
    )

    # Ingest every event first, then build the season-wide synergy once, then rebuild
    # each event on top of it. Rebuilding per event recomputed the whole season's
    # synergy once per event (299 times in a full re-ingest).
    event_results: list[dict] = []
    for event in selected_events:
        event_key = event.get("key")
        if not isinstance(event_key, str) or not event_key:
            continue
        event_result = refresh_event(
            db,
            event_key=event_key,
            run_post_compute=False,
            train_ml=False,
            synergy_model_version=synergy_model_version,
            quality_threshold=quality_threshold,
        )
        event_result["in_region"] = _is_in_region_event_payload(event)
        event_results.append(event_result)

    processed = [row for row in event_results if row.get("status") == "processed"]
    season_synergy: dict[int, dict] = {}
    if run_post_compute:
        for season_year in sorted({int(str(row["event_key"])[:4]) for row in processed}):
            try:
                season_synergy[season_year] = precompute_season_synergy(
                    db,
                    season_year,
                    model_version=synergy_model_version,
                    quality_threshold=quality_threshold,
                )
            except Exception as exc:
                # Each event then rebuilds its own season data, as before.
                season_synergy[season_year] = {"ok": False, "detail": sanitize_external_error(exc, default="Season synergy failed.")}
    for row in processed:
        event_key = str(row["event_key"])
        if run_post_compute:
            row["post_compute"] = {"ran": True, **post_compute_event(
                db,
                event_key=event_key,
                train_ml=False,
                synergy_model_version=synergy_model_version,
                quality_threshold=quality_threshold,
                season_ready=bool(season_synergy.get(int(event_key[:4]), {}).get("ok")),
            )}
        _mark_event_refreshed(event_key)

    # One training pass per tick: the shadow models learn from the whole season, not one event.
    ml_shadow = (
        train_shadow_models_after_refresh(db, event_key=str(event_results[-1]["event_key"]))
        if run_post_compute and event_results
        else {"triggered": False, "detail": "no_events_refreshed"}
    )
    ml_shadow.pop("ratings_result", None)

    return {
        "ok": True,
        "season": effective_season,
        "requested_season": requested_season,
        "effective_season": effective_season,
        "season_fallback_used": season_fallback_used,
        "fallback_from_season": fallback_from_season,
        "model_version": synergy_model_version,
        "quality_threshold": quality_threshold,
        "include_all_events": bool(include_all_events),
        "in_region_team_pool_size": len(in_region_team_keys),
        "candidate_event_count": len(candidate_events),
        "completed_event_count": len(completed_events),
        "processed_event_count": len(event_results),
        "skipped_settled_event_count": skipped_settled,
        "failed_event_count": sum(1 for row in event_results if row.get("status") != "processed"),
        "ml_shadow": ml_shadow,
        "events": event_results,
    }

def run_regional_automation_tick(
    *,
    season: int,
    db: Session,
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
    synergy_model_version: str = "",
    quality_threshold: float = 0.35,
) -> dict[str, Any]:
    # Run a single regional automation tick.
    #
    # Handles interval gating and Redis locking around the regional event
    # breakdown runner. This stays free of FastAPI dependencies so scheduled
    # jobs can call it without importing the API layer.
    if season < 2015 or season > 2100:
        raise RegionalAutomationError(400, "season must be between 2015 and 2100")

    if not settings.automation_regional_enabled and not force_tick:
        return {
            "ok": True,
            "status": "disabled",
            "season": season,
            "detail": "Automation is disabled by AUTOMATION_REGIONAL_ENABLED.",
        }

    interval_minutes = int(min_interval_minutes or settings.automation_regional_interval_minutes)
    interval_seconds = max(60, interval_minutes * 60)
    now_ts = datetime.now(timezone.utc).timestamp()

    redis_conn = redis.from_url(settings.redis_url)
    last_run_key = _automation_redis_key("regional", season, "last_run_ts")
    last_result_key = _automation_redis_key("regional", season, "last_result")
    lock_key = _automation_redis_key("regional", season, "lock")

    last_run_ts = _decode_redis_float(redis_conn.get(last_run_key))
    seconds_since_last_run = None if last_run_ts is None else max(0.0, now_ts - last_run_ts)
    if (
        not force_tick
        and last_run_ts is not None
        and seconds_since_last_run is not None
        and seconds_since_last_run < interval_seconds
    ):
        next_run_ts = last_run_ts + interval_seconds
        return {
            "ok": True,
            "status": "skipped_interval",
            "season": season,
            "interval_minutes": interval_minutes,
            "seconds_since_last_run": round(seconds_since_last_run, 3),
            "next_run_earliest_at": datetime.fromtimestamp(next_run_ts, tz=timezone.utc).isoformat(),
        }

    lock_token = uuid.uuid4().hex
    lock_ttl = max(AUTOMATION_LOCK_TTL_SEC, interval_seconds)
    lock_acquired = bool(redis_conn.set(lock_key, lock_token, nx=True, ex=lock_ttl))
    if not lock_acquired:
        return {
            "ok": True,
            "status": "locked",
            "season": season,
            "detail": "Another automation tick is currently running.",
        }

    try:
        run_result = run_regional_post_event_breakdowns(
            season=season,
            include_all_events=(
                bool(settings.automation_regional_include_all_events)
                if include_all_events is None
                else include_all_events
            ),
            include_out_of_region_events_for_in_region_teams=(
                bool(settings.automation_regional_include_out_of_region_events)
                if include_out_of_region_events_for_in_region_teams is None
                else include_out_of_region_events_for_in_region_teams
            ),
            include_ended_today=(
                bool(settings.automation_regional_include_ended_today)
                if include_ended_today is None
                else include_ended_today
            ),
            allow_previous_season_fallback=(
                bool(settings.automation_regional_season_fallback_enabled)
                if allow_previous_season_fallback is None
                else allow_previous_season_fallback
            ),
            max_events=(
                int(settings.automation_regional_max_events) if max_events is None else int(max_events)
            ),
            max_teams=(
                int(settings.automation_regional_max_teams) if max_teams is None else int(max_teams)
            ),
            run_post_compute=(
                bool(settings.automation_regional_run_post_compute)
                if run_post_compute is None
                else run_post_compute
            ),
            refresh_all=refresh_all,
            synergy_model_version=synergy_model_version,
            quality_threshold=quality_threshold,
            db=db,
        )

        finished_ts = datetime.now(timezone.utc).timestamp()
        redis_conn.set(last_run_key, str(finished_ts))
        summary = {
            "requested_season": season,
            "effective_season": run_result.get("effective_season") if isinstance(run_result, dict) else None,
            "season_fallback_used": run_result.get("season_fallback_used") if isinstance(run_result, dict) else None,
            "processed_event_count": run_result.get("processed_event_count") if isinstance(run_result, dict) else None,
            "completed_event_count": run_result.get("completed_event_count") if isinstance(run_result, dict) else None,
            "failed_event_count": run_result.get("failed_event_count") if isinstance(run_result, dict) else None,
            "skipped_settled_event_count": (
                run_result.get("skipped_settled_event_count") if isinstance(run_result, dict) else None
            ),
            "finished_at": datetime.fromtimestamp(finished_ts, tz=timezone.utc).isoformat(),
        }
        redis_conn.set(last_result_key, json.dumps(summary))

        return {
            "ok": True,
            "status": "ran",
            "season": season,
            "interval_minutes": interval_minutes,
            "last_run_at": summary["finished_at"],
            "result": run_result,
        }
    finally:
        current_lock = redis_conn.get(lock_key)
        current_value = (
            current_lock.decode("utf-8", errors="ignore")
            if isinstance(current_lock, bytes)
            else str(current_lock or "")
        )
        if current_value == lock_token:
            redis_conn.delete(lock_key)
