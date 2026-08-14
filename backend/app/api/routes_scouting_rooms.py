from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging
import re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.exc import DatabaseError, IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import settings
from app.core.security import (
    ROOM_ACCESS_HEADER,
    ROOM_ROLE_EDITOR,
    ROOM_ROLE_OWNER,
    issue_room_access_token,
    parse_room_access_token,
    require_write_access,
    room_access_allows,
)
from app.db import models
from app.db.session import SessionLocal, get_db
from app.services.scouting_rooms.bus import scouting_room_bus
from app.services.scouting_rooms.realtime import scouting_room_hub
from app.services.utils import _clamp, pg_sqlstate_code as _pg_sqlstate_shared

router = APIRouter(prefix="/scouting/rooms", tags=["scouting-rooms"])
logger = logging.getLogger(__name__)

_ROOM_KEY_RE = re.compile(r"[^a-z0-9_-]+")
_ROOM_BUS_CONTROL_DISCONNECT_PROFILE = "room_control_disconnect_profile"
_ROOM_REALTIME_TIMEOUT_SEC = 1.5

def _pg_sqlstate(exc: Exception) -> str | None:
    return _pg_sqlstate_shared(exc)

def _exc_message_lower(exc: Exception) -> str:
    candidate = getattr(exc, "orig", None) or exc
    return str(candidate).strip().lower()

def _contains_any_table(message_lower: str, table_names: tuple[str, ...]) -> bool:
    if not table_names:
        return True
    return any(str(name or "").strip().lower() in message_lower for name in table_names)

def _is_missing_table_error(exc: Exception, *_table_names: str) -> bool:
    # 42P01 = undefined_table
    message_lower = _exc_message_lower(exc)
    if _pg_sqlstate(exc) == "42P01":
        if not _table_names:
            return True
        return _contains_any_table(message_lower, tuple(_table_names))
    missing_markers = (
        "no such table",
        "does not exist",
    )
    if not any(marker in message_lower for marker in missing_markers):
        return False
    return _contains_any_table(message_lower, tuple(_table_names))

def _is_table_privilege_error(exc: Exception, *_table_names: str) -> bool:
    # 42501 = insufficient_privilege
    message_lower = _exc_message_lower(exc)
    if _pg_sqlstate(exc) == "42501":
        if not _table_names:
            return True
        return _contains_any_table(message_lower, tuple(_table_names))
    privilege_markers = (
        "permission denied",
        "insufficient privilege",
        "insufficient privileges",
    )
    if not any(marker in message_lower for marker in privilege_markers):
        return False
    return _contains_any_table(message_lower, tuple(_table_names))

def _is_table_schema_error(exc: Exception, *_table_names: str) -> bool:
    # 42703 = undefined_column, 42804 = datatype_mismatch
    message_lower = _exc_message_lower(exc)
    if _pg_sqlstate(exc) in {"42703", "42804"}:
        if not _table_names:
            return True
        return _contains_any_table(message_lower, tuple(_table_names))
    schema_markers = (
        "undefined column",
        "no such column",
        "datatype mismatch",
    )
    if not any(marker in message_lower for marker in schema_markers):
        return False
    return _contains_any_table(message_lower, tuple(_table_names))

def _is_table_unavailable_error(exc: Exception, *table_names: str) -> bool:
    return (
        _is_missing_table_error(exc, *table_names)
        or _is_table_privilege_error(exc, *table_names)
        or _is_table_schema_error(exc, *table_names)
    )

def _is_missing_room_leaders_table_error(exc: Exception) -> bool:
    return _is_table_unavailable_error(exc, "scouting_room_leaders")

def _bootstrap_missing_room_table(
    db: Session,
    table_name: str,
    *,
    trigger_exc: Exception | None = None,
) -> bool:
    # Check whether a missing scouting-room table should be retried.
    #
    # Previously this auto-created tables at runtime via ``table.create()``.
    # That hid migration gaps and could cause schema drift between
    # environments.  Now it always logs an actionable error and returns
    # ``False`` — callers fall through to their graceful-degradation path
    # (empty list / 503).  Run ``alembic upgrade head`` to fix.
    normalized = str(table_name or "").strip().lower()
    if not normalized:
        return False

    logger.error(
        "Scouting-room table '%s' is missing. "
        "Run `alembic upgrade head` to apply migrations. (trigger=%s)",
        normalized,
        trigger_exc,
    )
    with suppress(Exception):
        db.rollback()
    return False

def _is_database_transfer_quota_error(exc: Exception) -> bool:
    # Neon surfaces the quota breach as a connection-time message, not a SQLSTATE.
    return "data transfer quota" in str(exc).lower()

def _database_unavailable_detail(default_detail: str, exc: Exception) -> str:
    if _is_database_transfer_quota_error(exc):
        return (
            "Scouting room creation is temporarily unavailable: Neon data transfer quota exceeded. "
            "Upgrade/reset Neon quota, then retry."
        )
    return default_detail

def _normalize_room_key(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    value = _ROOM_KEY_RE.sub("", value)
    value = value[:48]
    return value

def _normalize_event_key(raw: str | None) -> str | None:
    value = str(raw or "").strip().lower()
    if not value:
        return None
    return value[:48]

def _normalize_team_key(raw: str | None) -> str | None:
    value = str(raw or "").strip().lower()
    if not value:
        return None
    return value[:24]

def _normalize_match_key(raw: str | None) -> str | None:
    value = str(raw or "").strip().lower()
    if not value:
        return None
    return value[:80]

def _normalize_scout_profile(raw: str | None) -> str:
    value = str(raw or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value[:40]

def _normalize_scout_profile_lookup(raw: str | None) -> str:
    return _normalize_scout_profile(raw).lower()

def _scout_profiles_match(left: str | None, right: str | None) -> bool:
    left_norm = _normalize_scout_profile_lookup(left)
    right_norm = _normalize_scout_profile_lookup(right)
    if not left_norm or not right_norm:
        return False
    return left_norm == right_norm

def _parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _require_scout_profile(raw: str | None, *, context: str) -> str:
    profile = _normalize_scout_profile(raw)
    if not profile:
        raise HTTPException(
            status_code=400,
            detail=f"scout_profile is required for {context}. Enter your name to continue.",
        )
    return profile

def _clamp_0_100(value: float) -> float:
    return _clamp(float(value), 0.0, 100.0)

def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None

def _room_ws_path(room_key: str) -> str:
    return f"/scouting/rooms/{room_key}/ws"

async def _broadcast_room_message(room_key: str, payload: dict[str, Any]) -> None:
    try:
        await scouting_room_hub.broadcast(room_key, payload)
    except Exception as exc:
        logger.warning(
            "Scouting room local broadcast failed for %s payload_type=%s: %s",
            room_key,
            str(payload.get("type") or "").strip().lower() or "unknown",
            exc,
        )
    try:
        await scouting_room_bus.publish(room_key, payload)
    except Exception as exc:
        logger.warning(
            "Scouting room redis publish failed for %s payload_type=%s: %s",
            room_key,
            str(payload.get("type") or "").strip().lower() or "unknown",
            exc,
        )

async def _safe_presence_snapshot(room_key: str, *, context: str) -> list[dict[str, Any]]:
    try:
        return await asyncio.wait_for(
            scouting_room_hub.presence_snapshot(room_key),
            timeout=_ROOM_REALTIME_TIMEOUT_SEC,
        )
    except Exception as exc:
        logger.warning(
            "Failed to load scouting room presence for %s during %s: %s",
            room_key,
            context,
            exc,
        )
        return []

async def _safe_touch_http_presence(
    room_key: str,
    *,
    scout_profile: str,
    client_id: str | None,
    context: str,
    fallback_presence: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    try:
        return await asyncio.wait_for(
            scouting_room_hub.touch_http_presence(
                room_key,
                scout_profile=scout_profile,
                client_id=client_id,
            ),
            timeout=_ROOM_REALTIME_TIMEOUT_SEC,
        )
    except Exception as exc:
        logger.warning(
            "Failed to update scouting room HTTP presence for %s (%s) during %s: %s",
            room_key,
            scout_profile,
            context,
            exc,
        )
        return list(fallback_presence or [])

async def _safe_profile_has_client_presence(
    room_key: str,
    *,
    scout_profile: str,
    client_id: str,
    context: str,
) -> bool:
    try:
        return await asyncio.wait_for(
            scouting_room_hub.profile_has_client_presence(
                room_key,
                scout_profile=scout_profile,
                client_id=client_id,
            ),
            timeout=_ROOM_REALTIME_TIMEOUT_SEC,
        )
    except Exception as exc:
        logger.warning(
            "Failed to verify scouting room client presence for %s (%s) during %s: %s",
            room_key,
            scout_profile,
            context,
            exc,
        )
        return False

async def _broadcast_presence_message(
    room_key: str, presence: list[dict[str, Any]] | None = None
) -> None:
    members = presence if presence is not None else await _safe_presence_snapshot(
        room_key,
        context="presence broadcast",
    )
    leader_profile = None
    leader_source = None
    db = SessionLocal()
    try:
        try:
            room = db.get(models.ScoutingRoom, room_key)
            if room is not None:
                secondary_leaders = _load_room_secondary_leader_profiles(db, room_key)
                leader_profile, leader_source = _resolve_room_leader(
                    room,
                    members,
                    secondary_leader_profiles=secondary_leaders,
                )
        except Exception as exc:
            logger.warning(
                "Failed to compute scouting room leader while broadcasting presence for %s: %s",
                room_key,
                exc,
            )
    finally:
        db.close()
    await _broadcast_room_message(
        room_key,
        {
            "type": "presence",
            "room_key": room_key,
            "members": members,
            "leader_scout_profile": leader_profile,
            "leader_source": leader_source,
        },
    )

def _serialize_room(
    room: models.ScoutingRoom,
    *,
    presence: list[dict[str, Any]],
    room_role: str | None = None,
    secondary_leader_scout_profiles: list[str] | None = None,
) -> dict[str, Any]:
    secondary_leaders = [
        profile
        for profile in (secondary_leader_scout_profiles or [])
        if not _scout_profiles_match(profile, room.created_by)
    ]
    leader_scout_profile, leader_source = _resolve_room_leader(
        room,
        presence,
        secondary_leader_profiles=secondary_leaders,
    )
    payload = {
        "room_key": room.room_key,
        "event_key": room.event_key,
        "title": room.title,
        "created_by": room.created_by,
        "archived": bool(room.archived),
        "created_at": room.created_at.isoformat() if room.created_at else None,
        "updated_at": room.updated_at.isoformat() if room.updated_at else None,
        "last_activity_at": room.last_activity_at.isoformat() if room.last_activity_at else None,
        "leader_scout_profile": leader_scout_profile,
        "leader_source": leader_source,
        "presence": presence,
        "secondary_leader_scout_profiles": secondary_leaders,
        "ws_path": _room_ws_path(room.room_key),
    }
    if room_role:
        payload["room_role"] = room_role
    return payload

def _leader_presence_rank_key(member: dict[str, Any]) -> tuple[float, float, float, str]:
    first_connected = _parse_iso_datetime(member.get("first_connected_at"))
    last_connected = _parse_iso_datetime(member.get("last_connected_at"))
    last_seen = _parse_iso_datetime(member.get("last_seen_at"))
    # Prefer longest-present members first so leadership handoff after disconnect
    # goes to the next oldest active member in the room.
    first_connected_ts = first_connected.timestamp() if isinstance(first_connected, datetime) else float("inf")
    last_connected_ts = last_connected.timestamp() if isinstance(last_connected, datetime) else float("inf")
    # On equal connection age, prefer more recently seen members.
    seen_rank = -last_seen.timestamp() if isinstance(last_seen, datetime) else float("inf")
    profile = _normalize_scout_profile_lookup(member.get("scout_profile"))
    return (first_connected_ts, last_connected_ts, seen_rank, profile)

def _resolve_room_leader(
    room: models.ScoutingRoom,
    presence: list[dict[str, Any]],
    *,
    secondary_leader_profiles: list[str] | None = None,
) -> tuple[str | None, str]:
    owner_profile = _normalize_scout_profile(room.created_by)
    owner_lookup = _normalize_scout_profile_lookup(owner_profile)
    if owner_lookup:
        for member in presence:
            member_profile = _normalize_scout_profile(member.get("scout_profile"))
            if _scout_profiles_match(member_profile, owner_lookup):
                return member_profile or owner_profile, "owner_present"

    secondary_profiles = []
    seen_secondary: set[str] = set()
    for profile in secondary_leader_profiles or []:
        normalized_profile = _normalize_scout_profile(profile)
        lookup = _normalize_scout_profile_lookup(normalized_profile)
        if not lookup or lookup in seen_secondary or (owner_lookup and lookup == owner_lookup):
            continue
        seen_secondary.add(lookup)
        secondary_profiles.append(normalized_profile)

    if secondary_profiles and presence:
        secondary_lookup = {_normalize_scout_profile_lookup(profile): profile for profile in secondary_profiles}
        matched_secondary = [
            member
            for member in presence
            if _normalize_scout_profile_lookup(member.get("scout_profile")) in secondary_lookup
        ]
        if matched_secondary:
            chosen_secondary = min(matched_secondary, key=_leader_presence_rank_key)
            member_profile = _normalize_scout_profile(chosen_secondary.get("scout_profile"))
            member_lookup = _normalize_scout_profile_lookup(member_profile)
            return member_profile or secondary_lookup.get(member_lookup), "secondary_leader_present"

    if presence:
        fallback = min(presence, key=_leader_presence_rank_key)
        fallback_profile = _normalize_scout_profile(fallback.get("scout_profile"))
        if fallback_profile:
            return fallback_profile, "presence_fallback"
    if owner_profile:
        return owner_profile, "owner_offline"
    if secondary_profiles:
        return secondary_profiles[0], "secondary_leader_offline"
    return None, "no_leader"

def _presence_connections_for_profile(
    presence: list[dict[str, Any]],
    scout_profile: str,
) -> int:
    target = _normalize_scout_profile_lookup(scout_profile)
    if not target:
        return 0
    for member in presence:
        if not isinstance(member, dict):
            continue
        if _scout_profiles_match(member.get("scout_profile"), target):
            try:
                return max(0, int(member.get("connections") or 0))
            except Exception:
                return 1
    return 0

def _room_profile_claim_conflicts(
    *,
    room_key: str,
    scout_profile: str,
    presence: list[dict[str, Any]],
    existing_room_access_payload: dict[str, Any] | None,
) -> bool:
    active_connections = _presence_connections_for_profile(presence, scout_profile)
    if active_connections <= 0:
        return False
    if room_access_allows(
        existing_room_access_payload,
        room_key=room_key,
        scout_profile=scout_profile,
        require_write=True,
    ):
        return False
    return True

def _serialize_entry(row: models.ScoutingRoomEntry) -> dict[str, Any]:
    payload = row.payload if isinstance(row.payload, dict) else {}
    entry = dict(payload)
    if not entry.get("id"):
        entry["id"] = row.client_entry_id or f"room-entry-{row.id}"
    if not entry.get("scout_profile"):
        entry["scout_profile"] = row.scout_profile
    if not entry.get("event_key") and row.event_key:
        entry["event_key"] = row.event_key
    if not entry.get("match_key") and row.match_key:
        entry["match_key"] = row.match_key
    if not entry.get("team_key") and row.team_key:
        entry["team_key"] = row.team_key

    return {
        "server_id": row.id,
        "room_key": row.room_key,
        "event_key": row.event_key,
        "match_key": row.match_key,
        "team_key": row.team_key,
        "scout_profile": row.scout_profile,
        "client_entry_id": row.client_entry_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "entry": entry,
        "scores": {
            "total_points": row.total_points,
            "driver_score_0_100": row.driver_score_0_100,
            "manual_rating_0_100": row.manual_rating_0_100,
            "scouting_api_rating_0_100": row.scouting_api_rating_0_100,
            "overall_scout_rating_0_100": _entry_overall_scout_rating(payload),
        },
    }

def _serialize_assignment(row: models.ScoutingRoomAssignment) -> dict[str, Any]:
    return {
        "id": row.id,
        "room_key": row.room_key,
        "event_key": row.event_key,
        "match_key": row.match_key,
        "team_key": row.team_key,
        "assigned_scout_profile": row.assigned_scout_profile,
        "assigned_by_scout_profile": row.assigned_by_scout_profile,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }

def _load_room_assignments(
    db: Session,
    room_key: str,
    *,
    event_key: str | None = None,
    limit: int = 1500,
    _allow_bootstrap_retry: bool = True,
) -> list[models.ScoutingRoomAssignment]:
    try:
        query = db.query(models.ScoutingRoomAssignment).filter(
            models.ScoutingRoomAssignment.room_key == room_key
        )
        normalized_event = _normalize_event_key(event_key)
        if normalized_event:
            query = query.filter(models.ScoutingRoomAssignment.event_key == normalized_event)
        return (
            query.order_by(
                models.ScoutingRoomAssignment.updated_at.desc(),
                models.ScoutingRoomAssignment.id.desc(),
            )
            .limit(max(1, min(int(limit), 3000)))
            .all()
        )
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if (
            _allow_bootstrap_retry
            and _is_missing_table_error(exc, "scouting_room_assignments")
            and _bootstrap_missing_room_table(
                db,
                "scouting_room_assignments",
                trigger_exc=exc,
            )
        ):
            return _load_room_assignments(
                db,
                room_key,
                event_key=event_key,
                limit=limit,
                _allow_bootstrap_retry=False,
            )
        if _is_table_unavailable_error(exc, "scouting_room_assignments"):
            return []
        raise

def _assignments_for_scout(
    rows: list[models.ScoutingRoomAssignment],
    scout_profile: str | None,
) -> list[models.ScoutingRoomAssignment]:
    target = _normalize_scout_profile_lookup(scout_profile)
    if not target:
        return []
    return [
        row
        for row in rows
        if _normalize_scout_profile_lookup(row.assigned_scout_profile) == target
    ]

def _load_room_secondary_leader_rows(
    db: Session,
    room_key: str,
    *,
    limit: int = 300,
    _allow_bootstrap_retry: bool = True,
) -> list[models.ScoutingRoomLeader]:
    try:
        return (
            db.query(models.ScoutingRoomLeader)
            .filter(models.ScoutingRoomLeader.room_key == room_key)
            .order_by(
                models.ScoutingRoomLeader.scout_profile_norm.asc(),
                models.ScoutingRoomLeader.id.asc(),
            )
            .limit(max(1, min(int(limit), 1000)))
            .all()
        )
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if (
            _allow_bootstrap_retry
            and _is_missing_table_error(exc, "scouting_room_leaders")
            and _bootstrap_missing_room_table(
                db,
                "scouting_room_leaders",
                trigger_exc=exc,
            )
        ):
            return _load_room_secondary_leader_rows(
                db,
                room_key,
                limit=limit,
                _allow_bootstrap_retry=False,
            )
        if _is_missing_room_leaders_table_error(exc):
            return []
        raise

def _secondary_leader_profiles(rows: list[models.ScoutingRoomLeader]) -> list[str]:
    profiles: list[str] = []
    seen: set[str] = set()
    for row in rows:
        profile = _normalize_scout_profile(row.scout_profile)
        lookup = _normalize_scout_profile_lookup(profile)
        if not lookup or lookup in seen:
            continue
        seen.add(lookup)
        profiles.append(profile)
    return profiles

def _load_room_secondary_leader_profiles(
    db: Session,
    room_key: str,
    *,
    limit: int = 300,
) -> list[str]:
    return _secondary_leader_profiles(
        _load_room_secondary_leader_rows(
            db,
            room_key,
            limit=limit,
        )
    )

def _upsert_room_secondary_leader(
    db: Session,
    *,
    room: models.ScoutingRoom,
    scout_profile: str,
    added_by_scout_profile: str,
    commit: bool = True,
    _allow_bootstrap_retry: bool = True,
) -> tuple[models.ScoutingRoomLeader, bool]:
    normalized_profile = _normalize_scout_profile(scout_profile)
    normalized_lookup = _normalize_scout_profile_lookup(normalized_profile)
    if not normalized_lookup:
        raise HTTPException(status_code=400, detail="scout_profile is required for room leader updates.")
    normalized_added_by = _normalize_scout_profile(added_by_scout_profile)
    if not normalized_added_by:
        raise HTTPException(status_code=400, detail="Actor scout_profile is required for room leader updates.")

    try:
        row = (
            db.query(models.ScoutingRoomLeader)
            .filter(
                models.ScoutingRoomLeader.room_key == room.room_key,
                models.ScoutingRoomLeader.scout_profile_norm == normalized_lookup,
            )
            .first()
        )
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if (
            _allow_bootstrap_retry
            and _is_missing_table_error(exc, "scouting_room_leaders")
            and _bootstrap_missing_room_table(
                db,
                "scouting_room_leaders",
                trigger_exc=exc,
            )
        ):
            return _upsert_room_secondary_leader(
                db,
                room=room,
                scout_profile=scout_profile,
                added_by_scout_profile=added_by_scout_profile,
                commit=commit,
                _allow_bootstrap_retry=False,
            )
        if _is_missing_room_leaders_table_error(exc):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Secondary leader updates are temporarily unavailable. "
                    "Apply database migration 20260305_0006_scouting_room_leaders "
                    "and ensure DB privileges for scouting_room_leaders."
                ),
            ) from exc
        raise
    now = datetime.now(timezone.utc)
    created = False
    if row is None:
        row = models.ScoutingRoomLeader(
            room_key=room.room_key,
            scout_profile=normalized_profile,
            scout_profile_norm=normalized_lookup,
            added_by_scout_profile=normalized_added_by,
            added_by_scout_profile_norm=_normalize_scout_profile_lookup(normalized_added_by),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        created = True
    else:
        row.scout_profile = normalized_profile
        row.scout_profile_norm = normalized_lookup
        row.added_by_scout_profile = normalized_added_by
        row.added_by_scout_profile_norm = _normalize_scout_profile_lookup(normalized_added_by)
        row.updated_at = now
        db.add(row)

    _touch_room_activity(db, room, commit=False)
    db.add(room)
    try:
        if commit:
            db.commit()
            db.refresh(row)
            db.refresh(room)
        else:
            db.flush()
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if (
            _allow_bootstrap_retry
            and _is_missing_table_error(exc, "scouting_room_leaders")
            and _bootstrap_missing_room_table(
                db,
                "scouting_room_leaders",
                trigger_exc=exc,
            )
        ):
            return _upsert_room_secondary_leader(
                db,
                room=room,
                scout_profile=scout_profile,
                added_by_scout_profile=added_by_scout_profile,
                commit=commit,
                _allow_bootstrap_retry=False,
            )
        if _is_missing_room_leaders_table_error(exc):
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Secondary leader updates are temporarily unavailable. "
                    "Apply database migration 20260305_0006_scouting_room_leaders "
                    "and ensure DB privileges for scouting_room_leaders."
                ),
            ) from exc
        raise
    return row, created

def _remove_room_secondary_leader(
    db: Session,
    *,
    room: models.ScoutingRoom,
    scout_profile: str,
    commit: bool = True,
    _allow_bootstrap_retry: bool = True,
) -> bool:
    normalized_lookup = _normalize_scout_profile_lookup(scout_profile)
    if not normalized_lookup:
        return False
    try:
        row = (
            db.query(models.ScoutingRoomLeader)
            .filter(
                models.ScoutingRoomLeader.room_key == room.room_key,
                models.ScoutingRoomLeader.scout_profile_norm == normalized_lookup,
            )
            .first()
        )
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if (
            _allow_bootstrap_retry
            and _is_missing_table_error(exc, "scouting_room_leaders")
            and _bootstrap_missing_room_table(
                db,
                "scouting_room_leaders",
                trigger_exc=exc,
            )
        ):
            return _remove_room_secondary_leader(
                db,
                room=room,
                scout_profile=scout_profile,
                commit=commit,
                _allow_bootstrap_retry=False,
            )
        if _is_missing_room_leaders_table_error(exc):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Secondary leader updates are temporarily unavailable. "
                    "Apply database migration 20260305_0006_scouting_room_leaders "
                    "and ensure DB privileges for scouting_room_leaders."
                ),
            ) from exc
        raise
    if row is None:
        return False
    db.delete(row)
    _touch_room_activity(db, room, commit=False)
    db.add(room)
    try:
        if commit:
            db.commit()
            db.refresh(room)
        else:
            db.flush()
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if (
            _allow_bootstrap_retry
            and _is_missing_table_error(exc, "scouting_room_leaders")
            and _bootstrap_missing_room_table(
                db,
                "scouting_room_leaders",
                trigger_exc=exc,
            )
        ):
            return _remove_room_secondary_leader(
                db,
                room=room,
                scout_profile=scout_profile,
                commit=commit,
                _allow_bootstrap_retry=False,
            )
        if _is_missing_room_leaders_table_error(exc):
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Secondary leader updates are temporarily unavailable. "
                    "Apply database migration 20260305_0006_scouting_room_leaders "
                    "and ensure DB privileges for scouting_room_leaders."
                ),
            ) from exc
        raise
    return True

def _touch_room_activity(db: Session, room: models.ScoutingRoom, *, commit: bool = True) -> None:
    now = datetime.now(timezone.utc)
    room.last_activity_at = now
    room.updated_at = now
    db.add(room)
    if not commit:
        return
    try:
        db.commit()
        db.refresh(room)
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if _is_table_unavailable_error(exc, "scouting_rooms"):
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Scouting rooms are temporarily unavailable. "
                    "Apply database migration 20260219_0002_scouting_rooms "
                    "and ensure DB privileges for scouting_rooms."
                ),
            ) from exc
        raise

def _upsert_room_assignment(
    db: Session,
    *,
    room: models.ScoutingRoom,
    match_key: str,
    team_key: str,
    assigned_scout_profile: str,
    assigned_by_scout_profile: str,
    event_key: str | None,
    commit: bool = True,
    _allow_bootstrap_retry: bool = True,
    _allow_stale_retry: bool = True,
) -> models.ScoutingRoomAssignment:
    normalized_match_key = _normalize_match_key(match_key)
    normalized_team_key = _normalize_team_key(team_key)
    if not normalized_match_key or not normalized_team_key:
        raise HTTPException(status_code=400, detail="match_key and team_key are required for room assignments.")

    match = db.get(models.Match, normalized_match_key)
    if match is None:
        raise HTTPException(status_code=400, detail=f"Match {normalized_match_key} was not found.")
    team = db.get(models.Team, normalized_team_key)
    if team is None:
        raise HTTPException(status_code=400, detail=f"Team {normalized_team_key} was not found.")

    normalized_assigned_scout = _normalize_scout_profile(assigned_scout_profile)
    if not normalized_assigned_scout:
        raise HTTPException(status_code=400, detail="assigned_scout_profile is required.")
    normalized_assigned_by = _normalize_scout_profile(assigned_by_scout_profile)
    if not normalized_assigned_by:
        raise HTTPException(status_code=400, detail="assigned_by_scout_profile is required.")

    normalized_event = _normalize_event_key(event_key) or _normalize_event_key(match.event_key) or room.event_key

    try:
        row = (
            db.query(models.ScoutingRoomAssignment)
            .filter(
                models.ScoutingRoomAssignment.room_key == room.room_key,
                models.ScoutingRoomAssignment.match_key == normalized_match_key,
                models.ScoutingRoomAssignment.team_key == normalized_team_key,
            )
            .first()
        )
        now = datetime.now(timezone.utc)
        if row is None:
            row = models.ScoutingRoomAssignment(
                room_key=room.room_key,
                event_key=normalized_event,
                match_key=normalized_match_key,
                team_key=normalized_team_key,
                assigned_scout_profile=normalized_assigned_scout,
                assigned_scout_profile_norm=_normalize_scout_profile_lookup(normalized_assigned_scout),
                assigned_by_scout_profile=normalized_assigned_by,
                assigned_by_scout_profile_norm=_normalize_scout_profile_lookup(normalized_assigned_by),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        else:
            row.event_key = normalized_event
            row.assigned_scout_profile = normalized_assigned_scout
            row.assigned_scout_profile_norm = _normalize_scout_profile_lookup(normalized_assigned_scout)
            row.assigned_by_scout_profile = normalized_assigned_by
            row.assigned_by_scout_profile_norm = _normalize_scout_profile_lookup(normalized_assigned_by)
            row.updated_at = now
            db.add(row)

        if normalized_event and not room.event_key:
            room.event_key = normalized_event
        _touch_room_activity(db, room, commit=False)
        db.add(room)
        if commit:
            db.commit()
            db.refresh(row)
            db.refresh(room)
        else:
            db.flush()
        return row
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if (
            _allow_bootstrap_retry
            and _is_missing_table_error(exc, "scouting_room_assignments")
            and _bootstrap_missing_room_table(
                db,
                "scouting_room_assignments",
                trigger_exc=exc,
            )
        ):
            return _upsert_room_assignment(
                db,
                room=room,
                match_key=match_key,
                team_key=team_key,
                assigned_scout_profile=assigned_scout_profile,
                assigned_by_scout_profile=assigned_by_scout_profile,
                event_key=event_key,
                commit=commit,
                _allow_bootstrap_retry=False,
            )
        if _is_table_unavailable_error(exc, "scouting_room_assignments"):
            if commit:
                db.rollback()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Scouting room assignments are temporarily unavailable. "
                    "Apply database migration 20260305_0005_scouting_room_assignments "
                    "and ensure DB privileges for scouting_room_assignments."
                ),
            ) from exc
        raise
    except StaleDataError as exc:
        # Concurrent assignment replace/delete can invalidate the row selected above.
        # Retry once against a fresh transaction so high-concurrency updates do not 500.
        if _allow_stale_retry:
            if commit:
                db.rollback()
            return _upsert_room_assignment(
                db,
                room=room,
                match_key=match_key,
                team_key=team_key,
                assigned_scout_profile=assigned_scout_profile,
                assigned_by_scout_profile=assigned_by_scout_profile,
                event_key=event_key,
                commit=commit,
                _allow_bootstrap_retry=False,
                _allow_stale_retry=False,
            )
        if commit:
            db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Assignment update conflicted with a concurrent room sync. "
                "Retry the request."
            ),
        ) from exc

def _clear_room_assignment(
    db: Session,
    *,
    room: models.ScoutingRoom,
    match_key: str,
    team_key: str,
    commit: bool = True,
    _allow_bootstrap_retry: bool = True,
) -> models.ScoutingRoomAssignment | None:
    normalized_match_key = _normalize_match_key(match_key)
    normalized_team_key = _normalize_team_key(team_key)
    if not normalized_match_key or not normalized_team_key:
        raise HTTPException(status_code=400, detail="match_key and team_key are required for room assignments.")
    try:
        row = (
            db.query(models.ScoutingRoomAssignment)
            .filter(
                models.ScoutingRoomAssignment.room_key == room.room_key,
                models.ScoutingRoomAssignment.match_key == normalized_match_key,
                models.ScoutingRoomAssignment.team_key == normalized_team_key,
            )
            .first()
        )
        if row is None:
            return None
        db.delete(row)
        _touch_room_activity(db, room, commit=False)
        db.add(room)
        if commit:
            db.commit()
        else:
            db.flush()
        return row
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if (
            _allow_bootstrap_retry
            and _is_missing_table_error(exc, "scouting_room_assignments")
            and _bootstrap_missing_room_table(
                db,
                "scouting_room_assignments",
                trigger_exc=exc,
            )
        ):
            return _clear_room_assignment(
                db,
                room=room,
                match_key=match_key,
                team_key=team_key,
                commit=commit,
                _allow_bootstrap_retry=False,
            )
        if _is_table_unavailable_error(exc, "scouting_room_assignments"):
            if commit:
                db.rollback()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Scouting room assignments are temporarily unavailable. "
                    "Apply database migration 20260305_0005_scouting_room_assignments "
                    "and ensure DB privileges for scouting_room_assignments."
                ),
            ) from exc
        raise

def _load_room_or_404(db: Session, room_key: str) -> models.ScoutingRoom:
    try:
        room = db.get(models.ScoutingRoom, room_key)
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if _is_table_unavailable_error(exc, "scouting_rooms"):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Scouting rooms are temporarily unavailable. "
                    "Apply database migration 20260219_0002_scouting_rooms "
                    "and ensure DB privileges for scouting_rooms."
                ),
            ) from exc
        raise
    if room is None:
        raise HTTPException(status_code=404, detail=f"Scouting room {room_key} not found")
    return room

def _load_room_entries(db: Session, room_key: str, *, limit: int) -> list[models.ScoutingRoomEntry]:
    try:
        return (
            db.query(models.ScoutingRoomEntry)
            .filter(models.ScoutingRoomEntry.room_key == room_key)
            .order_by(models.ScoutingRoomEntry.created_at.desc(), models.ScoutingRoomEntry.id.desc())
            .limit(max(1, min(int(limit), 500)))
            .all()
        )
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if _is_table_unavailable_error(exc, "scouting_room_entries"):
            return []
        raise

def _upsert_room(
    db: Session,
    *,
    room_key: str,
    event_key: str | None,
    title: str | None,
    created_by: str,
) -> models.ScoutingRoom:
    try:
        normalized_created_by = _normalize_scout_profile(created_by)
        if event_key and db.get(models.Event, event_key) is None:
            event_key = None

        room = db.get(models.ScoutingRoom, room_key)
        now = datetime.now(timezone.utc)
        if room is None:
            room = models.ScoutingRoom(
                room_key=room_key,
                event_key=event_key,
                title=(title or "").strip()[:80] or None,
                created_by=normalized_created_by,
                archived=False,
                created_at=now,
                updated_at=now,
                last_activity_at=now,
            )
            db.add(room)
            db.commit()
            db.refresh(room)
            return room

        changed = False
        if event_key and room.event_key != event_key:
            room.event_key = event_key
            changed = True
        if title:
            normalized_title = title.strip()[:80] or None
            if normalized_title and room.title != normalized_title:
                room.title = normalized_title
                changed = True
        if normalized_created_by and not _normalize_scout_profile(room.created_by):
            room.created_by = normalized_created_by
            changed = True
        if changed:
            room.updated_at = now
            db.add(room)
            db.commit()
            db.refresh(room)
        return room
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if _is_table_unavailable_error(exc, "scouting_rooms"):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Scouting rooms are temporarily unavailable. "
                    "Apply database migration 20260219_0002_scouting_rooms "
                    "and ensure DB privileges for scouting_rooms."
                ),
            ) from exc
        raise

def _sync_room_metadata_for_join(
    db: Session,
    *,
    room: models.ScoutingRoom,
    event_key: str | None,
    title: str | None,
    scout_profile: str,
    commit: bool = True,
) -> models.ScoutingRoom:
    normalized_event_key = _normalize_event_key(event_key)
    if normalized_event_key and db.get(models.Event, normalized_event_key) is None:
        normalized_event_key = None
    normalized_title = (title or "").strip()[:80] or None
    normalized_scout_profile = _normalize_scout_profile(scout_profile)
    now = datetime.now(timezone.utc)
    changed = False

    if normalized_event_key and room.event_key != normalized_event_key:
        room.event_key = normalized_event_key
        changed = True
    if normalized_title and room.title != normalized_title:
        room.title = normalized_title
        changed = True
    if normalized_scout_profile and not _normalize_scout_profile(room.created_by):
        room.created_by = normalized_scout_profile
        changed = True

    if changed:
        room.updated_at = now
        db.add(room)
        if commit:
            db.commit()
            db.refresh(room)
        else:
            db.flush()
    return room

def _resolve_room_role(
    room: models.ScoutingRoom,
    scout_profile: str,
    *,
    secondary_leader_profiles: list[str] | None = None,
) -> str:
    profile = _normalize_scout_profile(scout_profile)
    if not profile:
        return ROOM_ROLE_EDITOR

    created_by = _normalize_scout_profile(room.created_by)
    if created_by and _scout_profiles_match(created_by, profile):
        return ROOM_ROLE_OWNER

    for leader_profile in secondary_leader_profiles or []:
        if _scout_profiles_match(leader_profile, profile):
            return ROOM_ROLE_OWNER
    return ROOM_ROLE_EDITOR

def _resolve_room_role_with_presence(
    room: models.ScoutingRoom,
    scout_profile: str,
    *,
    presence: list[dict[str, Any]] | None = None,
    secondary_leader_profiles: list[str] | None = None,
) -> str:
    resolved = _resolve_room_role(
        room,
        scout_profile,
        secondary_leader_profiles=secondary_leader_profiles,
    )
    if resolved == ROOM_ROLE_OWNER:
        return ROOM_ROLE_OWNER
    normalized_profile = _normalize_scout_profile(scout_profile)
    if not normalized_profile:
        return ROOM_ROLE_EDITOR
    leader_profile, leader_source = _resolve_room_leader(
        room,
        presence or [],
        secondary_leader_profiles=secondary_leader_profiles,
    )
    if leader_source == "presence_fallback" and _scout_profiles_match(leader_profile, normalized_profile):
        return ROOM_ROLE_OWNER
    return ROOM_ROLE_EDITOR

async def _resolve_room_action_actor_role(
    room: models.ScoutingRoom,
    actor_profile: str,
    *,
    secondary_leader_profiles: list[str] | None = None,
    fallback_context: str = "room action",
) -> str:
    # Resolve actor role for write actions without requiring fragile presence calls.
    #
    # In standard rooms, ownership is explicit (creator or secondary leaders) and we avoid
    # realtime presence dependencies. Presence fallback is only used for legacy rooms that
    # still have no persisted owner metadata.
    direct_role = _resolve_room_role(
        room,
        actor_profile,
        secondary_leader_profiles=secondary_leader_profiles,
    )
    if direct_role == ROOM_ROLE_OWNER:
        return ROOM_ROLE_OWNER

    has_persisted_owner = bool(_normalize_scout_profile(room.created_by))
    has_secondary_leaders = bool(secondary_leader_profiles)
    if has_persisted_owner or has_secondary_leaders:
        return direct_role

    presence = await _safe_presence_snapshot(room.room_key, context=fallback_context)
    return _resolve_room_role_with_presence(
        room,
        actor_profile,
        presence=presence,
        secondary_leader_profiles=secondary_leader_profiles,
    )

def _entry_metric(payload: dict[str, Any], *path: str) -> float | None:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return _float_or_none(value)

def _entry_overall_scout_rating(payload: dict[str, Any]) -> float | None:
    explicit = _entry_metric(payload, "overall_scout_rating", "score_0_100")
    if explicit is not None:
        return round(_clamp_0_100(explicit), 4)

    manual = _entry_metric(payload, "manual_rating", "score_0_100")
    driver = _entry_metric(payload, "driver_competency", "score_0_100")
    total_points = _entry_metric(payload, "points", "total")
    endgame_points = _entry_metric(payload, "points", "endgame")
    discipline = _entry_metric(payload, "manual_rating", "breakdown", "discipline_0_1")
    if discipline is not None and discipline <= 1.0:
        discipline = float(discipline) * 100.0

    manual_score = _clamp_0_100(manual if manual is not None else 50.0)
    driver_score = _clamp_0_100(driver if driver is not None else 50.0)
    point_score = _clamp_0_100(((total_points if total_points is not None else 0.0) / 45.0) * 100.0)
    endgame_score = _clamp_0_100(((endgame_points if endgame_points is not None else 0.0) / 30.0) * 100.0)
    discipline_score = _clamp_0_100(discipline if discipline is not None else 50.0)

    score = (
        (0.56 * manual_score)
        + (0.20 * driver_score)
        + (0.14 * point_score)
        + (0.06 * endgame_score)
        + (0.04 * discipline_score)
    )
    return round(_clamp_0_100(score), 4)

def _persist_room_entry(
    db: Session,
    *,
    room: models.ScoutingRoom,
    entry_payload: dict[str, Any],
    scout_profile: str,
    client_entry_id: str | None,
) -> tuple[models.ScoutingRoomEntry, bool]:
    normalized_client_id = str(client_entry_id or "").strip()[:120] or None
    if normalized_client_id:
        existing = (
            db.query(models.ScoutingRoomEntry)
            .filter(
                models.ScoutingRoomEntry.room_key == room.room_key,
                models.ScoutingRoomEntry.client_entry_id == normalized_client_id,
            )
            .first()
        )
        if existing is not None:
            return existing, False

    now = datetime.now(timezone.utc)
    payload = dict(entry_payload)
    if not payload.get("id"):
        payload["id"] = normalized_client_id or f"{int(now.timestamp() * 1000)}-{secrets.token_hex(3)}"
    payload["scout_profile"] = scout_profile

    event_key = _normalize_event_key(payload.get("event_key") or room.event_key)
    match_key = _normalize_match_key(payload.get("match_key"))
    team_key = _normalize_team_key(payload.get("team_key"))

    if event_key and db.get(models.Event, event_key) is None:
        event_key = None
    if match_key and db.get(models.Match, match_key) is None:
        match_key = None
    if team_key and db.get(models.Team, team_key) is None:
        team_key = None

    row = models.ScoutingRoomEntry(
        room_key=room.room_key,
        event_key=event_key,
        match_key=match_key,
        team_key=team_key,
        scout_profile=scout_profile,
        client_entry_id=normalized_client_id,
        payload=payload,
        total_points=_entry_metric(payload, "points", "total"),
        driver_score_0_100=_entry_metric(payload, "driver_competency", "score_0_100"),
        manual_rating_0_100=_entry_metric(payload, "manual_rating", "score_0_100"),
        scouting_api_rating_0_100=_entry_metric(payload, "scouting_api_rating", "score_0_100"),
        created_at=now,
        updated_at=now,
    )

    room.last_activity_at = now
    room.updated_at = now
    if event_key and not room.event_key:
        room.event_key = event_key

    db.add(row)
    db.add(room)
    db.commit()
    db.refresh(row)
    db.refresh(room)
    return row, True

def _generate_room_key(db: Session) -> str:
    try:
        for _ in range(8):
            candidate = f"room-{secrets.token_hex(3)}"
            if db.get(models.ScoutingRoom, candidate) is None:
                return candidate
        return f"room-{secrets.token_hex(4)}"
    except (ProgrammingError, OperationalError, DatabaseError) as exc:
        if _is_table_unavailable_error(exc, "scouting_rooms"):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Scouting rooms are temporarily unavailable. "
                    "Apply database migration 20260219_0002_scouting_rooms "
                    "and ensure DB privileges for scouting_rooms."
                ),
            ) from exc
        raise

def _websocket_write_access_allowed(websocket: WebSocket) -> tuple[bool, str | None]:
    if settings.public_readonly_mode:
        return False, "Scouting rooms are disabled in public mode."
    return True, None

def _websocket_room_access_token(websocket: WebSocket) -> str:
    query_token = str(websocket.query_params.get("room_access") or "").strip()
    if query_token:
        return query_token
    return str(websocket.headers.get(ROOM_ACCESS_HEADER) or "").strip()

class RoomCreateRequest(BaseModel):
    room_key: str | None = Field(default=None, max_length=48)
    event_key: str | None = Field(default=None, max_length=48)
    title: str | None = Field(default=None, max_length=80)
    scout_profile: str | None = Field(default=None, max_length=40)
    client_id: str | None = Field(default=None, max_length=80)
    create_if_missing: bool = Field(default=False)

class RoomEntryCreateRequest(BaseModel):
    entry: dict[str, Any]
    scout_profile: str | None = Field(default=None, max_length=40)
    client_entry_id: str | None = Field(default=None, max_length=120)

class RoomAssignmentUpsertRequest(BaseModel):
    match_key: str = Field(..., min_length=1, max_length=80)
    team_key: str = Field(..., min_length=1, max_length=24)
    assigned_scout_profile: str | None = Field(default=None, max_length=40)
    event_key: str | None = Field(default=None, max_length=48)

class RoomAssignmentRowRequest(BaseModel):
    match_key: str = Field(..., min_length=1, max_length=80)
    team_key: str = Field(..., min_length=1, max_length=24)
    assigned_scout_profile: str = Field(..., min_length=1, max_length=40)

class RoomAssignmentReplaceRequest(BaseModel):
    event_key: str | None = Field(default=None, max_length=48)
    assignments: list[RoomAssignmentRowRequest] = Field(default_factory=list)

class RoomKickRequest(BaseModel):
    scout_profile: str = Field(..., min_length=1, max_length=40)

class RoomLeaderUpdateRequest(BaseModel):
    scout_profile: str = Field(..., min_length=1, max_length=40)

@router.post("")
async def create_or_join_room(
    request: RoomCreateRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    require_write_access("Scouting room create/join")

    room_key = ""
    event_key = _normalize_event_key(request.event_key)
    scout_profile = _require_scout_profile(
        request.scout_profile,
        context="joining a scouting room",
    )
    requested_room_key = _normalize_room_key(request.room_key)
    normalized_client_id = str(request.client_id or "").strip()[:80] or None
    create_if_missing = bool(request.create_if_missing)

    try:
        if not requested_room_key:
            room_key = _generate_room_key(db)
            room = _upsert_room(
                db,
                room_key=room_key,
                event_key=event_key,
                title=request.title,
                created_by=scout_profile,
            )
        elif create_if_missing:
            room_key = requested_room_key
            room = _upsert_room(
                db,
                room_key=room_key,
                event_key=event_key,
                title=request.title,
                created_by=scout_profile,
            )
        else:
            room_key = requested_room_key
            room = _load_room_or_404(db, room_key)
            room = _sync_room_metadata_for_join(
                db,
                room=room,
                event_key=event_key,
                title=request.title,
                scout_profile=scout_profile,
                commit=True,
            )
        _touch_room_activity(db, room, commit=True)
        room = _load_room_or_404(db, room_key)
        presence = await _safe_presence_snapshot(room_key, context="create or join room")
        secondary_leaders = _load_room_secondary_leader_profiles(db, room_key)
    except HTTPException:
        raise
    except (ProgrammingError, OperationalError, DatabaseError, IntegrityError) as exc:
        raise HTTPException(
            status_code=503,
            detail=_database_unavailable_detail(
                (
                    "Scouting room creation is temporarily unavailable due to database schema/permission issues. "
                    "Apply migrations through 20260305_0006 and ensure write privileges on scouting room tables."
                ),
                exc,
            ),
        ) from exc
    existing_room_access_payload = parse_room_access_token(
        str(http_request.headers.get(ROOM_ACCESS_HEADER) or "").strip()
    )
    profile_claim_conflicts = _room_profile_claim_conflicts(
        room_key=room.room_key,
        scout_profile=scout_profile,
        presence=presence,
        existing_room_access_payload=existing_room_access_payload,
    )
    if profile_claim_conflicts and normalized_client_id:
        if await _safe_profile_has_client_presence(
            room.room_key,
            scout_profile=scout_profile,
            client_id=normalized_client_id,
            context="create or join room",
        ):
            profile_claim_conflicts = False
    if profile_claim_conflicts:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Scout profile '{scout_profile}' is already active in room {room.room_key}. "
                "Use a unique scout name."
            ),
        )
    presence = await _safe_touch_http_presence(
        room.room_key,
        scout_profile=scout_profile,
        client_id=normalized_client_id,
        context="create or join room",
        fallback_presence=presence,
    )
    room_role = _resolve_room_role_with_presence(
        room,
        scout_profile,
        presence=presence,
        secondary_leader_profiles=secondary_leaders,
    )
    room_access = issue_room_access_token(
        room_key=room.room_key,
        scout_profile=scout_profile,
        role=room_role,
    )
    rows: list[models.ScoutingRoomEntry] = []
    assignments: list[models.ScoutingRoomAssignment] = []
    try:
        rows = _load_room_entries(db, room_key, limit=200)
    except (ProgrammingError, OperationalError, DatabaseError, IntegrityError):
        rows = []
    try:
        assignments = _load_room_assignments(db, room_key, event_key=room.event_key, limit=1500)
    except (ProgrammingError, OperationalError, DatabaseError, IntegrityError):
        assignments = []

    return {
        "ok": True,
        "room": _serialize_room(
            room,
            presence=presence,
            room_role=room_role,
            secondary_leader_scout_profiles=secondary_leaders,
        ),
        "entries": [_serialize_entry(row) for row in rows],
        "assignments": [_serialize_assignment(row) for row in assignments],
        "my_assignments": [
            _serialize_assignment(row) for row in _assignments_for_scout(assignments, scout_profile)
        ],
        "access": {
            "room_role": room_role,
            "room_access_token": room_access["token"],
            "expires_at": room_access["expires_at"],
            "expires_at_unix": room_access["expires_at_unix"],
            "ttl_sec": room_access["ttl_sec"],
            "header": ROOM_ACCESS_HEADER,
        },
    }

@router.get("/{room_key}")
async def get_room_state(
    room_key: str,
    http_request: Request,
    history_limit: int = Query(default=200, ge=1, le=500),
    scout_profile: str | None = Query(default=None, max_length=40),
    client_id: str | None = Query(default=None, max_length=80),
    presence_heartbeat: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    normalized = _normalize_room_key(room_key)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid room key")

    room = _load_room_or_404(db, normalized)
    presence = await _safe_presence_snapshot(normalized, context="get room state")
    try:
        secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
    except (ProgrammingError, OperationalError, DatabaseError, IntegrityError) as exc:
        logger.warning(
            "Failed to load scouting room secondary leaders for %s: %s",
            normalized,
            exc,
        )
        secondary_leaders = []
    room_role = None
    access_payload = None
    normalized_profile = _normalize_scout_profile(scout_profile)
    normalized_client_id = str(client_id or "").strip()[:80] or None
    existing_room_access_payload = parse_room_access_token(
        str(http_request.headers.get(ROOM_ACCESS_HEADER) or "").strip()
    )
    if normalized_profile:
        try:
            if (
                presence_heartbeat
                and room_access_allows(
                    existing_room_access_payload,
                    room_key=room.room_key,
                    scout_profile=normalized_profile,
                    require_write=True,
                )
            ):
                presence = await _safe_touch_http_presence(
                    room.room_key,
                    scout_profile=normalized_profile,
                    client_id=normalized_client_id,
                    context="get room state",
                    fallback_presence=presence,
                )
            profile_claim_conflicts = _room_profile_claim_conflicts(
                room_key=room.room_key,
                scout_profile=normalized_profile,
                presence=presence,
                existing_room_access_payload=existing_room_access_payload,
            )
            if profile_claim_conflicts and normalized_client_id:
                if await _safe_profile_has_client_presence(
                    room.room_key,
                    scout_profile=normalized_profile,
                    client_id=normalized_client_id,
                    context="get room state",
                ):
                    profile_claim_conflicts = False
            if profile_claim_conflicts:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Scout profile '{normalized_profile}' is already active in room {room.room_key}. "
                        "Use a unique scout name."
                    ),
                )
            try:
                _touch_room_activity(db, room, commit=True)
                room = _load_room_or_404(db, normalized)
            except (ProgrammingError, OperationalError, DatabaseError, IntegrityError):
                # Issuing a room access token should not fail solely because activity-touch writes are unavailable.
                room = _load_room_or_404(db, normalized)
            try:
                secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
            except (ProgrammingError, OperationalError, DatabaseError, IntegrityError):
                secondary_leaders = []
            room_role = _resolve_room_role_with_presence(
                room,
                normalized_profile,
                presence=presence,
                secondary_leader_profiles=secondary_leaders,
            )
            room_access = issue_room_access_token(
                room_key=room.room_key,
                scout_profile=normalized_profile,
                role=room_role,
            )
            access_payload = {
                "room_role": room_role,
                "room_access_token": room_access["token"],
                "expires_at": room_access["expires_at"],
                "expires_at_unix": room_access["expires_at_unix"],
                "ttl_sec": room_access["ttl_sec"],
                "header": ROOM_ACCESS_HEADER,
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "Failed to issue scouting room access payload for room %s scout %s: %s",
                normalized,
                normalized_profile,
                exc,
            )
            # Degrade gracefully: caller can still read room state and continue with
            # stored room access token if available.
            try:
                room_role = _resolve_room_role_with_presence(
                    room,
                    normalized_profile,
                    presence=presence,
                    secondary_leader_profiles=secondary_leaders,
                )
            except Exception:
                room_role = None
            access_payload = None

    # Load history/assignments after optional activity touch so ORM row state remains valid.
    try:
        rows = _load_room_entries(db, normalized, limit=history_limit)
    except (ProgrammingError, OperationalError, DatabaseError, IntegrityError) as exc:
        logger.warning(
            "Failed to load scouting room entries for %s: %s",
            normalized,
            exc,
        )
        rows = []
    try:
        assignments = _load_room_assignments(db, normalized, event_key=room.event_key, limit=1500)
    except (ProgrammingError, OperationalError, DatabaseError, IntegrityError) as exc:
        logger.warning(
            "Failed to load scouting room assignments for %s: %s",
            normalized,
            exc,
        )
        assignments = []

    payload: dict[str, Any] = {
        "ok": True,
        "room": _serialize_room(
            room,
            presence=presence,
            room_role=room_role,
            secondary_leader_scout_profiles=secondary_leaders,
        ),
        "entries": [_serialize_entry(row) for row in rows],
        "assignments": [_serialize_assignment(row) for row in assignments],
    }
    if normalized_profile:
        payload["my_assignments"] = [
            _serialize_assignment(row)
            for row in _assignments_for_scout(assignments, normalized_profile)
        ]
    if access_payload is not None:
        payload["access"] = access_payload
    return payload

@router.get("/{room_key}/entries")
def get_room_entries(
    room_key: str,
    history_limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    normalized = _normalize_room_key(room_key)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid room key")

    _load_room_or_404(db, normalized)
    rows = _load_room_entries(db, normalized, limit=history_limit)
    return {
        "ok": True,
        "room_key": normalized,
        "count": len(rows),
        "entries": [_serialize_entry(row) for row in rows],
    }

@router.get("/{room_key}/assignments")
async def get_room_assignments(
    room_key: str,
    http_request: Request,
    event_key: str | None = Query(default=None, max_length=48),
    for_scout_profile: str | None = Query(default=None, max_length=40),
    client_id: str | None = Query(default=None, max_length=80),
    presence_heartbeat: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    normalized = _normalize_room_key(room_key)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid room key")

    room = _load_room_or_404(db, normalized)
    presence = await _safe_presence_snapshot(normalized, context="get room assignments")
    normalized_profile = _normalize_scout_profile(for_scout_profile)
    normalized_client_id = str(client_id or "").strip()[:80] or None
    existing_room_access_payload = parse_room_access_token(
        str(http_request.headers.get(ROOM_ACCESS_HEADER) or "").strip()
    )
    if (
        normalized_profile
        and presence_heartbeat
        and room_access_allows(
            existing_room_access_payload,
            room_key=room.room_key,
            scout_profile=normalized_profile,
            require_write=True,
        )
    ):
        presence = await _safe_touch_http_presence(
            room.room_key,
            scout_profile=normalized_profile,
            client_id=normalized_client_id,
            context="get room assignments",
            fallback_presence=presence,
        )

    rows = _load_room_assignments(db, normalized, event_key=event_key or room.event_key, limit=2000)
    secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
    my_rows = _assignments_for_scout(rows, for_scout_profile)
    leader_profile, leader_source = _resolve_room_leader(
        room,
        presence,
        secondary_leader_profiles=secondary_leaders,
    )
    room_role = None
    if normalized_profile:
        room_role = _resolve_room_role_with_presence(
            room,
            normalized_profile,
            presence=presence,
            secondary_leader_profiles=secondary_leaders,
        )
    return {
        "ok": True,
        "room_key": normalized,
        "event_key": _normalize_event_key(event_key) or room.event_key,
        "count": len(rows),
        "leader_scout_profile": leader_profile,
        "leader_source": leader_source,
        "room_role": room_role,
        "secondary_leader_scout_profiles": secondary_leaders,
        "presence": presence,
        "assignments": [_serialize_assignment(row) for row in rows],
        "my_assignments": [_serialize_assignment(row) for row in my_rows],
    }

@router.post("/{room_key}/assignments")
async def upsert_room_assignment(
    room_key: str,
    request: RoomAssignmentUpsertRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    require_write_access("Scouting room assignment update")

    normalized = _normalize_room_key(room_key)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid room key")
    room = _load_room_or_404(db, normalized)

    room_access_token = str(http_request.headers.get(ROOM_ACCESS_HEADER) or "").strip()
    room_access_payload = parse_room_access_token(room_access_token)
    actor_profile = _normalize_scout_profile((room_access_payload or {}).get("scout_profile"))
    if not room_access_allows(
        room_access_payload,
        room_key=normalized,
        scout_profile=actor_profile,
        require_write=True,
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Assignment update requires a valid {ROOM_ACCESS_HEADER} token for this scout profile.",
        )
    if not actor_profile:
        raise HTTPException(status_code=403, detail="Assignment update requires a bound scout profile.")
    secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
    actor_role = await _resolve_room_action_actor_role(
        room,
        actor_profile,
        secondary_leader_profiles=secondary_leaders,
        fallback_context="assignment update role check",
    )
    if actor_role != ROOM_ROLE_OWNER:
        raise HTTPException(
            status_code=403,
            detail="Only room leaders can update scouting room assignments.",
        )

    assigned_scout_profile = _normalize_scout_profile(request.assigned_scout_profile)
    if assigned_scout_profile:
        row = _upsert_room_assignment(
            db,
            room=room,
            match_key=request.match_key,
            team_key=request.team_key,
            assigned_scout_profile=assigned_scout_profile,
            assigned_by_scout_profile=actor_profile,
            event_key=request.event_key or room.event_key,
        )
        serialized = _serialize_assignment(row)
        await _broadcast_room_message(
            normalized,
            {
                "type": "assignment_updated",
                "room_key": normalized,
                "assignment": serialized,
                "server_time": datetime.now(timezone.utc).isoformat(),
                "assigned_by": actor_profile,
            },
        )
        return {
            "ok": True,
            "room_key": normalized,
            "deleted": False,
            "assignment": serialized,
        }

    deleted = _clear_room_assignment(
        db,
        room=room,
        match_key=request.match_key,
        team_key=request.team_key,
    )
    deleted_payload = {
        "match_key": _normalize_match_key(request.match_key),
        "team_key": _normalize_team_key(request.team_key),
    }
    await _broadcast_room_message(
        normalized,
        {
            "type": "assignment_deleted",
            "room_key": normalized,
            "assignment": deleted_payload,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "assigned_by": actor_profile,
        },
    )
    return {
        "ok": True,
        "room_key": normalized,
        "deleted": bool(deleted is not None),
        "assignment": deleted_payload,
    }

@router.post("/{room_key}/assignments/replace")
async def replace_room_assignments(
    room_key: str,
    request: RoomAssignmentReplaceRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    require_write_access("Scouting room assignment replace")

    normalized = _normalize_room_key(room_key)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid room key")
    room = _load_room_or_404(db, normalized)

    room_access_token = str(http_request.headers.get(ROOM_ACCESS_HEADER) or "").strip()
    room_access_payload = parse_room_access_token(room_access_token)
    actor_profile = _normalize_scout_profile((room_access_payload or {}).get("scout_profile"))
    if not room_access_allows(
        room_access_payload,
        room_key=normalized,
        scout_profile=actor_profile,
        require_write=True,
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Assignment replace requires a valid {ROOM_ACCESS_HEADER} token for this scout profile.",
        )
    if not actor_profile:
        raise HTTPException(status_code=403, detail="Assignment replace requires a bound scout profile.")
    secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
    actor_role = await _resolve_room_action_actor_role(
        room,
        actor_profile,
        secondary_leader_profiles=secondary_leaders,
        fallback_context="assignment replace role check",
    )
    if actor_role != ROOM_ROLE_OWNER:
        raise HTTPException(
            status_code=403,
            detail="Only room leaders can replace scouting room assignments.",
        )

    normalized_event = _normalize_event_key(request.event_key) or room.event_key

    def _replace_rows(*, allow_bootstrap_retry: bool) -> None:
        try:
            if normalized_event:
                (
                    db.query(models.ScoutingRoomAssignment)
                    .filter(models.ScoutingRoomAssignment.room_key == normalized)
                    .filter(models.ScoutingRoomAssignment.event_key == normalized_event)
                    .delete(synchronize_session=False)
                )

            for row in request.assignments:
                _upsert_room_assignment(
                    db,
                    room=room,
                    match_key=row.match_key,
                    team_key=row.team_key,
                    assigned_scout_profile=row.assigned_scout_profile,
                    assigned_by_scout_profile=actor_profile,
                    event_key=normalized_event,
                    commit=False,
                    _allow_bootstrap_retry=allow_bootstrap_retry,
                )
            db.commit()
            db.refresh(room)
        except (ProgrammingError, OperationalError, DatabaseError) as exc:
            if (
                allow_bootstrap_retry
                and _is_missing_table_error(exc, "scouting_room_assignments")
                and _bootstrap_missing_room_table(
                    db,
                    "scouting_room_assignments",
                    trigger_exc=exc,
                )
            ):
                _replace_rows(allow_bootstrap_retry=False)
                return
            if _is_table_unavailable_error(exc, "scouting_room_assignments"):
                db.rollback()
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Scouting room assignments are temporarily unavailable. "
                        "Apply database migration 20260305_0005_scouting_room_assignments "
                        "and ensure DB privileges for scouting_room_assignments."
                    ),
                ) from exc
            raise

    _replace_rows(allow_bootstrap_retry=True)

    rows = _load_room_assignments(db, normalized, event_key=normalized_event, limit=2000)
    serialized_rows = [_serialize_assignment(row) for row in rows]
    await _broadcast_room_message(
        normalized,
        {
            "type": "assignments_replaced",
            "room_key": normalized,
            "event_key": normalized_event,
            "assignments": serialized_rows,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "assigned_by": actor_profile,
        },
    )
    return {
        "ok": True,
        "room_key": normalized,
        "event_key": normalized_event,
        "count": len(serialized_rows),
        "assignments": serialized_rows,
    }

@router.post("/{room_key}/leaders")
async def add_room_secondary_leader(
    room_key: str,
    request: RoomLeaderUpdateRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    require_write_access("Scouting room leader promote")

    normalized = _normalize_room_key(room_key)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid room key")
    room = _load_room_or_404(db, normalized)

    room_access_token = str(http_request.headers.get(ROOM_ACCESS_HEADER) or "").strip()
    room_access_payload = parse_room_access_token(room_access_token)
    actor_profile = _normalize_scout_profile((room_access_payload or {}).get("scout_profile"))
    if not room_access_allows(
        room_access_payload,
        room_key=normalized,
        scout_profile=actor_profile,
        require_write=True,
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Leader promote requires a valid {ROOM_ACCESS_HEADER} token for this scout profile.",
        )
    if not actor_profile:
        raise HTTPException(status_code=403, detail="Leader promote requires a bound scout profile.")

    secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
    actor_role = await _resolve_room_action_actor_role(
        room,
        actor_profile,
        secondary_leader_profiles=secondary_leaders,
        fallback_context="leader promote role check",
    )
    if actor_role != ROOM_ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Only room leaders can promote secondary leaders.")

    target_profile = _normalize_scout_profile(request.scout_profile)
    if not target_profile:
        raise HTTPException(status_code=400, detail="Target scout_profile is required.")
    if _scout_profiles_match(target_profile, room.created_by):
        raise HTTPException(status_code=400, detail="Room creator is already a room leader.")

    _row, created = _upsert_room_secondary_leader(
        db,
        room=room,
        scout_profile=target_profile,
        added_by_scout_profile=actor_profile,
    )
    secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
    presence = await _safe_presence_snapshot(normalized, context="leader promote broadcast")

    await _broadcast_room_message(
        normalized,
        {
            "type": "secondary_leaders_updated",
            "room_key": normalized,
            "secondary_leader_scout_profiles": secondary_leaders,
            "action": "promoted",
            "target_scout_profile": target_profile,
            "updated_by": actor_profile,
            "server_time": datetime.now(timezone.utc).isoformat(),
        },
    )
    await _broadcast_presence_message(normalized, presence)
    return {
        "ok": True,
        "room_key": normalized,
        "created": created,
        "target_scout_profile": target_profile,
        "secondary_leader_scout_profiles": secondary_leaders,
    }

@router.post("/{room_key}/leaders/remove")
async def remove_room_secondary_leader(
    room_key: str,
    request: RoomLeaderUpdateRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    require_write_access("Scouting room leader demote")

    normalized = _normalize_room_key(room_key)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid room key")
    room = _load_room_or_404(db, normalized)

    room_access_token = str(http_request.headers.get(ROOM_ACCESS_HEADER) or "").strip()
    room_access_payload = parse_room_access_token(room_access_token)
    actor_profile = _normalize_scout_profile((room_access_payload or {}).get("scout_profile"))
    if not room_access_allows(
        room_access_payload,
        room_key=normalized,
        scout_profile=actor_profile,
        require_write=True,
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Leader demote requires a valid {ROOM_ACCESS_HEADER} token for this scout profile.",
        )
    if not actor_profile:
        raise HTTPException(status_code=403, detail="Leader demote requires a bound scout profile.")

    secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
    actor_role = await _resolve_room_action_actor_role(
        room,
        actor_profile,
        secondary_leader_profiles=secondary_leaders,
        fallback_context="leader demote role check",
    )
    if actor_role != ROOM_ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Only room leaders can remove secondary leaders.")

    target_profile = _normalize_scout_profile(request.scout_profile)
    if not target_profile:
        raise HTTPException(status_code=400, detail="Target scout_profile is required.")
    if _scout_profiles_match(target_profile, room.created_by):
        raise HTTPException(status_code=400, detail="Cannot remove the room creator from leadership.")

    removed = _remove_room_secondary_leader(
        db,
        room=room,
        scout_profile=target_profile,
    )
    secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
    presence = await _safe_presence_snapshot(normalized, context="leader demote broadcast")

    if removed:
        await _broadcast_room_message(
            normalized,
            {
                "type": "secondary_leaders_updated",
                "room_key": normalized,
                "secondary_leader_scout_profiles": secondary_leaders,
                "action": "demoted",
                "target_scout_profile": target_profile,
                "updated_by": actor_profile,
                "server_time": datetime.now(timezone.utc).isoformat(),
            },
        )
    await _broadcast_presence_message(normalized, presence)
    return {
        "ok": True,
        "room_key": normalized,
        "removed": bool(removed),
        "target_scout_profile": target_profile,
        "secondary_leader_scout_profiles": secondary_leaders,
    }

@router.post("/{room_key}/kick")
async def kick_room_member(
    room_key: str,
    request: RoomKickRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    require_write_access("Scouting room kick member")

    normalized = _normalize_room_key(room_key)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid room key")
    room = _load_room_or_404(db, normalized)

    room_access_token = str(http_request.headers.get(ROOM_ACCESS_HEADER) or "").strip()
    room_access_payload = parse_room_access_token(room_access_token)
    actor_profile = _normalize_scout_profile((room_access_payload or {}).get("scout_profile"))
    if not room_access_allows(
        room_access_payload,
        room_key=normalized,
        scout_profile=actor_profile,
        require_write=True,
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Kick member requires a valid {ROOM_ACCESS_HEADER} token for this scout profile.",
        )
    if not actor_profile:
        raise HTTPException(status_code=403, detail="Kick member requires a bound scout profile.")
    secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
    actor_role = await _resolve_room_action_actor_role(
        room,
        actor_profile,
        secondary_leader_profiles=secondary_leaders,
        fallback_context="kick member role check",
    )
    if actor_role != ROOM_ROLE_OWNER:
        raise HTTPException(
            status_code=403,
            detail="Only room leaders can kick members.",
        )

    presence_before = await _safe_presence_snapshot(normalized, context="kick member target lookup")

    target_profile = _normalize_scout_profile(request.scout_profile)
    if not target_profile:
        raise HTTPException(status_code=400, detail="Target scout_profile is required.")
    if _scout_profiles_match(actor_profile, target_profile):
        raise HTTPException(status_code=400, detail="Room leader cannot kick themselves.")

    leader_profile, leader_source = _resolve_room_leader(
        room,
        presence_before,
        secondary_leader_profiles=secondary_leaders,
    )

    target_member = next(
        (
            member
            for member in presence_before
            if _scout_profiles_match(member.get("scout_profile"), target_profile)
        ),
        None,
    )
    resolved_target_profile = _normalize_scout_profile(
        (target_member or {}).get("scout_profile")
    ) or target_profile
    target_was_present = bool(target_member is not None)
    kick_reason = f"Removed by room leader {actor_profile}."

    removed_connections, presence_after = await scouting_room_hub.disconnect_scout_profile(
        normalized,
        resolved_target_profile,
        reason=kick_reason,
    )
    if target_was_present:
        await scouting_room_bus.publish(
            normalized,
            {
                "type": _ROOM_BUS_CONTROL_DISCONNECT_PROFILE,
                "room_key": normalized,
                "scout_profile": resolved_target_profile,
                "kicked_by": actor_profile,
                "reason": kick_reason,
                "close_code": 4403,
            },
        )

    if removed_connections > 0 or target_was_present:
        _touch_room_activity(db, room, commit=True)
        await _broadcast_room_message(
            normalized,
            {
                "type": "member_kicked",
                "room_key": normalized,
                "target_scout_profile": resolved_target_profile,
                "removed_connections": int(removed_connections),
                "kicked_by": actor_profile,
                "server_time": datetime.now(timezone.utc).isoformat(),
            },
        )
    await _broadcast_presence_message(normalized, presence_after)

    return {
        "ok": True,
        "room_key": normalized,
        "target_scout_profile": resolved_target_profile,
        "kicked": bool(removed_connections > 0 or target_was_present),
        "removed_connections": int(removed_connections),
        "leader_scout_profile": leader_profile,
        "leader_source": leader_source,
    }

@router.post("/{room_key}/entries")
async def save_room_entry(
    room_key: str,
    request: RoomEntryCreateRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    require_write_access("Scouting room entry save")

    normalized = _normalize_room_key(room_key)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid room key")

    room = _load_room_or_404(db, normalized)
    scout_profile = _require_scout_profile(
        request.scout_profile,
        context="saving a scouting room entry",
    )
    room_access_token = str(http_request.headers.get(ROOM_ACCESS_HEADER) or "").strip()
    room_access_payload = parse_room_access_token(room_access_token)
    if not room_access_allows(
        room_access_payload,
        room_key=normalized,
        scout_profile=scout_profile,
        require_write=True,
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Room entry save requires a valid {ROOM_ACCESS_HEADER} token for this scout profile.",
        )
    row, created = _persist_room_entry(
        db,
        room=room,
        entry_payload=request.entry,
        scout_profile=scout_profile,
        client_entry_id=request.client_entry_id,
    )
    serialized = _serialize_entry(row)

    await _broadcast_room_message(
        normalized,
        {
            "type": "entry_saved",
            "room_key": normalized,
            "created": created,
            "entry": serialized["entry"],
            "server_entry": serialized,
            "server_time": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {
        "ok": True,
        "room_key": normalized,
        "created": created,
        "entry": serialized,
    }

@router.websocket("/{room_key}/ws")
async def scouting_room_ws(websocket: WebSocket, room_key: str):
    normalized = _normalize_room_key(room_key)
    if not normalized:
        await websocket.close(code=4400, reason="Invalid room key")
        return

    allowed, deny_reason = _websocket_write_access_allowed(websocket)
    if not allowed:
        await websocket.close(code=4403, reason=deny_reason or "Write access denied")
        return

    scout_profile = _normalize_scout_profile(websocket.query_params.get("scout_profile"))
    if not scout_profile:
        await websocket.close(
            code=4400,
            reason="scout_profile is required for room websocket. Enter your name and reconnect.",
        )
        return
    room_access_token = _websocket_room_access_token(websocket)
    room_access_payload = parse_room_access_token(room_access_token)
    if not room_access_allows(
        room_access_payload,
        room_key=normalized,
        scout_profile=scout_profile,
        require_write=True,
    ):
        await websocket.close(
            code=4403,
            reason=f"Room websocket requires a valid {ROOM_ACCESS_HEADER} token for this scout profile.",
        )
        return
    event_key = _normalize_event_key(websocket.query_params.get("event_key"))
    title = websocket.query_params.get("title")
    client_id = str(websocket.query_params.get("client_id") or "").strip()[:80] or None
    history_limit = int(websocket.query_params.get("history_limit") or 200)
    history_limit = max(1, min(history_limit, 500))

    db = SessionLocal()
    try:
        room = _load_room_or_404(db, normalized)
        room = _sync_room_metadata_for_join(
            db,
            room=room,
            event_key=event_key,
            title=title,
            scout_profile=scout_profile,
            commit=True,
        )
        _touch_room_activity(db, room, commit=True)
        room = _load_room_or_404(db, normalized)
        presence_before = await _safe_presence_snapshot(normalized, context="websocket connect")
        if _presence_connections_for_profile(presence_before, scout_profile) > 0:
            removed_connections, presence_after_replace = await scouting_room_hub.disconnect_scout_profile(
                normalized,
                scout_profile,
                reason=f"Session replaced for scout profile {scout_profile}.",
            )
            if removed_connections <= 0:
                await websocket.close(
                    code=4409,
                    reason=(
                        f"Scout profile '{scout_profile}' is already active in room {normalized}. "
                        "Use a unique scout name."
                    ),
                )
                return
            await _broadcast_presence_message(normalized, presence_after_replace)
        entries = [_serialize_entry(row) for row in _load_room_entries(db, normalized, limit=history_limit)]
        assignments = [
            _serialize_assignment(row)
            for row in _load_room_assignments(db, normalized, event_key=room.event_key, limit=1500)
        ]
        secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
        presence = await scouting_room_hub.connect(
            normalized,
            websocket,
            scout_profile=scout_profile,
            client_id=client_id,
        )
        room_role = _resolve_room_role_with_presence(
            room,
            scout_profile,
            presence=presence,
            secondary_leader_profiles=secondary_leaders,
        )

        await websocket.send_json(
            {
                "type": "snapshot",
                "room": _serialize_room(
                    room,
                    presence=presence,
                    room_role=room_role,
                    secondary_leader_scout_profiles=secondary_leaders,
                ),
                "entries": entries,
                "assignments": assignments,
                "server_time": datetime.now(timezone.utc).isoformat(),
            }
        )
        await _broadcast_presence_message(normalized, presence)

        while True:
            payload = await websocket.receive_json()
            if not isinstance(payload, dict):
                await websocket.send_json({"type": "error", "detail": "Invalid websocket payload"})
                continue

            message_type = str(payload.get("type") or "").strip().lower()
            if message_type in {"ping", "heartbeat"}:
                await scouting_room_hub.touch(websocket)
                await websocket.send_json(
                    {
                        "type": "pong",
                        "server_time": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue

            if message_type == "update_profile":
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": "Profile updates require joining again to mint a new room access token.",
                    }
                )
                continue

            if message_type == "request_snapshot":
                snapshot_rows = _load_room_entries(db, normalized, limit=history_limit)
                snapshot_assignments = [
                    _serialize_assignment(row)
                    for row in _load_room_assignments(db, normalized, event_key=room.event_key, limit=1500)
                ]
                snapshot_presence = await _safe_presence_snapshot(
                    normalized,
                    context="websocket snapshot request",
                )
                room = _load_room_or_404(db, normalized)
                secondary_leaders = _load_room_secondary_leader_profiles(db, normalized)
                room_role = _resolve_room_role_with_presence(
                    room,
                    scout_profile,
                    presence=snapshot_presence,
                    secondary_leader_profiles=secondary_leaders,
                )
                await websocket.send_json(
                    {
                        "type": "snapshot",
                        "room": _serialize_room(
                            room,
                            presence=snapshot_presence,
                            room_role=room_role,
                            secondary_leader_scout_profiles=secondary_leaders,
                        ),
                        "entries": [_serialize_entry(row) for row in snapshot_rows],
                        "assignments": snapshot_assignments,
                        "server_time": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue

            if message_type == "save_entry":
                entry = payload.get("entry")
                if not isinstance(entry, dict):
                    await websocket.send_json({"type": "error", "detail": "save_entry requires an entry object"})
                    continue
                override_profile = payload.get("scout_profile")
                if override_profile is not None:
                    normalized_override = _normalize_scout_profile(
                        override_profile if isinstance(override_profile, str) else ""
                    )
                    if normalized_override and normalized_override != scout_profile:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "detail": "Room access token is bound to scout_profile; reconnect to change profile.",
                            }
                        )
                        continue
                row, created = _persist_room_entry(
                    db,
                    room=_load_room_or_404(db, normalized),
                    entry_payload=entry,
                    scout_profile=scout_profile,
                    client_entry_id=(str(payload.get("client_entry_id") or "").strip() or None),
                )
                serialized = _serialize_entry(row)
                message = {
                    "type": "entry_saved",
                    "room_key": normalized,
                    "created": created,
                    "entry": serialized["entry"],
                    "server_entry": serialized,
                    "server_time": datetime.now(timezone.utc).isoformat(),
                }
                await _broadcast_room_message(normalized, message)
                await websocket.send_json(
                    {
                        "type": "entry_ack",
                        "room_key": normalized,
                        "client_entry_id": payload.get("client_entry_id"),
                        "server_id": serialized.get("server_id"),
                        "created": created,
                    }
                )
                continue

            await websocket.send_json({"type": "error", "detail": f"Unsupported message type: {message_type}"})

    except WebSocketDisconnect:
        pass
    except HTTPException as exc:  # pragma: no cover - websocket close path
        with suppress(Exception):
            close_code = 4404 if int(getattr(exc, "status_code", 0) or 0) == 404 else 4400
            reason = str(getattr(exc, "detail", "") or "Room websocket error")
            await websocket.close(code=close_code, reason=reason[:120])
    except Exception as exc:  # pragma: no cover - defensive websocket close path
        with suppress(Exception):
            await websocket.send_json({"type": "error", "detail": f"Room websocket error: {exc}"})
    finally:
        with suppress(Exception):
            disconnected_room, presence = await scouting_room_hub.disconnect(websocket)
            if disconnected_room:
                await _broadcast_presence_message(disconnected_room, presence)
        db.close()
