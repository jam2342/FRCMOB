# Bounds on what anonymous team workspaces can cost.
#
# Anyone can create a workspace with no account, and per-IP limits don't work here:
# behind the Vercel rewrite every visitor arrives from Vercel's addresses. So the
# cost is capped instead of the caller: a site-wide hourly creation budget, a pit
# photo quota per workspace and event, and removal of workspaces that never held
# anything and nobody has opened in a month.
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.cache import get_cache

logger = logging.getLogger(__name__)

_CREATED_KEY = "workspaces:created:"
_BUDGET_HIT_KEY = "workspaces:budget_hit:"
_JOIN_FAILURES_KEY = "workspaces:join_failures:"
_KEY_TTL_SEC = 2 * 86400
# Wrong join codes are counted, not locked out: a lockout can't be aimed at the
# guesser (see above), and a site-wide one would stop every team from joining. A
# 10-character code is ~50 bits, so the count is there to notice someone trying.
JOIN_FAILURE_ALERT_PER_HOUR = 200


def _hour_key(now: datetime) -> str:
    return now.strftime("%Y%m%d%H")


def take_workspace_creation_slot(*, now: datetime | None = None) -> bool:
    # Production signup is a new allocation: a failed shared limiter must not
    # silently turn the storage/cost ceiling off. Existing teams can still join.
    client = get_cache().redis_client
    if client is None:
        if settings.is_production_like:
            raise HTTPException(status_code=503, detail="Team signup is temporarily unavailable. Existing teams can still join.")
        return True
    hour = _hour_key(now or datetime.now(timezone.utc))
    limit = max(1, int(settings.workspace_create_max_per_hour or 30))
    try:
        count = int(client.incr(_CREATED_KEY + hour))
        if count == 1:
            client.expire(_CREATED_KEY + hour, _KEY_TTL_SEC)
        if count <= limit:
            return True
        client.set(_BUDGET_HIT_KEY + hour, "1", ex=_KEY_TTL_SEC)
        if count == limit + 1:
            logger.warning("workspaces.create_budget_reached hour=%s limit=%s", hour, limit)
        return False
    except Exception:
        logger.warning("workspace creation budget unavailable", exc_info=True)
        if settings.is_production_like:
            raise HTTPException(status_code=503, detail="Team signup is temporarily unavailable. Existing teams can still join.") from None
        return True


def record_join_failure(*, now: datetime | None = None) -> None:
    client = get_cache().redis_client
    if client is None:
        return
    key = _JOIN_FAILURES_KEY + _hour_key(now or datetime.now(timezone.utc))
    try:
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, _KEY_TTL_SEC)
        if count == JOIN_FAILURE_ALERT_PER_HOUR:
            logger.warning("workspaces.join_failures_high key=%s count=%s", key, count)
    except Exception:
        logger.warning("workspace join failure count unavailable", exc_info=True)


def join_failure_status(*, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    status: dict[str, Any] = {"alert_per_hour": JOIN_FAILURE_ALERT_PER_HOUR, "this_hour": None, "high_hours_24h": []}
    client = get_cache().redis_client
    if client is None:
        return status
    try:
        hours = [_hour_key(current - timedelta(hours=offset)) for offset in range(24)]
        counts = [int(value or 0) for value in client.mget([_JOIN_FAILURES_KEY + hour for hour in hours])]
        status["this_hour"] = counts[0]
        status["high_hours_24h"] = [hour for hour, count in zip(hours, counts) if count >= JOIN_FAILURE_ALERT_PER_HOUR]
    except Exception:
        logger.warning("workspace join failure status unavailable", exc_info=True)
    return status


def creation_budget_status(*, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    status: dict[str, Any] = {
        "limit_per_hour": int(settings.workspace_create_max_per_hour or 30),
        "created_this_hour": None,
        "budget_hit_hours_24h": [],
    }
    client = get_cache().redis_client
    if client is None:
        return status
    try:
        status["created_this_hour"] = int(client.get(_CREATED_KEY + _hour_key(current)) or 0)
        hours = [_hour_key(current - timedelta(hours=offset)) for offset in range(24)]
        hits = client.mget([_BUDGET_HIT_KEY + hour for hour in hours])
        status["budget_hit_hours_24h"] = [hour for hour, hit in zip(hours, hits) if hit]
    except Exception:
        logger.warning("workspace creation budget status unavailable", exc_info=True)
    return status


def pit_photo_bytes_used(db: Session, workspace_id: int, event_key: str | None, *, photos_root: Path) -> int:
    # Stored paths look like /media/pit_photos/<event>/<team>/<file>.
    query = select(models.PitScoutingEntry.photos).where(models.PitScoutingEntry.workspace_id == workspace_id)
    if event_key is not None:
        query = query.where(models.PitScoutingEntry.event_key == event_key)
    rows = db.execute(query).scalars().all()
    total = 0
    root = photos_root.resolve()
    for photos in rows:
        for path in photos if isinstance(photos, list) else []:
            relative = str(path or "").removeprefix("/media/pit_photos/")
            candidate = (photos_root / relative).resolve()
            try:
                if candidate.is_relative_to(root) and candidate.is_file():
                    total += candidate.stat().st_size
            except OSError:
                continue
    return total


def lock_pit_photo_storage(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 0x46524350})


def guard_pit_photo_storage(db: Session, workspace_id: int, incoming_bytes: int, *, photos_root: Path) -> None:
    # Serialize the global check AND the subsequent write/commit across workers.
    # A workspace row lock alone cannot protect a site-wide disk budget.
    lock_pit_photo_storage(db)
    photos_root.mkdir(parents=True, exist_ok=True)
    # A byte allowance alone permits millions of tiny valid images and database
    # rows. Bound cardinality too, including across invented event names.
    rows = db.execute(select(models.PitScoutingEntry.photos).where(
        models.PitScoutingEntry.workspace_id == workspace_id,
    )).scalars().all()
    workspace_files = sum(len(photos) for photos in rows if isinstance(photos, list))
    if workspace_files >= max(1, int(settings.pit_photo_max_files_per_workspace)):
        raise HTTPException(status_code=413, detail="Your team has reached its photo limit. Delete old photos first.")
    workspace_limit = max(1, int(settings.pit_photo_quota_bytes_per_workspace))
    if pit_photo_bytes_used(db, workspace_id, None, photos_root=photos_root) + incoming_bytes > workspace_limit:
        raise HTTPException(status_code=413, detail="Your team's photo storage is full. Delete old photos first.")
    root = photos_root.resolve()
    total = 0
    file_count = 0
    for path in photos_root.rglob("*"):
        try:
            if path.is_file() and path.resolve().is_relative_to(root):
                file_count += 1
                total += path.stat().st_size
                if file_count >= max(1, int(settings.pit_photo_max_files_total)):
                    raise HTTPException(status_code=413, detail="Photo storage is at capacity. Delete old photos first.")
        except FileNotFoundError:
            continue  # A concurrent deletion only frees space.
    if total + incoming_bytes > max(1, int(settings.pit_photo_quota_bytes_total)):
        raise HTTPException(status_code=413, detail="Photo storage is at capacity. Try again after old photos are removed.")
    if shutil.disk_usage(photos_root).free - incoming_bytes < max(1, int(settings.pit_photo_minimum_free_bytes)):
        raise HTTPException(status_code=503, detail="Photo uploads are temporarily unavailable. Your notes can still be saved.")


def delete_abandoned_empty_workspaces(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    # Only workspaces that never held anything (no rooms, picklists, pit notes,
    # recordings or strategy boards) and never had a second member: a team that formed keeps its
    # roster and code between seasons even if its rooms were cleaned up. Locked
    # ones (the legacy bucket, or left by their last member) exist to hold rows.
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        days=max(1, int(settings.workspace_empty_cleanup_after_days or 30))
    )
    workspace = models.TeamWorkspace
    member = models.TeamWorkspaceMember
    recently_used = exists().where(
        member.workspace_id == workspace.id,
        or_(member.last_seen_at >= cutoff, member.joined_at >= cutoff),
    )
    member_count = (
        select(func.count()).where(member.workspace_id == workspace.id).correlate(workspace).scalar_subquery()
    )
    holds_data = or_(
        exists().where(models.ScoutingRoom.workspace_id == workspace.id),
        exists().where(models.EventPicklist.workspace_id == workspace.id),
        exists().where(models.PitScoutingEntry.workspace_id == workspace.id),
        exists().where(models.OnDeviceSession.workspace_id == workspace.id),
        exists().where(models.StrategyBoard.workspace_id == workspace.id),
    )
    rows = db.execute(
        select(workspace.id, workspace.name, workspace.created_at)
        .where(
            workspace.is_locked.is_(False),
            workspace.created_at < cutoff,
            ~recently_used,
            ~holds_data,
            member_count <= 1,
        )
        .order_by(workspace.id)
        .limit(500)
    ).all()
    ids = [int(row.id) for row in rows]
    if not ids:
        return {"deleted_workspaces": 0, "deleted_members": 0, "cutoff": cutoff.isoformat()}
    deleted_members = db.query(member).filter(member.workspace_id.in_(ids)).delete(synchronize_session=False)
    db.query(workspace).filter(workspace.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    for row in rows:
        logger.info(
            "workspaces.empty_removed id=%s name=%r created_at=%s", row.id, row.name, row.created_at
        )
    remaining = db.execute(select(func.count()).select_from(workspace)).scalar() or 0
    return {
        "deleted_workspaces": len(ids),
        "deleted_members": int(deleted_members or 0),
        "remaining_workspaces": int(remaining),
        "cutoff": cutoff.isoformat(),
    }
