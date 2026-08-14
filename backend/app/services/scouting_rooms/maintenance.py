from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.db.session import SessionLocal

def cleanup_inactive_scouting_rooms(
    *,
    inactive_days: int | None = None,
    max_rooms_per_run: int | None = None,
) -> dict[str, Any]:
    # Delete scouting rooms inactive beyond retention, including dependent rows.
    resolved_inactive_days = max(1, int(inactive_days or settings.scouting_rooms_inactive_delete_days))
    resolved_max_rooms = max(1, int(max_rooms_per_run or settings.scouting_rooms_cleanup_max_rooms_per_run))
    cutoff = datetime.now(timezone.utc) - timedelta(days=resolved_inactive_days)

    db = SessionLocal()
    try:
        return _cleanup_inactive_scouting_rooms_db(
            db,
            cutoff=cutoff,
            max_rooms_per_run=resolved_max_rooms,
        )
    finally:
        db.close()

def _cleanup_inactive_scouting_rooms_db(
    db: Session,
    *,
    cutoff: datetime,
    max_rooms_per_run: int,
) -> dict[str, Any]:
    stale_rooms = (
        db.query(models.ScoutingRoom)
        .filter(models.ScoutingRoom.last_activity_at < cutoff)
        .order_by(models.ScoutingRoom.last_activity_at.asc(), models.ScoutingRoom.room_key.asc())
        .limit(max(1, int(max_rooms_per_run)))
        .all()
    )
    if not stale_rooms:
        return {
            "ok": True,
            "cutoff": cutoff.isoformat(),
            "deleted_rooms": 0,
            "deleted_entries": 0,
            "deleted_assignments": 0,
            "deleted_leaders": 0,
            "room_keys": [],
        }

    room_keys = [str(room.room_key or "").strip().lower() for room in stale_rooms if room.room_key]
    if not room_keys:
        return {
            "ok": True,
            "cutoff": cutoff.isoformat(),
            "deleted_rooms": 0,
            "deleted_entries": 0,
            "deleted_assignments": 0,
            "deleted_leaders": 0,
            "room_keys": [],
        }

    deleted_leaders = (
        db.query(models.ScoutingRoomLeader)
        .filter(models.ScoutingRoomLeader.room_key.in_(room_keys))
        .delete(synchronize_session=False)
    )
    deleted_assignments = (
        db.query(models.ScoutingRoomAssignment)
        .filter(models.ScoutingRoomAssignment.room_key.in_(room_keys))
        .delete(synchronize_session=False)
    )
    deleted_entries = (
        db.query(models.ScoutingRoomEntry)
        .filter(models.ScoutingRoomEntry.room_key.in_(room_keys))
        .delete(synchronize_session=False)
    )
    deleted_rooms = (
        db.query(models.ScoutingRoom)
        .filter(models.ScoutingRoom.room_key.in_(room_keys))
        .delete(synchronize_session=False)
    )
    db.commit()
    return {
        "ok": True,
        "cutoff": cutoff.isoformat(),
        "deleted_rooms": int(deleted_rooms or 0),
        "deleted_entries": int(deleted_entries or 0),
        "deleted_assignments": int(deleted_assignments or 0),
        "deleted_leaders": int(deleted_leaders or 0),
        "room_keys": room_keys,
    }
