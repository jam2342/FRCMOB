from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import logging
import math
import threading
import time
from typing import Any

from apscheduler.schedulers.base import STATE_RUNNING
from rq import Queue, Retry
from rq.job import Job
from rq.registry import DeferredJobRegistry, ScheduledJobRegistry, StartedJobRegistry
import redis

from app.core.config import settings
from app.db import models
from app.db.session import SessionLocal
from app.services.jobs import analyze_match, build_analysis_params_hash
from app.services.vision.perimeter_resolver import resolve_perimeter_type_for_event_profile
from app.services.ratings.model import recompute_event_ratings
from app.services.ml.synergy import precompute_event_synergy
from app.services.vision.video_extraction import VideoExtractionError, capture_live_stream_clip
from app.tba.client import TBAClient

logger = logging.getLogger(__name__)

ANALYSIS_JOB_TIMEOUT_SEC = max(120, int(getattr(settings, "analysis_job_timeout_sec", 3600)))
ANALYSIS_JOB_RESULT_TTL_SEC = max(300, int(getattr(settings, "analysis_job_result_ttl_sec", 86400)))
ANALYSIS_JOB_FAILURE_TTL_SEC = max(600, int(getattr(settings, "analysis_job_failure_ttl_sec", 604800)))
ANALYSIS_JOB_RETRY_MAX = max(0, int(getattr(settings, "analysis_job_retry_max", 2)))


@dataclass
class LiveMonitorSession:
    event_key: str
    interval_sec: int
    clip_duration_sec: int
    stream_url: str | None
    auto_stream_discovery: bool
    created_at_unix: float
    last_tick_started_at_unix: float | None = None
    last_tick_completed_at_unix: float | None = None
    tick_count: int = 0
    running: bool = True
    last_result: dict[str, Any] = field(default_factory=dict)
    last_enqueue_unix_by_match: dict[str, float] = field(default_factory=dict)


_sessions_lock = threading.Lock()
_sessions_by_event: dict[str, LiveMonitorSession] = {}
_auto_regional_lock = threading.Lock()
_auto_regional_managed_events: set[str] = set()
_auto_regional_last_run_unix: float | None = None
_auto_regional_last_result: dict[str, Any] = {}


def _now_unix() -> float:
    return time.time()


def _iso_or_none(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()


def _parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


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


def _normalize_countries(value: Any) -> set[str]:
    tokens = {
        _normalize_country_value(token)
        for token in str(value or "").split(",")
        if token.strip()
    }
    return {token for token in tokens if token} or {"usa", "canada"}


def _event_is_in_region_scope(event_payload: dict[str, Any], allowed_countries: set[str]) -> bool:
    if not allowed_countries:
        return True
    return _normalize_country_value(event_payload.get("country")) in allowed_countries


def _event_is_active_today(event_payload: dict[str, Any], now_utc: datetime) -> bool:
    start_date = _parse_iso_date(event_payload.get("start_date"))
    end_date = _parse_iso_date(event_payload.get("end_date")) or start_date
    if start_date is None or end_date is None:
        return False
    today = now_utc.date()
    return start_date <= today <= end_date


def _analysis_job_id(match_key: str, analysis_version: str, params_hash: str, calibration_id: int) -> str:
    raw = f"{match_key}|{analysis_version}|{params_hash}|{calibration_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"live-analyze:{match_key}:{digest}"


def _job_is_pending_in_queue(queue: Queue, job_id: str, status: str) -> bool:
    connection = queue.connection
    normalized = (status or "").lower()
    if normalized == "queued":
        try:
            return connection.lpos(queue.key, job_id) is not None
        except Exception:
            return job_id in queue.job_ids
    if normalized == "started":
        return connection.zscore(StartedJobRegistry(queue=queue).key, job_id) is not None
    if normalized == "deferred":
        return connection.zscore(DeferredJobRegistry(queue=queue).key, job_id) is not None
    if normalized == "scheduled":
        return connection.zscore(ScheduledJobRegistry(queue=queue).key, job_id) is not None
    return False


def _queue() -> Queue:
    connection = redis.from_url(settings.redis_url)
    return Queue("default", connection=connection)


def _enqueue_live_analysis(
    queue: Queue,
    *,
    match_key: str,
    analysis_version: str,
    params_hash: str,
    calibration_id: int,
) -> tuple[bool, str, str]:
    job_id = _analysis_job_id(
        match_key=match_key,
        analysis_version=analysis_version,
        params_hash=params_hash,
        calibration_id=calibration_id,
    )
    existing_job = queue.fetch_job(job_id)
    if existing_job is not None:
        status = (existing_job.get_status() or "unknown").lower()
        if status in {"queued", "started", "deferred", "scheduled"} and _job_is_pending_in_queue(queue, job_id, status):
            return False, job_id, status
        with suppress(Exception):
            existing_job.delete(delete_dependents=False)

    retry_cfg = Retry(max=ANALYSIS_JOB_RETRY_MAX, interval=[45, 180]) if ANALYSIS_JOB_RETRY_MAX > 0 else None
    job: Job = queue.enqueue(
        analyze_match,
        match_key,
        analysis_version,
        params_hash,
        calibration_id,
        job_id=job_id,
        job_timeout=ANALYSIS_JOB_TIMEOUT_SEC,
        result_ttl=ANALYSIS_JOB_RESULT_TTL_SEC,
        failure_ttl=ANALYSIS_JOB_FAILURE_TTL_SEC,
        retry=retry_cfg,
    )
    return True, job_id, str(job.get_status() or "queued")


def _normalize_stream_url(webcast_type: str, channel: str) -> str | None:
    stream_type = str(webcast_type or "").strip().lower()
    value = str(channel or "").strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if stream_type == "youtube":
        return f"https://www.youtube.com/watch?v={value}"
    if stream_type == "twitch":
        return f"https://www.twitch.tv/{value}"
    if stream_type == "ustream":
        return f"https://www.ustream.tv/channel/{value}"
    return None


def _stream_url_from_webcasts(webcasts: Any) -> str | None:
    if not isinstance(webcasts, list):
        return None
    youtube_candidate: str | None = None
    generic_candidate: str | None = None
    for row in webcasts:
        if not isinstance(row, dict):
            continue
        stream_url = _normalize_stream_url(str(row.get("type") or ""), str(row.get("channel") or ""))
        if not stream_url:
            continue
        row_type = str(row.get("type") or "").strip().lower()
        if row_type == "youtube":
            youtube_candidate = stream_url
            break
        if generic_candidate is None:
            generic_candidate = stream_url
    return youtube_candidate or generic_candidate


def _discover_event_stream_url(event_key: str) -> str | None:
    if not str(settings.tba_auth_key or "").strip():
        return None
    try:
        payload = TBAClient().event(event_key)
    except Exception:
        return None
    webcasts = payload.get("webcasts") if isinstance(payload, dict) else None
    return _stream_url_from_webcasts(webcasts)


def _find_live_matches(db, event_key: str, live_window_sec: int) -> list[models.Match]:
    now_unix = int(time.time())
    rows = (
        db.query(models.Match)
        .filter(
            models.Match.event_key == event_key,
            models.Match.time.isnot(None),
        )
        .order_by(models.Match.time.asc(), models.Match.comp_level.asc(), models.Match.set_number.asc(), models.Match.match_number.asc())
        .all()
    )
    results: list[models.Match] = []
    for row in rows:
        scheduled = int(row.time or 0)
        if scheduled <= 0:
            continue
        if scheduled <= now_unix <= (scheduled + int(live_window_sec)):
            results.append(row)
    return results


def _event_has_local_schedule(db, event_key: str) -> bool:
    row = (
        db.query(models.Match.match_key)
        .filter(
            models.Match.event_key == event_key,
            models.Match.time.isnot(None),
        )
        .order_by(models.Match.time.asc())
        .first()
    )
    return row is not None


def _discover_active_events_with_streams(
    db,
    *,
    season: int,
    allowed_countries: set[str],
    require_local_schedule: bool,
    max_events: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    now_utc = datetime.now(timezone.utc)
    payload = TBAClient().events(season)
    events = payload if isinstance(payload, list) else []
    selected: list[dict[str, Any]] = []
    skipped_counts = {
        "invalid_payload": 0,
        "out_of_region_scope": 0,
        "inactive_today": 0,
        "missing_stream": 0,
        "missing_local_schedule": 0,
    }
    for row in events:
        if not isinstance(row, dict):
            skipped_counts["invalid_payload"] += 1
            continue
        event_key = str(row.get("key") or "").strip().lower()
        if not event_key:
            skipped_counts["invalid_payload"] += 1
            continue
        if not _event_is_in_region_scope(row, allowed_countries):
            skipped_counts["out_of_region_scope"] += 1
            continue
        if not _event_is_active_today(row, now_utc):
            skipped_counts["inactive_today"] += 1
            continue
        stream_url = _stream_url_from_webcasts(row.get("webcasts"))
        if not stream_url:
            skipped_counts["missing_stream"] += 1
            continue
        if require_local_schedule and not _event_has_local_schedule(db, event_key):
            skipped_counts["missing_local_schedule"] += 1
            continue
        selected.append(
            {
                "event_key": event_key,
                "name": str(row.get("name") or event_key),
                "stream_url": stream_url,
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
            }
        )

    selected.sort(key=lambda row: (str(row.get("start_date") or ""), str(row.get("event_key") or "")))
    bounded = selected[: max(1, min(int(max_events), 200))]
    return bounded, skipped_counts


def _ensure_match_calibration(db, match: models.Match) -> tuple[models.FieldCalibration | None, str]:
    existing = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key == match.match_key)
        .one_or_none()
    )
    if existing is not None:
        return existing, "existing"

    template = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.event_key == match.event_key)
        .order_by(models.FieldCalibration.updated_at.desc(), models.FieldCalibration.id.desc())
        .first()
    )
    if template is None:
        return None, "missing"

    created = models.FieldCalibration(
        match_key=match.match_key,
        event_key=match.event_key,
        frame_time_sec=template.frame_time_sec,
        image_width=template.image_width,
        image_height=template.image_height,
        image_points=template.image_points,
        field_points=template.field_points,
        homography=template.homography,
    )
    db.add(created)
    db.commit()
    db.refresh(created)
    return created, "cloned_template"


def _upsert_live_match_video(db, match_key: str, stream_url: str) -> None:
    if not stream_url.strip():
        return
    row = (
        db.query(models.MatchVideo)
        .filter(
            models.MatchVideo.match_key == match_key,
            models.MatchVideo.video_type == "live_stream",
        )
        .order_by(models.MatchVideo.id.asc())
        .first()
    )
    stream_hash = hashlib.sha256(stream_url.encode("utf-8")).hexdigest()[:16]
    if row is None:
        db.add(
            models.MatchVideo(
                match_key=match_key,
                video_type="live_stream",
                video_key=f"live-{stream_hash}",
                url=stream_url,
            )
        )
    else:
        row.video_key = f"live-{stream_hash}"
        row.url = stream_url
    db.commit()


def _tick_session(event_key: str, *, manual: bool = False) -> dict[str, Any]:
    if not bool(settings.live_analysis_enabled):
        return {
            "ok": False,
            "event_key": event_key,
            "status": "live_analysis_disabled",
        }

    with _sessions_lock:
        session = _sessions_by_event.get(event_key)
    if session is None:
        return {"ok": False, "event_key": event_key, "status": "not_running"}

    session.last_tick_started_at_unix = _now_unix()
    result: dict[str, Any] = {
        "ok": True,
        "event_key": event_key,
        "manual": bool(manual),
        "stream_url": session.stream_url,
        "auto_stream_discovery": session.auto_stream_discovery,
        "interval_sec": int(session.interval_sec),
        "clip_duration_sec": int(session.clip_duration_sec),
    }

    db = SessionLocal()
    try:
        queue = _queue()
        live_window_sec = max(90, int(settings.live_analysis_live_window_sec))
        live_matches = _find_live_matches(db, event_key, live_window_sec=live_window_sec)
        result["live_match_count"] = len(live_matches)

        # Keep model outputs fresh from previously completed live snapshots.
        post_compute_result: dict[str, Any] = {"ran": False}
        if bool(settings.live_analysis_post_compute):
            try:
                ratings_result = recompute_event_ratings(db, event_key)
            except Exception as exc:
                ratings_result = {"ok": False, "detail": str(exc)}
            try:
                synergy_result = precompute_event_synergy(
                    db,
                    event_key,
                    quality_threshold=float(settings.live_analysis_synergy_quality_threshold),
                )
            except Exception as exc:
                synergy_result = {"ok": False, "detail": str(exc)}
            post_compute_result = {
                "ran": True,
                "ratings": ratings_result,
                "synergy": synergy_result,
            }
        result["post_compute"] = post_compute_result

        if not live_matches:
            result["status"] = "no_live_matches"
            session.last_result = result
            session.last_tick_completed_at_unix = _now_unix()
            session.tick_count += 1
            return result

        stream_url = (session.stream_url or "").strip()
        if not stream_url and session.auto_stream_discovery:
            stream_url = (_discover_event_stream_url(event_key) or "").strip()
            if stream_url:
                session.stream_url = stream_url
        result["stream_url"] = stream_url or None
        if not stream_url:
            result["ok"] = False
            result["status"] = "missing_stream_url"
            session.last_result = result
            session.last_tick_completed_at_unix = _now_unix()
            session.tick_count += 1
            return result

        max_matches_per_tick = max(1, int(settings.live_analysis_max_matches_per_tick))
        selected_matches = live_matches[:max_matches_per_tick]
        analysis_version = str(settings.live_analysis_analysis_version or "video_v3_live_window").strip() or "video_v3_live_window"
        enqueue_cooldown_sec = max(20, int(settings.live_analysis_analysis_cooldown_sec))
        tick_bucket = int(math.floor(_now_unix() / max(20, int(session.interval_sec))))
        now_unix = _now_unix()

        queued: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for match in selected_matches:
            last_enqueue_at = float(session.last_enqueue_unix_by_match.get(match.match_key) or 0.0)
            if (now_unix - last_enqueue_at) < float(enqueue_cooldown_sec):
                skipped.append(
                    {
                        "match_key": match.match_key,
                        "reason": "cooldown",
                        "cooldown_remaining_sec": max(0, int(round(enqueue_cooldown_sec - (now_unix - last_enqueue_at)))),
                    }
                )
                continue

            calibration, calibration_source = _ensure_match_calibration(db, match)
            if calibration is None:
                skipped.append({"match_key": match.match_key, "reason": "missing_calibration"})
                continue

            try:
                capture_live_stream_clip(
                    match.match_key,
                    stream_url,
                    clip_duration_sec=float(session.clip_duration_sec),
                    output_tag="live",
                )
            except VideoExtractionError as exc:
                skipped.append({"match_key": match.match_key, "reason": f"clip_capture_failed:{exc}"})
                continue

            _upsert_live_match_video(db, match.match_key, stream_url)
            event_profile = db.get(models.EventProfile, match.event_key)
            perimeter_type, _ = resolve_perimeter_type_for_event_profile(event_profile)
            base_hash = build_analysis_params_hash(
                analysis_version=analysis_version,
                calibration_id=int(calibration.id),
                perimeter_type=perimeter_type,
            )
            params_hash = hashlib.sha256(
                f"{base_hash}|live_bucket={tick_bucket}|clip={int(session.clip_duration_sec)}".encode("utf-8")
            ).hexdigest()[:24]
            scheduled, job_id, job_status = _enqueue_live_analysis(
                queue,
                match_key=match.match_key,
                analysis_version=analysis_version,
                params_hash=params_hash,
                calibration_id=int(calibration.id),
            )
            if not scheduled:
                skipped.append(
                    {
                        "match_key": match.match_key,
                        "reason": "already_pending",
                        "job_id": job_id,
                        "job_status": job_status,
                    }
                )
                continue
            session.last_enqueue_unix_by_match[match.match_key] = now_unix
            queued.append(
                {
                    "match_key": match.match_key,
                    "job_id": job_id,
                    "job_status": job_status,
                    "analysis_version": analysis_version,
                    "calibration_id": int(calibration.id),
                    "calibration_source": calibration_source,
                    "params_hash": params_hash,
                }
            )

        result["status"] = "tick_completed"
        result["queued"] = queued
        result["skipped"] = skipped
        result["selected_live_matches"] = [match.match_key for match in selected_matches]
        session.last_result = result
        session.last_tick_completed_at_unix = _now_unix()
        session.tick_count += 1
        return result
    except Exception as exc:
        error_result = {
            "ok": False,
            "event_key": event_key,
            "status": "tick_failed",
            "detail": str(exc),
        }
        session.last_result = error_result
        session.last_tick_completed_at_unix = _now_unix()
        session.tick_count += 1
        logger.exception("live analysis tick failed for %s: %s", event_key, exc)
        return error_result
    finally:
        db.rollback()
        db.close()


def _scheduler_job_id(event_key: str) -> str:
    return f"live_analysis_monitor:{event_key}"


def _get_scheduler_instance():
    # Local import avoids circular import chain:
    # routes_analysis -> live_analysis_monitor -> scheduler -> freshness_recovery -> routes_analysis
    from app.services.scheduler import get_scheduler

    return get_scheduler()


def _ensure_scheduler_running() -> None:
    scheduler = _get_scheduler_instance()
    if scheduler.state != STATE_RUNNING:
        scheduler.start()


def start_live_monitor(
    event_key: str,
    *,
    interval_sec: int | None = None,
    clip_duration_sec: int | None = None,
    stream_url: str | None = None,
    auto_stream_discovery: bool = True,
) -> dict[str, Any]:
    if not bool(settings.live_analysis_enabled):
        return {"ok": False, "event_key": event_key, "status": "live_analysis_disabled"}

    normalized_event_key = str(event_key or "").strip().lower()
    if not normalized_event_key:
        return {"ok": False, "status": "invalid_event_key"}

    interval = int(interval_sec or settings.live_analysis_default_interval_sec)
    interval = max(15, min(300, interval))
    clip_duration = int(clip_duration_sec or settings.live_analysis_default_clip_duration_sec)
    clip_duration = max(8, min(120, clip_duration))

    with _sessions_lock:
        existing = _sessions_by_event.get(normalized_event_key)
        if existing is None:
            existing = LiveMonitorSession(
                event_key=normalized_event_key,
                interval_sec=interval,
                clip_duration_sec=clip_duration,
                stream_url=(str(stream_url).strip() if stream_url else None),
                auto_stream_discovery=bool(auto_stream_discovery),
                created_at_unix=_now_unix(),
            )
            _sessions_by_event[normalized_event_key] = existing
        else:
            existing.interval_sec = interval
            existing.clip_duration_sec = clip_duration
            existing.stream_url = (str(stream_url).strip() if stream_url else existing.stream_url)
            existing.auto_stream_discovery = bool(auto_stream_discovery)
            existing.running = True
        session = existing

    _ensure_scheduler_running()
    scheduler = _get_scheduler_instance()
    job_id = _scheduler_job_id(normalized_event_key)
    with suppress(Exception):
        if scheduler.get_job(job_id) is not None:
            scheduler.remove_job(job_id)
    scheduler.add_job(
        _tick_session,
        "interval",
        seconds=int(session.interval_sec),
        id=job_id,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(10, int(session.interval_sec)),
        kwargs={"event_key": normalized_event_key, "manual": False},
    )

    first_tick = _tick_session(normalized_event_key, manual=True)
    return {
        "ok": True,
        "status": "started",
        "event_key": normalized_event_key,
        "interval_sec": int(session.interval_sec),
        "clip_duration_sec": int(session.clip_duration_sec),
        "stream_url": session.stream_url,
        "auto_stream_discovery": bool(session.auto_stream_discovery),
        "first_tick": first_tick,
    }


def stop_live_monitor(event_key: str) -> dict[str, Any]:
    normalized_event_key = str(event_key or "").strip().lower()
    if not normalized_event_key:
        return {"ok": False, "status": "invalid_event_key"}
    with _sessions_lock:
        session = _sessions_by_event.pop(normalized_event_key, None)
    scheduler = _get_scheduler_instance()
    job_id = _scheduler_job_id(normalized_event_key)
    with suppress(Exception):
        if scheduler.get_job(job_id) is not None:
            scheduler.remove_job(job_id)
    return {
        "ok": True,
        "status": "stopped" if session is not None else "not_running",
        "event_key": normalized_event_key,
    }


def stop_all_live_monitors() -> dict[str, Any]:
    with _sessions_lock:
        event_keys = list(_sessions_by_event.keys())
    stopped = [stop_live_monitor(event_key) for event_key in event_keys]
    return {
        "ok": True,
        "stopped_count": len(stopped),
        "events": [row.get("event_key") for row in stopped if isinstance(row, dict)],
    }


def manual_live_monitor_tick(event_key: str) -> dict[str, Any]:
    normalized_event_key = str(event_key or "").strip().lower()
    if not normalized_event_key:
        return {"ok": False, "status": "invalid_event_key"}
    return _tick_session(normalized_event_key, manual=True)


def run_regional_live_auto_manager_tick(*, force: bool = False, season: int | None = None) -> dict[str, Any]:
    global _auto_regional_last_run_unix, _auto_regional_last_result
    if not bool(settings.live_analysis_enabled):
        return {"ok": False, "status": "live_analysis_disabled"}

    enabled = bool(getattr(settings, "live_analysis_regional_auto_enabled", False))
    if not enabled and not force:
        with _auto_regional_lock:
            stale_events = set(_auto_regional_managed_events)
            _auto_regional_managed_events.clear()
        stopped = [stop_live_monitor(event_key) for event_key in sorted(stale_events)]
        result = {
            "ok": True,
            "status": "disabled",
            "stopped_count": len(stopped),
            "stopped_events": [row.get("event_key") for row in stopped if isinstance(row, dict)],
            "enabled": enabled,
            "forced": bool(force),
        }
        with _auto_regional_lock:
            _auto_regional_last_run_unix = _now_unix()
            _auto_regional_last_result = result
        return result

    if not str(settings.tba_auth_key or "").strip():
        return {"ok": False, "status": "missing_tba_auth_key"}

    monitor_interval = max(15, min(300, int(settings.live_analysis_default_interval_sec)))
    clip_duration = max(8, min(120, int(settings.live_analysis_default_clip_duration_sec)))
    max_events = max(1, min(int(getattr(settings, "live_analysis_regional_auto_max_events", 16)), 200))
    require_local_schedule = bool(getattr(settings, "live_analysis_regional_auto_require_schedule", False))
    allowed_countries = _normalize_countries(getattr(settings, "automation_regional_countries", "USA,Canada"))
    resolved_season = int(season or datetime.now(timezone.utc).year)

    db = SessionLocal()
    try:
        discovered_events, skipped_counts = _discover_active_events_with_streams(
            db,
            season=resolved_season,
            allowed_countries=allowed_countries,
            require_local_schedule=require_local_schedule,
            max_events=max_events,
        )
    finally:
        db.rollback()
        db.close()

    started_events: list[str] = []
    updated_events: list[str] = []
    kept_events: list[str] = []
    errored_events: list[dict[str, Any]] = []
    managed_event_keys: set[str] = set()

    for event_row in discovered_events:
        event_key = str(event_row.get("event_key") or "").strip().lower()
        if not event_key:
            continue
        stream_url = str(event_row.get("stream_url") or "").strip()
        with _sessions_lock:
            existing = _sessions_by_event.get(event_key)
        needs_restart = False
        if existing is None:
            needs_restart = True
        else:
            existing_stream = str(existing.stream_url or "").strip()
            if (
                int(existing.interval_sec) != monitor_interval
                or int(existing.clip_duration_sec) != clip_duration
                or existing_stream != stream_url
                or not bool(existing.running)
            ):
                needs_restart = True

        if not needs_restart:
            kept_events.append(event_key)
            managed_event_keys.add(event_key)
            continue

        try:
            start_result = start_live_monitor(
                event_key=event_key,
                interval_sec=monitor_interval,
                clip_duration_sec=clip_duration,
                stream_url=stream_url,
                auto_stream_discovery=True,
            )
        except Exception as exc:
            start_result = {"ok": False, "detail": str(exc)}

        if bool(start_result.get("ok")):
            managed_event_keys.add(event_key)
            if existing is None:
                started_events.append(event_key)
            else:
                updated_events.append(event_key)
        else:
            errored_events.append(
                {
                    "event_key": event_key,
                    "detail": start_result.get("detail") if isinstance(start_result, dict) else "start_failed",
                }
            )
            if existing is not None:
                managed_event_keys.add(event_key)

    with _auto_regional_lock:
        previously_managed = set(_auto_regional_managed_events)
    stale_event_keys = sorted(previously_managed - managed_event_keys)
    stopped_rows = [stop_live_monitor(event_key) for event_key in stale_event_keys]
    stopped_events = [row.get("event_key") for row in stopped_rows if isinstance(row, dict)]

    result = {
        "ok": True,
        "status": "ran",
        "enabled": enabled,
        "forced": bool(force),
        "season": resolved_season,
        "countries": sorted(allowed_countries),
        "require_local_schedule": require_local_schedule,
        "monitor_interval_sec": monitor_interval,
        "clip_duration_sec": clip_duration,
        "discovered_active_events": [row.get("event_key") for row in discovered_events],
        "discovered_event_rows": discovered_events,
        "skipped_counts": skipped_counts,
        "started_events": started_events,
        "updated_events": updated_events,
        "kept_events": kept_events,
        "stopped_events": stopped_events,
        "errored_events": errored_events,
        "managed_count": len(managed_event_keys),
    }
    with _auto_regional_lock:
        _auto_regional_managed_events.clear()
        _auto_regional_managed_events.update(managed_event_keys)
        _auto_regional_last_run_unix = _now_unix()
        _auto_regional_last_result = result
    return result


def regional_live_auto_manager_status() -> dict[str, Any]:
    with _auto_regional_lock:
        managed = sorted(_auto_regional_managed_events)
        last_run = _auto_regional_last_run_unix
        last_result = dict(_auto_regional_last_result) if isinstance(_auto_regional_last_result, dict) else {}
    running_details = live_monitor_status()
    sessions = running_details.get("sessions") if isinstance(running_details, dict) else []
    managed_sessions = []
    if isinstance(sessions, list):
        managed_set = set(managed)
        managed_sessions = [row for row in sessions if isinstance(row, dict) and str(row.get("event_key")) in managed_set]
    return {
        "ok": True,
        "enabled": bool(getattr(settings, "live_analysis_regional_auto_enabled", False)),
        "live_analysis_enabled": bool(settings.live_analysis_enabled),
        "interval_sec": max(30, int(getattr(settings, "live_analysis_regional_auto_interval_sec", 120))),
        "countries": sorted(_normalize_countries(getattr(settings, "automation_regional_countries", "USA,Canada"))),
        "require_local_schedule": bool(getattr(settings, "live_analysis_regional_auto_require_schedule", False)),
        "last_run_at": _iso_or_none(last_run),
        "managed_events": managed,
        "managed_count": len(managed),
        "managed_sessions": managed_sessions,
        "last_result": last_result,
    }


def live_monitor_status(event_key: str | None = None) -> dict[str, Any]:
    with _sessions_lock:
        if event_key is not None:
            event_key_norm = str(event_key).strip().lower()
            sessions = [(_sessions_by_event.get(event_key_norm), event_key_norm)]
        else:
            sessions = [(session, key) for key, session in _sessions_by_event.items()]

    rows: list[dict[str, Any]] = []
    for session, key in sessions:
        if session is None:
            continue
        rows.append(
            {
                "event_key": key,
                "running": bool(session.running),
                "interval_sec": int(session.interval_sec),
                "clip_duration_sec": int(session.clip_duration_sec),
                "stream_url": session.stream_url,
                "auto_stream_discovery": bool(session.auto_stream_discovery),
                "created_at": _iso_or_none(session.created_at_unix),
                "last_tick_started_at": _iso_or_none(session.last_tick_started_at_unix),
                "last_tick_completed_at": _iso_or_none(session.last_tick_completed_at_unix),
                "tick_count": int(session.tick_count),
                "last_result": session.last_result,
            }
        )
    return {
        "ok": True,
        "running_count": len(rows),
        "sessions": rows,
    }
