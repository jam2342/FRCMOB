# Event refresh: re-ingest one event's official data, then rebuild what depends on it.

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import sanitize_external_error
from app.services.intel.snapshots import invalidate_event_snapshots
from app.services.ratings.model import recompute_event_ratings

logger = logging.getLogger(__name__)


def post_compute_event(db: Session, *, event_key: str) -> dict[str, Any]:
    try:
        ratings_result = recompute_event_ratings(db, event_key)
    except Exception as exc:
        db.rollback()
        ratings_result = {"ok": False, "detail": sanitize_external_error(exc, default="Ratings recompute failed.")}

    # Team and event pages for this event were built from the old numbers; idle events keep
    # their snapshots for a whole refresh cycle, so drop them now rather than wait.
    invalidate_event_snapshots(db, event_key)

    return {"ratings": ratings_result}


def refresh_event(
    db: Session,
    *,
    event_key: str,
    run_post_compute: bool,
) -> dict[str, Any]:
    from app.services.events.ingest import ingest_event as ingest_event_data
    from app.tba.client import TBAClient

    started = time.perf_counter()
    try:
        # Ingest skips its own rebuild: the block below runs it once.
        ingest_result = ingest_event_data(event_key=event_key, tba=TBAClient(), db=db, run_post_compute=False)
    except Exception as exc:
        db.rollback()
        logger.error("event_refresh.ingest_failed event=%s error=%s", event_key, str(exc)[:200])
        return {
            "event_key": event_key,
            "status": "ingest_failed",
            "detail": sanitize_external_error(exc, default="Event ingest failed."),
        }

    post_compute: dict[str, Any] = {"ran": False}
    if run_post_compute:
        post_compute = {"ran": True, **post_compute_event(db, event_key=event_key)}

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "event_refresh.completed event=%s elapsed_ms=%.1f ratings_ok=%s",
        event_key,
        elapsed_ms,
        bool((post_compute.get("ratings") or {}).get("ok")),
    )
    return {
        "event_key": event_key,
        "status": "processed",
        "ingest": ingest_result,
        "post_compute": post_compute,
        "elapsed_ms": elapsed_ms,
    }
