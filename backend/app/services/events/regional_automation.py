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
from app.services.events.pipeline import (
    ANALYSIS_VERSION,
    get_queue,
    run_event_pipeline as _run_event_pipeline,
)
from app.services.utils import (
    automation_redis_key as _automation_redis_key,
    decode_redis_float as _decode_redis_float,
)
from app.tba.client import TBAClient

logger = logging.getLogger(__name__)

AUTOMATION_LOCK_TTL_SEC = 15 * 60

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

def run_regional_post_event_breakdowns(
    *,
    season: int,
    db: Session,
    force_analysis: bool = False,
    include_all_events: bool = False,
    include_out_of_region_events_for_in_region_teams: bool = True,
    include_ended_today: bool = False,
    allow_previous_season_fallback: bool | None = None,
    all_matches_in_region_events: bool = False,
    auto_calibrate_missing: bool = True,
    auto_calibration_overwrite_existing: bool = False,
    auto_calibration_refresh_video: bool = False,
    auto_calibration_sample_count: int = 18,
    auto_calibration_min_inliers: int = 4,
    auto_calibration_ransac_reproj_threshold_px: float = 3.0,
    auto_calibration_focus_time_sec: float | None = None,
    max_events: int = 300,
    max_teams: int = 1000,
    max_matches_per_event: int = 42,
    max_new_jobs_per_tick: int = 220,
    max_queue_pending_jobs: int | None = None,
    clone_event_calibration: bool = True,
    require_video: bool = True,
    require_calibration: bool = False,
    run_post_compute: bool = True,
    synergy_model_version: str = "",
    quality_threshold: float = 0.35,
) -> dict[str, Any]:
    if season < 2015 or season > 2100:
        raise RegionalAutomationError(400, "season must be between 2015 and 2100")
    if quality_threshold < 0.0 or quality_threshold > 1.0:
        raise RegionalAutomationError(400, "quality_threshold must be between 0 and 1")
    if auto_calibration_sample_count < 1 or auto_calibration_sample_count > 80:
        raise RegionalAutomationError(400, "auto_calibration_sample_count must be between 1 and 80")
    if auto_calibration_min_inliers < 4 or auto_calibration_min_inliers > 64:
        raise RegionalAutomationError(400, "auto_calibration_min_inliers must be between 4 and 64")
    if (
        auto_calibration_ransac_reproj_threshold_px <= 0.1
        or auto_calibration_ransac_reproj_threshold_px > 25.0
    ):
        raise RegionalAutomationError(
            400,
            "auto_calibration_ransac_reproj_threshold_px must be > 0.1 and <= 25.0",
        )
    if auto_calibration_focus_time_sec is not None and auto_calibration_focus_time_sec < 0:
        raise RegionalAutomationError(400, "auto_calibration_focus_time_sec must be >= 0")

    tba = TBAClient()
    today_utc = datetime.now(timezone.utc).date()
    queue = get_queue()

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

    selected_events = completed_events[: max(1, min(max_events, 1000))]
    per_event_schedule_cap = max(1, min(int(max_matches_per_event), 5000))
    remaining_job_budget = max(1, min(int(max_new_jobs_per_tick), 20000))
    queue_pending_cap = (
        int(max_queue_pending_jobs)
        if max_queue_pending_jobs is not None
        else int(settings.analysis_queue_max_pending_jobs)
    )
    queue_pending_cap = max(10, min(queue_pending_cap, 20000))

    event_results: list[dict] = []
    for event in selected_events:
        if remaining_job_budget <= 0:
            break
        event_key = event.get("key")
        if not isinstance(event_key, str) or not event_key:
            continue
        event_is_in_region = _is_in_region_event_payload(event)
        if include_all_events:
            allowed_team_keys = None
        else:
            allowed_team_keys = None if (all_matches_in_region_events and event_is_in_region) else in_region_team_keys
        event_new_jobs_cap = max(1, min(per_event_schedule_cap, remaining_job_budget))
        event_result = _run_event_pipeline(
            db,
            queue,
            event_key=event_key,
            force_analysis=force_analysis,
            allowed_team_keys=allowed_team_keys,
            clone_event_calibration=clone_event_calibration,
            require_video=require_video,
            require_calibration=require_calibration,
            run_post_compute=run_post_compute,
            synergy_model_version=synergy_model_version,
            quality_threshold=quality_threshold,
            auto_calibrate_missing=auto_calibrate_missing,
            auto_calibration_overwrite_existing=auto_calibration_overwrite_existing,
            auto_calibration_refresh_video=auto_calibration_refresh_video,
            auto_calibration_sample_count=int(auto_calibration_sample_count),
            auto_calibration_min_inliers=int(auto_calibration_min_inliers),
            auto_calibration_ransac_reproj_threshold_px=float(auto_calibration_ransac_reproj_threshold_px),
            auto_calibration_focus_time_sec=auto_calibration_focus_time_sec,
            max_new_jobs=event_new_jobs_cap,
            max_pending_jobs=queue_pending_cap,
        )
        remaining_job_budget = max(0, remaining_job_budget - int(event_result.get("scheduled") or 0))
        if include_all_events:
            event_result["target_scope"] = "all_teams_in_event"
        elif all_matches_in_region_events and event_is_in_region:
            event_result["target_scope"] = "all_matches_in_region_event"
        if not include_all_events and event_result.get("status") == "no_target_teams_detected":
            event_result["status"] = "no_in_region_teams_detected"
            event_result["in_region_teams_in_event"] = 0
        elif not include_all_events:
            event_result["in_region_teams_in_event"] = int(event_result.get("target_teams_in_event") or 0)
        event_result["queue_caps"] = {
            "max_new_jobs_for_event": int(event_new_jobs_cap),
            "max_pending_jobs": int(queue_pending_cap),
        }
        event_results.append(event_result)

    return {
        "ok": True,
        "season": effective_season,
        "requested_season": requested_season,
        "effective_season": effective_season,
        "season_fallback_used": season_fallback_used,
        "fallback_from_season": fallback_from_season,
        "analysis_version": ANALYSIS_VERSION,
        "model_version": synergy_model_version,
        "quality_threshold": quality_threshold,
        "include_all_events": bool(include_all_events),
        "in_region_team_pool_size": len(in_region_team_keys),
        "candidate_event_count": len(candidate_events),
        "completed_event_count": len(completed_events),
        "processed_event_count": len(event_results),
        "queue_caps": {
            "max_matches_per_event": int(per_event_schedule_cap),
            "max_new_jobs_per_tick": int(max_new_jobs_per_tick),
            "remaining_job_budget": int(remaining_job_budget),
            "max_pending_jobs": int(queue_pending_cap),
        },
        "events": event_results,
    }

def run_regional_automation_tick(
    *,
    season: int,
    db: Session,
    force_tick: bool = False,
    min_interval_minutes: int | None = None,
    force_analysis: bool = False,
    include_all_events: bool | None = None,
    include_out_of_region_events_for_in_region_teams: bool | None = None,
    include_ended_today: bool | None = None,
    allow_previous_season_fallback: bool | None = None,
    all_matches_in_region_events: bool | None = None,
    auto_calibrate_missing: bool | None = None,
    auto_calibration_overwrite_existing: bool | None = None,
    auto_calibration_refresh_video: bool | None = None,
    auto_calibration_sample_count: int | None = None,
    auto_calibration_min_inliers: int | None = None,
    auto_calibration_ransac_reproj_threshold_px: float | None = None,
    auto_calibration_focus_time_sec: float | None = None,
    max_events: int | None = None,
    max_teams: int | None = None,
    max_matches_per_event: int | None = None,
    max_new_jobs_per_tick: int | None = None,
    max_queue_pending_jobs: int | None = None,
    clone_event_calibration: bool | None = None,
    require_video: bool | None = None,
    require_calibration: bool | None = None,
    run_post_compute: bool | None = None,
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
        default_focus_time = (
            float(settings.automation_regional_auto_calibration_focus_time_sec)
            if float(settings.automation_regional_auto_calibration_focus_time_sec) > 0
            else None
        )

        run_result = run_regional_post_event_breakdowns(
            season=season,
            force_analysis=force_analysis,
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
            all_matches_in_region_events=(
                False if all_matches_in_region_events is None else all_matches_in_region_events
            ),
            auto_calibrate_missing=(
                bool(settings.automation_regional_auto_calibrate_missing)
                if auto_calibrate_missing is None
                else auto_calibrate_missing
            ),
            auto_calibration_overwrite_existing=(
                bool(settings.automation_regional_auto_calibration_overwrite_existing)
                if auto_calibration_overwrite_existing is None
                else auto_calibration_overwrite_existing
            ),
            auto_calibration_refresh_video=(
                bool(settings.automation_regional_auto_calibration_refresh_video)
                if auto_calibration_refresh_video is None
                else auto_calibration_refresh_video
            ),
            auto_calibration_sample_count=(
                int(settings.automation_regional_auto_calibration_sample_count)
                if auto_calibration_sample_count is None
                else int(auto_calibration_sample_count)
            ),
            auto_calibration_min_inliers=(
                int(settings.automation_regional_auto_calibration_min_inliers)
                if auto_calibration_min_inliers is None
                else int(auto_calibration_min_inliers)
            ),
            auto_calibration_ransac_reproj_threshold_px=(
                float(settings.automation_regional_auto_calibration_ransac_reproj_threshold_px)
                if auto_calibration_ransac_reproj_threshold_px is None
                else float(auto_calibration_ransac_reproj_threshold_px)
            ),
            auto_calibration_focus_time_sec=(
                default_focus_time if auto_calibration_focus_time_sec is None else auto_calibration_focus_time_sec
            ),
            max_events=(
                int(settings.automation_regional_max_events) if max_events is None else int(max_events)
            ),
            max_teams=(
                int(settings.automation_regional_max_teams) if max_teams is None else int(max_teams)
            ),
            max_matches_per_event=(
                int(settings.automation_regional_max_matches_per_event)
                if max_matches_per_event is None
                else int(max_matches_per_event)
            ),
            max_new_jobs_per_tick=(
                int(settings.automation_regional_max_new_jobs_per_tick)
                if max_new_jobs_per_tick is None
                else int(max_new_jobs_per_tick)
            ),
            max_queue_pending_jobs=(
                int(settings.analysis_queue_max_pending_jobs)
                if max_queue_pending_jobs is None
                else int(max_queue_pending_jobs)
            ),
            clone_event_calibration=(
                bool(settings.automation_regional_clone_event_calibration)
                if clone_event_calibration is None
                else clone_event_calibration
            ),
            require_video=(
                bool(settings.automation_regional_require_video) if require_video is None else require_video
            ),
            require_calibration=(
                bool(settings.automation_regional_require_calibration)
                if require_calibration is None
                else require_calibration
            ),
            run_post_compute=(
                bool(settings.automation_regional_run_post_compute)
                if run_post_compute is None
                else run_post_compute
            ),
            synergy_model_version=synergy_model_version,
            quality_threshold=quality_threshold,
            db=db,
        )

        events_payload = run_result.get("events") if isinstance(run_result, dict) else []
        events_payload = events_payload if isinstance(events_payload, list) else []
        total_scheduled = 0
        total_skipped = 0
        total_blocked = 0
        blocked_reason_counts: dict[str, int] = {}
        for event_payload in events_payload:
            if not isinstance(event_payload, dict):
                continue
            total_scheduled += int(event_payload.get("scheduled") or 0)
            total_skipped += int(event_payload.get("skipped") or 0)
            total_blocked += int(event_payload.get("blocked") or 0)
            blocked_rows = event_payload.get("blocked_matches")
            if not isinstance(blocked_rows, list):
                continue
            for blocked in blocked_rows:
                if not isinstance(blocked, dict):
                    continue
                reasons = blocked.get("reasons")
                if not isinstance(reasons, list):
                    continue
                for reason in reasons:
                    token = str(reason or "").strip().lower()
                    if not token:
                        continue
                    blocked_reason_counts[token] = int(blocked_reason_counts.get(token, 0)) + 1

        finished_ts = datetime.now(timezone.utc).timestamp()
        redis_conn.set(last_run_key, str(finished_ts))
        summary = {
            "requested_season": season,
            "effective_season": run_result.get("effective_season") if isinstance(run_result, dict) else None,
            "season_fallback_used": run_result.get("season_fallback_used") if isinstance(run_result, dict) else None,
            "processed_event_count": run_result.get("processed_event_count") if isinstance(run_result, dict) else None,
            "completed_event_count": run_result.get("completed_event_count") if isinstance(run_result, dict) else None,
            "totals": {
                "scheduled_matches": int(total_scheduled),
                "skipped_matches": int(total_skipped),
                "blocked_matches": int(total_blocked),
            },
            "blocked_reason_counts": blocked_reason_counts,
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
