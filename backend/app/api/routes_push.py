# API routes for Web Push notification subscriptions.

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.db.session import get_db
from app.services.push.sender import push_configured, push_unavailable_reason, send_web_push

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])

_EVENT_KEY_RE = re.compile(r"^\d{4}[a-z0-9]+$")
_TEAM_KEY_RE = re.compile(r"^frc\d{1,5}$")
MAX_TEAMS_PER_SUBSCRIPTION = 30

def _normalize_team_keys(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    cleaned: list[str] = []
    for item in raw[:MAX_TEAMS_PER_SUBSCRIPTION]:
        token = str(item or "").strip().lower()
        if _TEAM_KEY_RE.match(token) and token not in cleaned:
            cleaned.append(token)
    return cleaned

def _normalize_prefs(raw: Any) -> dict[str, Any]:
    prefs = raw if isinstance(raw, dict) else {}
    try:
        lead = int(prefs.get("match_lead_minutes") or settings.push_match_lead_minutes_default)
    except (TypeError, ValueError):
        lead = int(settings.push_match_lead_minutes_default)
    return {
        "match_lead_minutes": max(1, min(lead, 60)),
        "shift_alerts": bool(prefs.get("shift_alerts")),
        "scout_profile": str(prefs.get("scout_profile") or "").strip()[:40],
        "room_key": str(prefs.get("room_key") or "").strip().lower()[:48],
    }

def _serialize_subscription(row: models.PushSubscription) -> dict[str, Any]:
    return {
        "id": row.id,
        "endpoint": row.endpoint,
        "event_key": row.event_key,
        "team_keys": row.team_keys if isinstance(row.team_keys, list) else [],
        "prefs": row.prefs if isinstance(row.prefs, dict) else {},
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }

def _load_by_endpoint(db: Session, endpoint: str) -> models.PushSubscription | None:
    return db.execute(
        select(models.PushSubscription).where(models.PushSubscription.endpoint == endpoint)
    ).scalar_one_or_none()

class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(max_length=1024)
    keys: dict[str, Any] = Field(default_factory=dict)
    event_key: str | None = Field(default=None, max_length=48)
    team_keys: list[str] = Field(default_factory=list)
    prefs: dict[str, Any] = Field(default_factory=dict)

class PushEndpointRequest(BaseModel):
    endpoint: str = Field(max_length=1024)

@router.get("/public-key")
def get_public_key():
    if not push_configured():
        return {
            "ok": False,
            "configured": False,
            "detail": push_unavailable_reason(),
            "public_key": None,
        }
    return {
        "ok": True,
        "configured": True,
        "public_key": str(settings.vapid_public_key).strip(),
    }

@router.post("/subscribe")
def subscribe(payload: PushSubscribeRequest, db: Session = Depends(get_db)):
    endpoint = str(payload.endpoint or "").strip()
    if not endpoint.startswith("https://"):
        raise HTTPException(status_code=400, detail="Invalid push endpoint")
    keys = payload.keys if isinstance(payload.keys, dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not p256dh or not auth:
        raise HTTPException(status_code=400, detail="Missing subscription encryption keys")

    event_key = str(payload.event_key or "").strip().lower() or None
    if event_key and not _EVENT_KEY_RE.match(event_key):
        raise HTTPException(status_code=400, detail="Invalid event key")

    row = _load_by_endpoint(db, endpoint)
    if row is None:
        row = models.PushSubscription(
            endpoint=endpoint,
            keys={"p256dh": p256dh, "auth": auth},
            event_key=event_key,
            team_keys=_normalize_team_keys(payload.team_keys),
            prefs=_normalize_prefs(payload.prefs),
            enabled=True,
            notified={},
        )
        db.add(row)
    else:
        row.keys = {"p256dh": p256dh, "auth": auth}
        row.event_key = event_key
        row.team_keys = _normalize_team_keys(payload.team_keys)
        row.prefs = _normalize_prefs(payload.prefs)
        row.enabled = True
        row.failure_count = 0
    db.commit()
    db.refresh(row)
    return {"ok": True, "subscription": _serialize_subscription(row)}

@router.post("/unsubscribe")
def unsubscribe(payload: PushEndpointRequest, db: Session = Depends(get_db)):
    endpoint = str(payload.endpoint or "").strip()
    row = _load_by_endpoint(db, endpoint)
    if row is not None:
        db.delete(row)
        db.commit()
    return {"ok": True}

@router.post("/test")
def send_test(payload: PushEndpointRequest, db: Session = Depends(get_db)):
    if not push_configured():
        raise HTTPException(status_code=503, detail=push_unavailable_reason() or "Push not configured")
    endpoint = str(payload.endpoint or "").strip()
    row = _load_by_endpoint(db, endpoint)
    if row is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    ok = send_web_push(
        db,
        row,
        {
            "title": "FRCMOB notifications enabled",
            "body": "You will get alerts before your favorite teams play.",
            "tag": "test",
            "url": "/settings",
        },
    )
    db.commit()
    if not ok:
        raise HTTPException(status_code=502, detail="Push delivery failed")
    return {"ok": True}
