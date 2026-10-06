# Keep each match's predicted and actual start times current from TBA.
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db import models

logger = logging.getLogger(__name__)


def _unix(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def match_times_from_payload(payload: dict[str, Any]) -> dict[str, int | None]:
    return {
        "time": _unix(payload.get("time")),
        "predicted_time": _unix(payload.get("predicted_time")),
        "actual_time": _unix(payload.get("actual_time")),
    }


def refresh_match_times(db: Session, *, event_key: str, tba_matches: Any) -> int:
    # Writes only rows whose predicted/actual time moved. The scheduled time is left
    # alone here; ingest owns it.
    if not isinstance(tba_matches, list):
        return 0
    incoming: dict[str, dict[str, int | None]] = {}
    for payload in tba_matches:
        if isinstance(payload, dict) and isinstance(payload.get("key"), str):
            incoming[payload["key"]] = match_times_from_payload(payload)
    if not incoming:
        return 0
    changed = 0
    for match in db.query(models.Match).filter(models.Match.event_key == event_key):
        times = incoming.get(match.match_key)
        if times is None:
            continue
        for field in ("predicted_time", "actual_time"):
            value = times[field]
            if value is not None and getattr(match, field) != value:
                setattr(match, field, value)
                changed += 1
    if changed:
        db.commit()
        logger.info("match_times.refreshed event=%s fields_changed=%s", event_key, changed)
    return changed
