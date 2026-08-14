# API routes for pit scouting.
#
# One entry per (event, team) holding the schema-driven pit form answers and
# scout-taken robot photos. Photos arrive as base64 JSON (not multipart) so
# they ride the same offline mutation queue as every other write.

import base64
import binascii
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pit-scouting", tags=["pit-scouting"])

MEDIA_ROOT = Path(__file__).resolve().parents[2] / "media"
PIT_PHOTOS_ROOT = MEDIA_ROOT / "pit_photos"

_EVENT_KEY_RE = re.compile(r"^\d{4}[a-z0-9]+$")
_TEAM_KEY_RE = re.compile(r"^frc\d{1,5}$")

MAX_PHOTO_BYTES = 6 * 1024 * 1024
MAX_PHOTOS_PER_TEAM = 12
MAX_PAYLOAD_FIELDS = 80

_IMAGE_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"RIFF", ".webp"),
]

def _normalize_event_key(raw: str | None) -> str:
    token = str(raw or "").strip().lower()
    if not token or not _EVENT_KEY_RE.match(token):
        raise HTTPException(status_code=400, detail="Invalid event key")
    return token

def _normalize_team_key(raw: str | None) -> str:
    token = str(raw or "").strip().lower()
    if not token or not _TEAM_KEY_RE.match(token):
        raise HTTPException(status_code=400, detail="Invalid team key")
    return token

def _normalize_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in list(raw.items())[:MAX_PAYLOAD_FIELDS]:
        token = str(key)[:64]
        if isinstance(value, (str,)):
            cleaned[token] = value[:2000]
        elif isinstance(value, (int, float, bool)) or value is None:
            cleaned[token] = value
        elif isinstance(value, list):
            cleaned[token] = [str(item)[:120] for item in value[:24]]
    return cleaned

def _serialize_entry(row: models.PitScoutingEntry) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_key": row.event_key,
        "team_key": row.team_key,
        "scout_profile": row.scout_profile,
        "payload": row.payload if isinstance(row.payload, dict) else {},
        "photos": row.photos if isinstance(row.photos, list) else [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }

def _load_entry(db: Session, event_key: str, team_key: str) -> models.PitScoutingEntry | None:
    return db.execute(
        select(models.PitScoutingEntry).where(
            models.PitScoutingEntry.event_key == event_key,
            models.PitScoutingEntry.team_key == team_key,
        )
    ).scalar_one_or_none()

def _detect_image_extension(data: bytes) -> str | None:
    for signature, extension in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            if extension == ".webp" and data[8:12] != b"WEBP":
                continue
            return extension
    return None

class PitEntryUpsertRequest(BaseModel):
    event_key: str
    team_key: str
    scout_profile: str | None = Field(default=None, max_length=40)
    payload: dict[str, Any] = Field(default_factory=dict)

class PitPhotoUploadRequest(BaseModel):
    event_key: str
    team_key: str
    scout_profile: str | None = Field(default=None, max_length=40)
    image_base64: str

class PitPhotoDeleteRequest(BaseModel):
    event_key: str
    team_key: str
    photo_path: str

@router.get("")
def list_pit_entries(
    event_key: str = Query(..., max_length=48),
    db: Session = Depends(get_db),
):
    normalized = _normalize_event_key(event_key)
    rows = db.execute(
        select(models.PitScoutingEntry)
        .where(models.PitScoutingEntry.event_key == normalized)
        .order_by(models.PitScoutingEntry.team_key.asc())
    ).scalars().all()
    return {
        "ok": True,
        "event_key": normalized,
        "count": len(rows),
        "entries": [_serialize_entry(row) for row in rows],
    }

@router.get("/{event_key}/{team_key}")
def get_pit_entry(event_key: str, team_key: str, db: Session = Depends(get_db)):
    normalized_event = _normalize_event_key(event_key)
    normalized_team = _normalize_team_key(team_key)
    row = _load_entry(db, normalized_event, normalized_team)
    if row is None:
        return {"ok": True, "entry": None}
    return {"ok": True, "entry": _serialize_entry(row)}

@router.post("")
def upsert_pit_entry(payload: PitEntryUpsertRequest, db: Session = Depends(get_db)):
    normalized_event = _normalize_event_key(payload.event_key)
    normalized_team = _normalize_team_key(payload.team_key)
    scout_profile = str(payload.scout_profile or "").strip()[:40] or None

    row = _load_entry(db, normalized_event, normalized_team)
    if row is None:
        row = models.PitScoutingEntry(
            event_key=normalized_event,
            team_key=normalized_team,
            scout_profile=scout_profile,
            payload=_normalize_payload(payload.payload),
            photos=[],
        )
        db.add(row)
    else:
        row.payload = _normalize_payload(payload.payload)
        if scout_profile:
            row.scout_profile = scout_profile
    db.commit()
    db.refresh(row)
    return {"ok": True, "entry": _serialize_entry(row)}

@router.post("/photo")
def upload_pit_photo(payload: PitPhotoUploadRequest, db: Session = Depends(get_db)):
    normalized_event = _normalize_event_key(payload.event_key)
    normalized_team = _normalize_team_key(payload.team_key)

    raw_b64 = str(payload.image_base64 or "").strip()
    # Allow data-URL form ("data:image/jpeg;base64,....").
    if raw_b64.startswith("data:"):
        _, _, raw_b64 = raw_b64.partition(",")
    if not raw_b64:
        raise HTTPException(status_code=400, detail="Empty image payload")
    if len(raw_b64) > int(MAX_PHOTO_BYTES * 4 / 3) + 16:
        raise HTTPException(status_code=413, detail="Photo too large (6 MB max)")
    try:
        data = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 image payload") from exc
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="Photo too large (6 MB max)")

    extension = _detect_image_extension(data)
    if extension is None:
        raise HTTPException(status_code=400, detail="Unsupported image format (JPEG/PNG/WebP only)")

    row = _load_entry(db, normalized_event, normalized_team)
    if row is None:
        row = models.PitScoutingEntry(
            event_key=normalized_event,
            team_key=normalized_team,
            scout_profile=str(payload.scout_profile or "").strip()[:40] or None,
            payload={},
            photos=[],
        )
        db.add(row)
        db.flush()

    photos = list(row.photos) if isinstance(row.photos, list) else []
    if len(photos) >= MAX_PHOTOS_PER_TEAM:
        raise HTTPException(
            status_code=400,
            detail=f"Photo limit reached ({MAX_PHOTOS_PER_TEAM} per team). Delete one first.",
        )

    photo_dir = PIT_PHOTOS_ROOT / normalized_event / normalized_team
    photo_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{uuid.uuid4().hex}{extension}"
    file_path = photo_dir / file_name
    try:
        file_path.write_bytes(data)
    except OSError as exc:
        logger.exception("Failed to write pit photo for %s/%s", normalized_event, normalized_team)
        raise HTTPException(status_code=500, detail="Failed to store photo.") from exc

    photo_url = f"/media/pit_photos/{normalized_event}/{normalized_team}/{file_name}"
    photos.append(photo_url)
    row.photos = photos
    db.commit()
    db.refresh(row)
    return {"ok": True, "photo": photo_url, "entry": _serialize_entry(row)}

@router.post("/photo/delete")
def delete_pit_photo(payload: PitPhotoDeleteRequest, db: Session = Depends(get_db)):
    normalized_event = _normalize_event_key(payload.event_key)
    normalized_team = _normalize_team_key(payload.team_key)
    row = _load_entry(db, normalized_event, normalized_team)
    if row is None:
        raise HTTPException(status_code=404, detail="Pit entry not found")

    photo_path = str(payload.photo_path or "").strip()
    photos = list(row.photos) if isinstance(row.photos, list) else []
    if photo_path not in photos:
        raise HTTPException(status_code=404, detail="Photo not found on this entry")

    # Only ever delete files inside the pit photos root for this entry.
    expected_prefix = f"/media/pit_photos/{normalized_event}/{normalized_team}/"
    if photo_path.startswith(expected_prefix):
        file_name = Path(photo_path).name
        candidate = (PIT_PHOTOS_ROOT / normalized_event / normalized_team / file_name).resolve()
        if candidate.is_relative_to(PIT_PHOTOS_ROOT.resolve()) and candidate.is_file():
            try:
                candidate.unlink()
            except OSError:
                logger.warning("Failed to delete pit photo file %s", candidate)

    row.photos = [item for item in photos if item != photo_path]
    db.commit()
    db.refresh(row)
    return {"ok": True, "entry": _serialize_entry(row)}
