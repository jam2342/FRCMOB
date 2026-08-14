# API routes for alliance-selection picklists.
#
# Picklists are shared, hand-ordered team rankings used on alliance-selection
# morning. Concurrency model: each write echoes the `version` it loaded; a
# stale version gets a 409 with the current document so the client can merge.

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/picklists", tags=["picklists"])

_EVENT_KEY_RE = re.compile(r"^\d{4}[a-z0-9]+$")
_TEAM_KEY_RE = re.compile(r"^frc\d{1,5}$")
_VALID_TIERS = {"first", "second", "dnp"}
_VALID_STATUSES = {"available", "picked", "declined", "captain"}
MAX_SLOTS = 120
MAX_PICKLISTS_PER_EVENT = 12

def _normalize_event_key(raw: str | None) -> str:
    token = str(raw or "").strip().lower()
    if not token or not _EVENT_KEY_RE.match(token):
        raise HTTPException(status_code=400, detail="Invalid event key")
    return token

def _normalize_slot(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    team_key = str(raw.get("team_key") or "").strip().lower()
    if not _TEAM_KEY_RE.match(team_key):
        return None
    tier = str(raw.get("tier") or "first").strip().lower()
    if tier not in _VALID_TIERS:
        tier = "first"
    status = str(raw.get("status") or "available").strip().lower()
    if status not in _VALID_STATUSES:
        status = "available"
    picked_by = raw.get("picked_by_alliance")
    try:
        picked_by = int(picked_by) if picked_by is not None else None
    except (TypeError, ValueError):
        picked_by = None
    if picked_by is not None and not (1 <= picked_by <= 8):
        picked_by = None
    return {
        "team_key": team_key,
        "tier": tier,
        "status": status,
        "picked_by_alliance": picked_by,
        "dnp_reason": str(raw.get("dnp_reason") or "")[:240],
        "notes": str(raw.get("notes") or "")[:480],
    }

def _normalize_slots(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    slots: list[dict[str, Any]] = []
    for item in raw[: MAX_SLOTS]:
        slot = _normalize_slot(item)
        if slot is None or slot["team_key"] in seen:
            continue
        seen.add(slot["team_key"])
        slots.append(slot)
    return slots

def _serialize_picklist(row: models.EventPicklist) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_key": row.event_key,
        "title": row.title,
        "created_by": row.created_by,
        "slots": row.slots if isinstance(row.slots, list) else [],
        "version": int(row.version or 1),
        "live_mode": bool(row.live_mode),
        "archived": bool(row.archived),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }

def _load_picklist_or_404(db: Session, picklist_id: int) -> models.EventPicklist:
    row = db.get(models.EventPicklist, picklist_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Picklist not found")
    return row

class PicklistCreateRequest(BaseModel):
    event_key: str
    title: str = Field(default="Picklist", max_length=120)
    created_by: str | None = Field(default=None, max_length=40)
    slots: list[dict[str, Any]] = Field(default_factory=list)

class PicklistUpdateRequest(BaseModel):
    version: int
    slots: list[dict[str, Any]] | None = None
    title: str | None = Field(default=None, max_length=120)
    live_mode: bool | None = None
    archived: bool | None = None

@router.get("")
def list_picklists(
    event_key: str = Query(..., max_length=48),
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    normalized = _normalize_event_key(event_key)
    stmt = select(models.EventPicklist).where(models.EventPicklist.event_key == normalized)
    if not include_archived:
        stmt = stmt.where(models.EventPicklist.archived.is_(False))
    stmt = stmt.order_by(models.EventPicklist.updated_at.desc())
    rows = db.execute(stmt).scalars().all()
    return {
        "ok": True,
        "event_key": normalized,
        "count": len(rows),
        "picklists": [_serialize_picklist(row) for row in rows],
    }

@router.post("")
def create_picklist(payload: PicklistCreateRequest, db: Session = Depends(get_db)):
    normalized = _normalize_event_key(payload.event_key)
    existing_count = len(
        db.execute(
            select(models.EventPicklist.id).where(
                models.EventPicklist.event_key == normalized,
                models.EventPicklist.archived.is_(False),
            )
        ).all()
    )
    if existing_count >= MAX_PICKLISTS_PER_EVENT:
        raise HTTPException(
            status_code=400,
            detail=f"Too many picklists for this event (max {MAX_PICKLISTS_PER_EVENT}). Archive old ones first.",
        )
    title = str(payload.title or "Picklist").strip()[:120] or "Picklist"
    row = models.EventPicklist(
        event_key=normalized,
        title=title,
        created_by=str(payload.created_by or "").strip()[:40] or None,
        slots=_normalize_slots(payload.slots),
        version=1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "picklist": _serialize_picklist(row)}

@router.get("/{picklist_id}")
def get_picklist(picklist_id: int, db: Session = Depends(get_db)):
    row = _load_picklist_or_404(db, picklist_id)
    return {"ok": True, "picklist": _serialize_picklist(row)}

@router.put("/{picklist_id}")
def update_picklist(
    picklist_id: int,
    payload: PicklistUpdateRequest,
    db: Session = Depends(get_db),
):
    row = _load_picklist_or_404(db, picklist_id)
    if int(payload.version or 0) != int(row.version or 1):
        # Stale client: return the current document so the UI can reconcile.
        return {
            "ok": False,
            "conflict": True,
            "detail": "Picklist was modified by someone else. Latest version returned.",
            "picklist": _serialize_picklist(row),
        }

    if payload.slots is not None:
        row.slots = _normalize_slots(payload.slots)
    if payload.title is not None:
        title = str(payload.title).strip()[:120]
        if title:
            row.title = title
    if payload.live_mode is not None:
        row.live_mode = bool(payload.live_mode)
    if payload.archived is not None:
        row.archived = bool(payload.archived)
    row.version = int(row.version or 1) + 1
    db.commit()
    db.refresh(row)
    return {"ok": True, "picklist": _serialize_picklist(row)}

@router.delete("/{picklist_id}")
def delete_picklist(picklist_id: int, db: Session = Depends(get_db)):
    row = _load_picklist_or_404(db, picklist_id)
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_id": picklist_id}
