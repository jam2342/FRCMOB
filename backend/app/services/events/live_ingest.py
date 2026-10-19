# Events in play get their results ingested every few minutes, so ratings, the forecast and
# in-event stats move with each match. Until 2026-10-18 only the 12 h job ingested, and only
# finished events: during an event everything but the live scores stayed at pre-event values
# (2026catt had no stored results, no ratings, and a forecast that replayed every played qual).

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.events.pipeline import post_compute_event, refresh_event

logger = logging.getLogger(__name__)

# In play: a match that started up to 2 h ago or is due within the hour.
IN_PLAY_BEFORE_SEC = 2 * 3600
IN_PLAY_AHEAD_SEC = 3600


def _events_covering(tba: Any, today: date) -> list[str]:
    # A day either side: an evening in Hawaii is already tomorrow in UTC.
    keys = []
    for event in tba.events(today.year) or []:
        try:
            start = date.fromisoformat(event["start_date"])
            end = date.fromisoformat(event["end_date"])
        except (KeyError, TypeError, ValueError):
            continue
        if start - timedelta(days=1) <= today <= end + timedelta(days=1):
            keys.append(str(event["key"]).lower())
    return sorted(keys)


def events_in_play(db: Session, candidates: list[str], now: float) -> list[str]:
    if not candidates:
        return []
    match_time = func.coalesce(models.Match.actual_time, models.Match.predicted_time, models.Match.time)
    with_matches = {
        key
        for (key,) in db.query(models.Match.event_key).filter(models.Match.event_key.in_(candidates)).distinct()
    }
    busy = {
        key
        for (key,) in db.query(models.Match.event_key)
        .filter(
            models.Match.event_key.in_(candidates),
            match_time >= int(now - IN_PLAY_BEFORE_SEC),
            match_time <= int(now + IN_PLAY_AHEAD_SEC),
        )
        .distinct()
    }
    # An event running today with no stored matches has never been ingested: that's the case
    # this job exists for, so it counts until its schedule is in.
    return sorted(busy | {key for key in candidates if key not in with_matches})


def _stored_results(db: Session, event_key: str) -> int:
    return int(
        db.query(func.count(models.MatchAllianceResult.match_key))
        .filter(models.MatchAllianceResult.event_key == event_key)
        .scalar()
        or 0
    )


def ingest_events_in_play(db: Session, tba: Any, *, now: float | None = None, max_events: int | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    started = time.perf_counter()
    today = datetime.fromtimestamp(now, timezone.utc).date()
    events = events_in_play(db, _events_covering(tba, today), now)
    limit = int(max_events if max_events is not None else settings.live_event_ingest_max_events)
    skipped = events[limit:]
    events = events[:limit]

    changed, errors = [], []
    for event_key in events:
        before = _stored_results(db, event_key)
        result = refresh_event(db, event_key=event_key, run_post_compute=False)
        if result.get("status") != "processed":
            errors.append(event_key)
            continue
        if _stored_results(db, event_key) != before:
            changed.append(event_key)

    # Ratings only for events that gained results: the first rebuild replays the season
    # (~25 s on the VM), the rest reuse it (~1.5 s each).
    rebuilt = []
    for event_key in changed:
        ratings = post_compute_event(db, event_key=event_key).get("ratings") or {}
        if ratings.get("ok"):
            rebuilt.append(event_key)
        else:
            errors.append(event_key)
    return {
        "events": events,
        "skipped_over_limit": skipped,
        "changed": changed,
        "rebuilt": rebuilt,
        "errors": sorted(set(errors)),
        "runtime_sec": round(time.perf_counter() - started, 1),
    }
