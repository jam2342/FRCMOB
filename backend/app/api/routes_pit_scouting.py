# API routes for pit scouting.
#
# One entry per (workspace, event, team) holding the schema-driven pit form
# answers and scout-taken robot photos. Each team workspace keeps its own notes. Photos arrive as base64 JSON (not multipart) so
# they ride the same offline mutation queue as every other write.

import base64
import binascii
import io
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import sign_media_path
from app.db import models
from app.db.session import get_db
from app.core.config import settings
from app.services.team_utils import stored_team_key, team_key_sort_key
from app.services.workspace_limits import guard_pit_photo_storage, lock_pit_photo_storage, pit_photo_bytes_used
from app.services.workspaces import WorkspaceActor, require_workspace_actor, require_workspace_writer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pit-scouting", tags=["pit-scouting"])

MEDIA_ROOT = Path(__file__).resolve().parents[2] / "media"
PIT_PHOTOS_ROOT = MEDIA_ROOT / "pit_photos"

_EVENT_KEY_RE = re.compile(r"^\d{4}[a-z0-9]+$")

MAX_PHOTO_BYTES = 6 * 1024 * 1024
MAX_PHOTOS_PER_TEAM = 12
MAX_PAYLOAD_FIELDS = 80
MAX_PHOTO_PIXELS = 16 * 1024 * 1024

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
    token = stored_team_key(raw)
    if token is None:
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

def _serialize_entry(row: models.PitScoutingEntry, member_id: int) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_key": row.event_key,
        "team_key": row.team_key,
        "scout_profile": row.scout_profile,
        "payload": row.payload if isinstance(row.payload, dict) else {},
        "photos": row.photos if isinstance(row.photos, list) else [],
        # What to display: the stored paths above stay the identifiers (delete uses them).
        "photo_urls": [sign_media_path(str(path), member_id=member_id) for path in (row.photos if isinstance(row.photos, list) else [])],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }

def _load_entry(db: Session, workspace_id: int, event_key: str, team_key: str) -> models.PitScoutingEntry | None:
    return db.execute(
        select(models.PitScoutingEntry).where(
            models.PitScoutingEntry.workspace_id == workspace_id,
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


def _validate_photo(data: bytes) -> str:
    extension = _detect_image_extension(data)
    if extension is None:
        raise HTTPException(status_code=400, detail="Unsupported image format (JPEG/PNG/WebP only)")
    try:
        with Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as image:
            if image.width * image.height > MAX_PHOTO_PIXELS:
                raise HTTPException(status_code=413, detail="Photo dimensions are too large. Resize it and try again.")
            if getattr(image, "n_frames", 1) != 1:
                raise HTTPException(status_code=400, detail="Upload a single still photo.")
            image.verify()
        # Header verification alone does not reject a truncated JPEG payload.
        # Pixel and request limits bound the decode before any file is stored.
        with Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise HTTPException(status_code=400, detail="This photo is damaged or invalid. Choose a different photo.") from None
    return extension

# Phone photos carry EXIF: GPS position, device, time. The app re-encodes on a canvas,
# which drops it, but the API can be called directly, so the server drops it too.
# Photos without metadata (the app's own uploads) are stored byte for byte.
_PHOTO_METADATA_KEYS = ("exif", "xmp", "XML:com.adobe.xmp", "comment")
_SAVE_FORMATS = {".jpg": ("JPEG", {"quality": 90}), ".png": ("PNG", {}), ".webp": ("WEBP", {"quality": 90})}


def _strip_photo_metadata(data: bytes, extension: str) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        has_metadata = (
            any(image.info.get(key) for key in _PHOTO_METADATA_KEYS)
            or bool(getattr(image, "text", None))
            or len(image.getexif()) > 0
        )
        if not has_metadata:
            return data
        # Orientation lives in EXIF; apply it to the pixels before dropping it.
        upright = ImageOps.exif_transpose(image)
        save_format, options = _SAVE_FORMATS[extension]
        if save_format == "JPEG" and upright.mode not in ("RGB", "L", "CMYK"):
            upright = upright.convert("RGB")
        icc_profile = image.info.get("icc_profile")
        if icc_profile:
            options = {**options, "icc_profile": icc_profile}
        output = io.BytesIO()
        upright.save(output, format=save_format, **options)
        return output.getvalue()

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
    request: Request,
    event_key: str = Query(..., max_length=48),
    db: Session = Depends(get_db),
):
    actor = require_workspace_actor(request, db)
    normalized = _normalize_event_key(event_key)
    rows = db.execute(
        select(models.PitScoutingEntry)
        .where(
            models.PitScoutingEntry.workspace_id == actor.workspace_id,
            models.PitScoutingEntry.event_key == normalized,
        )
        .order_by(models.PitScoutingEntry.team_key.asc())
    ).scalars().all()
    return {
        "ok": True,
        "event_key": normalized,
        "count": len(rows),
        "entries": [_serialize_entry(row, actor.member.id) for row in rows],
    }


@router.post("")
def upsert_pit_entry(payload: PitEntryUpsertRequest, request: Request, db: Session = Depends(get_db)):
    actor = require_workspace_writer(request, db)
    # There may be no pit row yet to lock. Serialize first saves and photo edits
    # on the owning workspace so concurrent inserts cannot hit the unique index.
    normalized_event = _normalize_event_key(payload.event_key)
    normalized_team = _normalize_team_key(payload.team_key)
    scout_profile = str(payload.scout_profile or "").strip()[:40] or actor.member.display_name

    row = _load_entry(db, actor.workspace_id, normalized_event, normalized_team)
    if row is None:
        row = models.PitScoutingEntry(
            workspace_id=actor.workspace_id,
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
    return {"ok": True, "entry": _serialize_entry(row, actor.member.id)}

@router.post("/photo")
def upload_pit_photo(payload: PitPhotoUploadRequest, request: Request, db: Session = Depends(get_db)):
    actor = require_workspace_writer(request, db)
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

    extension = _validate_photo(data)
    data = _strip_photo_metadata(data, extension)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="Photo too large (6 MB max)")

    guard_pit_photo_storage(db, actor.workspace_id, len(data), photos_root=PIT_PHOTOS_ROOT)
    quota = int(settings.pit_photo_quota_bytes_per_workspace_event or 0)
    used = pit_photo_bytes_used(db, actor.workspace_id, normalized_event, photos_root=PIT_PHOTOS_ROOT)
    if quota > 0 and used + len(data) > quota:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Your team's pit photos for this event have reached {quota // (1024 * 1024)} MB. "
                "Delete a few old ones first."
            ),
        )

    row = _load_entry(db, actor.workspace_id, normalized_event, normalized_team)
    if row is None:
        row = models.PitScoutingEntry(
            workspace_id=actor.workspace_id,
            event_key=normalized_event,
            team_key=normalized_team,
            scout_profile=str(payload.scout_profile or "").strip()[:40] or actor.member.display_name,
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
    try:
        db.commit()
    except Exception:
        # A failed transaction must not leave an unaccounted permanent upload.
        file_path.unlink(missing_ok=True)
        raise
    db.refresh(row)
    return {"ok": True, "photo": photo_url, "photo_url": sign_media_path(photo_url, member_id=actor.member.id), "entry": _serialize_entry(row, actor.member.id)}

@router.post("/photo/delete")
def delete_pit_photo(payload: PitPhotoDeleteRequest, request: Request, db: Session = Depends(get_db)):
    actor = require_workspace_writer(request, db)
    normalized_event = _normalize_event_key(payload.event_key)
    normalized_team = _normalize_team_key(payload.team_key)
    # Only photos on this workspace's own entry can be deleted, so one team can't
    # remove files another team uploaded to the same event/team folder.
    row = _load_entry(db, actor.workspace_id, normalized_event, normalized_team)
    if row is None:
        raise HTTPException(status_code=404, detail="Pit entry not found")

    photo_path = str(payload.photo_path or "").strip()
    photos = list(row.photos) if isinstance(row.photos, list) else []
    if photo_path not in photos:
        raise HTTPException(status_code=404, detail="Photo not found on this entry")

    lock_pit_photo_storage(db)
    # Only ever delete files inside the pit photos root for this entry.
    expected_prefix = f"/media/pit_photos/{normalized_event}/{normalized_team}/"
    if photo_path.startswith(expected_prefix):
        file_name = Path(photo_path).name
        candidate = (PIT_PHOTOS_ROOT / normalized_event / normalized_team / file_name).resolve()
        if candidate.is_relative_to(PIT_PHOTOS_ROOT.resolve()) and candidate.is_file():
            try:
                candidate.unlink()
                # Upload/delete churn must not leave unbounded empty directories
                # behind for every future global-capacity scan to traverse.
                for directory in (candidate.parent, candidate.parent.parent):
                    try:
                        directory.rmdir()
                    except OSError:
                        break  # Another photo still uses it.
            except OSError:
                logger.warning("Failed to delete pit photo file %s", candidate)

    row.photos = [item for item in photos if item != photo_path]
    response_entry = _serialize_entry(row, actor.member.id)
    remove_placeholder = not row.photos and not row.payload
    if remove_placeholder:
        # Photo-only placeholder, with no scouting answers to preserve.
        db.delete(row)
    db.commit()
    if not remove_placeholder:
        db.refresh(row)
        response_entry = _serialize_entry(row, actor.member.id)
    return {"ok": True, "entry": response_entry}


MAX_SPLIT_TEAMS = 200
MAX_SPLIT_MEMBERS = 50

SOURCE_LEADER = "leader"
SOURCE_CLAIM = "claim"
SOURCE_AUTO = "auto"


class PitClaimRequest(BaseModel):
    event_key: str
    team_key: str
    force: bool = False

class PitReleaseRequest(BaseModel):
    event_key: str
    team_key: str

class PitAssignmentSetRequest(BaseModel):
    event_key: str
    team_key: str
    member_id: int | None = None

class PitAutoSplitRequest(BaseModel):
    event_key: str
    team_keys: list[str] = Field(default_factory=list, max_length=MAX_SPLIT_TEAMS)
    member_ids: list[int] = Field(min_length=1, max_length=MAX_SPLIT_MEMBERS)
    only_unscouted: bool = True
    replace_existing: bool = False



def _serialize_assignment(row: models.PitAssignment, names: dict[int, str]) -> dict[str, Any]:
    return {
        "team_key": row.team_key,
        "member_id": row.member_id,
        "member_name": names.get(row.member_id) if row.member_id is not None else None,
        "source": row.source,
        "assigned_by_member_id": row.assigned_by_member_id,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }

def _member_names(db: Session, workspace_id: int) -> dict[int, str]:
    rows = db.execute(
        select(models.TeamWorkspaceMember.id, models.TeamWorkspaceMember.display_name).where(
            models.TeamWorkspaceMember.workspace_id == workspace_id,
        )
    ).all()
    return {int(row[0]): str(row[1]) for row in rows}

def _list_assignments(db: Session, workspace_id: int, event_key: str) -> list[dict[str, Any]]:
    rows = db.execute(
        select(models.PitAssignment).where(
            models.PitAssignment.workspace_id == workspace_id,
            models.PitAssignment.event_key == event_key,
        )
    ).scalars().all()
    names = _member_names(db, workspace_id)
    ordered = sorted(rows, key=lambda row: team_key_sort_key(row.team_key))
    return [_serialize_assignment(row, names) for row in ordered]

def _load_assignment(db: Session, workspace_id: int, event_key: str, team_key: str) -> models.PitAssignment | None:
    return db.execute(
        select(models.PitAssignment).where(
            models.PitAssignment.workspace_id == workspace_id,
            models.PitAssignment.event_key == event_key,
            models.PitAssignment.team_key == team_key,
        )
    ).scalar_one_or_none()

def _active_member(db: Session, workspace_id: int, member_id: int) -> models.TeamWorkspaceMember:
    member = db.get(models.TeamWorkspaceMember, member_id)
    if member is None or member.workspace_id != workspace_id or member.removed_at is not None:
        raise HTTPException(status_code=404, detail="That member isn't in this workspace.")
    return member

def _require_leader(actor: WorkspaceActor) -> None:
    if not actor.is_leader:
        raise HTTPException(status_code=403, detail="Only workspace leaders can do that.")


@router.get("/assignments")
def list_pit_assignments(
    request: Request,
    event_key: str = Query(..., max_length=48),
    db: Session = Depends(get_db),
):
    actor = require_workspace_actor(request, db)
    normalized = _normalize_event_key(event_key)
    return {"ok": True, "assignments": _list_assignments(db, actor.workspace_id, normalized)}


@router.post("/assignments/claim")
def claim_pit_team(payload: PitClaimRequest, request: Request, db: Session = Depends(get_db)):
    actor = require_workspace_writer(request, db)
    normalized_event = _normalize_event_key(payload.event_key)
    normalized_team = _normalize_team_key(payload.team_key)
    if payload.force:
        _require_leader(actor)

    workspace_id, member_id = actor.workspace_id, actor.member.id
    # Two scouts tapping the same free team at once both see no row and both insert; the
    # unique index lets one win. The loser rolls back and runs the normal rules again.
    for attempt in (1, 2):
        row = _load_assignment(db, workspace_id, normalized_event, normalized_team)
        holder = None
        if row is not None and row.member_id is not None and row.member_id != member_id:
            holder = db.get(models.TeamWorkspaceMember, row.member_id)
            if holder is not None and holder.removed_at is not None:
                holder = None
        if holder is not None and not payload.force:
            raise HTTPException(
                status_code=409,
                detail=f"{normalized_team} is already taken by {holder.display_name}.",
            )
        if row is None:
            row = models.PitAssignment(
                workspace_id=workspace_id,
                event_key=normalized_event,
                team_key=normalized_team,
                member_id=member_id,
                assigned_by_member_id=member_id,
                source=SOURCE_CLAIM,
            )
            db.add(row)
        elif row.member_id != member_id:
            row.member_id = member_id
            row.assigned_by_member_id = member_id
            row.source = SOURCE_CLAIM
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise HTTPException(status_code=409, detail=f"{normalized_team} was just taken. Try again.") from None
            continue
        db.refresh(row)
        return {"ok": True, "assignment": _serialize_assignment(row, _member_names(db, workspace_id))}


@router.post("/assignments/release")
def release_pit_team(payload: PitReleaseRequest, request: Request, db: Session = Depends(get_db)):
    actor = require_workspace_writer(request, db)
    normalized_event = _normalize_event_key(payload.event_key)
    normalized_team = _normalize_team_key(payload.team_key)
    row = _load_assignment(db, actor.workspace_id, normalized_event, normalized_team)
    if row is None:
        return {"ok": True, "released": False}
    if row.member_id is not None and row.member_id != actor.member.id and not actor.is_leader:
        raise HTTPException(status_code=403, detail="Only the scout who took this team, or a leader, can release it.")
    db.delete(row)
    db.commit()
    return {"ok": True, "released": True}


@router.post("/assignments/set")
def set_pit_assignment(payload: PitAssignmentSetRequest, request: Request, db: Session = Depends(get_db)):
    actor = require_workspace_writer(request, db)
    _require_leader(actor)
    normalized_event = _normalize_event_key(payload.event_key)
    normalized_team = _normalize_team_key(payload.team_key)
    if payload.member_id is not None:
        _active_member(db, actor.workspace_id, payload.member_id)

    workspace_id, leader_id = actor.workspace_id, actor.member.id
    for attempt in (1, 2):
        row = _load_assignment(db, workspace_id, normalized_event, normalized_team)
        if payload.member_id is None:
            if row is not None:
                db.delete(row)
                db.commit()
            return {"ok": True, "assignment": None}
        if row is None:
            row = models.PitAssignment(
                workspace_id=workspace_id,
                event_key=normalized_event,
                team_key=normalized_team,
            )
            db.add(row)
        row.member_id = payload.member_id
        row.assigned_by_member_id = leader_id
        row.source = SOURCE_LEADER
        try:
            db.commit()
        except IntegrityError:
            # A competing insert won; the retry reloads that row and updates it.
            db.rollback()
            if attempt == 2:
                raise HTTPException(status_code=409, detail="That team was just changed. Try again.") from None
            continue
        db.refresh(row)
        return {"ok": True, "assignment": _serialize_assignment(row, _member_names(db, workspace_id))}


@router.post("/assignments/auto-split")
def auto_split_pit_teams(payload: PitAutoSplitRequest, request: Request, db: Session = Depends(get_db)):
    actor = require_workspace_writer(request, db)
    _require_leader(actor)
    normalized_event = _normalize_event_key(payload.event_key)
    team_keys = sorted({_normalize_team_key(key) for key in payload.team_keys}, key=team_key_sort_key)
    # Sorted by id so the same request always deals teams out the same way.
    member_ids = sorted(set(payload.member_ids))
    for member_id in member_ids:
        _active_member(db, actor.workspace_id, member_id)

    workspace_id, leader_id = actor.workspace_id, actor.member.id
    for attempt in (1, 2):
        existing = {
            row.team_key: row
            for row in db.execute(
                select(models.PitAssignment).where(
                    models.PitAssignment.workspace_id == workspace_id,
                    models.PitAssignment.event_key == normalized_event,
                )
            ).scalars()
        }
        scouted: set[str] = set()
        if payload.only_unscouted:
            scouted = set(
                db.execute(
                    select(models.PitScoutingEntry.team_key).where(
                        models.PitScoutingEntry.workspace_id == workspace_id,
                        models.PitScoutingEntry.event_key == normalized_event,
                    )
                ).scalars()
            )

        eligible = []
        for team_key in team_keys:
            if team_key in scouted:
                continue
            current = existing.get(team_key)
            if current is not None and current.member_id is not None and not payload.replace_existing:
                continue
            eligible.append(team_key)

        # Count what the chosen scouts already hold, so the split ends up balanced overall.
        eligible_set = set(eligible)
        load = {member_id: 0 for member_id in member_ids}
        for team_key, row in existing.items():
            if row.member_id in load and team_key not in eligible_set:
                load[row.member_id] += 1

        for team_key in eligible:
            member_id = min(member_ids, key=lambda candidate: (load[candidate], candidate))
            load[member_id] += 1
            row = existing.get(team_key)
            if row is None:
                row = models.PitAssignment(
                    workspace_id=workspace_id,
                    event_key=normalized_event,
                    team_key=team_key,
                )
                db.add(row)
            row.member_id = member_id
            row.assigned_by_member_id = leader_id
            row.source = SOURCE_AUTO
        try:
            db.commit()
        except IntegrityError:
            # A competing insert won; the retry reloads and updates that row.
            db.rollback()
            if attempt == 2:
                raise HTTPException(status_code=409, detail="Those teams were just changed. Try again.") from None
            continue
        return {"ok": True, "assignments": _list_assignments(db, workspace_id, normalized_event)}
