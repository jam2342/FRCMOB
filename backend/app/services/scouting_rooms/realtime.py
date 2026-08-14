from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import secrets
from typing import Any

from fastapi import WebSocket
from redis import asyncio as redis_async

from app.core.config import settings

logger = logging.getLogger(__name__)
_REDIS_IO_TIMEOUT_SEC = 1.5

@dataclass
class _ConnectionMeta:
    room_key: str
    scout_profile: str
    client_id: str | None
    connection_id: str
    connected_at: datetime
    seen_at: datetime

class ScoutingRoomRealtimeHub:
    # Realtime websocket fanout with Redis-backed global presence.

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._room_connections: dict[str, set[WebSocket]] = {}
        self._connection_meta: dict[WebSocket, _ConnectionMeta] = {}
        self._presence_keepalive_tasks: dict[WebSocket, asyncio.Task] = {}
        self._http_presence: dict[str, _ConnectionMeta] = {}

        self._redis_presence_enabled = bool(
            getattr(settings, "scouting_rooms_redis_pubsub_enabled", True)
        )
        self._presence_prefix = str(
            getattr(settings, "scouting_rooms_presence_prefix", "scouting:rooms:presence:")
        ).strip() or "scouting:rooms:presence:"
        self._presence_ttl_sec = max(
            10,
            int(getattr(settings, "scouting_rooms_presence_ttl_sec", 30) or 30),
        )
        self._presence_touch_interval_sec = max(
            5,
            min(
                self._presence_ttl_sec - 1,
                int(
                    getattr(settings, "scouting_rooms_presence_touch_interval_sec", 10)
                    or 10
                ),
            ),
        )
        self._ws_send_timeout_sec = max(
            0.25,
            min(
                float(getattr(settings, "scouting_rooms_ws_send_timeout_sec", 1.5) or 1.5),
                10.0,
            ),
        )
        self._redis = None
        self._redis_warned = False

    async def connect(
        self,
        room_key: str,
        websocket: WebSocket,
        *,
        scout_profile: str,
        client_id: str | None,
    ) -> list[dict[str, Any]]:
        await websocket.accept()
        connection_id = f"{client_id or 'client'}-{secrets.token_hex(6)}"
        now = datetime.now(timezone.utc)
        meta = _ConnectionMeta(
            room_key=room_key,
            scout_profile=scout_profile,
            client_id=client_id,
            connection_id=connection_id,
            connected_at=now,
            seen_at=now,
        )
        removed_http_presence: list[_ConnectionMeta] = []
        async with self._lock:
            self._room_connections.setdefault(room_key, set()).add(websocket)
            self._connection_meta[websocket] = meta
            self._presence_keepalive_tasks[websocket] = asyncio.create_task(
                self._presence_keepalive(websocket)
            )
            removed_http_presence = self._pop_http_presence_unlocked(
                room_key=room_key,
                scout_profile=scout_profile,
                client_id=client_id,
            )

        for stale_meta in removed_http_presence:
            await self._delete_presence(stale_meta)

        await self._set_presence(meta)
        return await self.presence_snapshot(room_key)

    async def disconnect(self, websocket: WebSocket) -> tuple[str | None, list[dict[str, Any]]]:
        task = None
        meta = None
        room_key = None
        async with self._lock:
            task = self._presence_keepalive_tasks.pop(websocket, None)
            meta = self._connection_meta.pop(websocket, None)
            if meta is not None:
                room_key = meta.room_key
                room_connections = self._room_connections.get(meta.room_key)
                if room_connections is not None:
                    room_connections.discard(websocket)
                    if not room_connections:
                        self._room_connections.pop(meta.room_key, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("Scouting room presence task failed during disconnect: %s", exc)
        if meta is None or room_key is None:
            return None, []
        await self._delete_presence(meta)
        return room_key, await self.presence_snapshot(room_key)

    async def disconnect_scout_profile(
        self,
        room_key: str,
        scout_profile: str,
        *,
        close_code: int = 4403,
        reason: str = "Removed from room by room leader.",
    ) -> tuple[int, list[dict[str, Any]]]:
        target_room_key = str(room_key or "").strip().lower()
        target_profile_lookup = str(scout_profile or "").strip().lower()
        if not target_room_key:
            return 0, []
        if not target_profile_lookup:
            return 0, await self.presence_snapshot(target_room_key)

        http_targets: list[_ConnectionMeta] = []
        async with self._lock:
            targets = [
                websocket
                for websocket, meta in self._connection_meta.items()
                if str(meta.room_key or "").strip().lower() == target_room_key
                and str(meta.scout_profile or "").strip().lower() == target_profile_lookup
            ]
            http_targets = self._pop_http_presence_unlocked(
                room_key=target_room_key,
                scout_profile=scout_profile,
                client_id=None,
            )

        if not targets and not http_targets:
            return 0, await self.presence_snapshot(target_room_key)

        for websocket in targets:
            with suppress(Exception):
                await websocket.close(code=int(close_code), reason=str(reason or "")[:120])
            with suppress(Exception):
                await self.disconnect(websocket)

        for meta in http_targets:
            await self._delete_presence(meta)

        return len(targets) + len(http_targets), await self.presence_snapshot(target_room_key)

    async def touch(self, websocket: WebSocket) -> None:
        async with self._lock:
            meta = self._connection_meta.get(websocket)
            if meta is not None:
                meta.seen_at = datetime.now(timezone.utc)
        if meta is not None:
            await self._set_presence(meta)

    async def touch_http_presence(
        self,
        room_key: str,
        *,
        scout_profile: str,
        client_id: str | None,
    ) -> list[dict[str, Any]]:
        normalized_room = str(room_key or "").strip().lower()
        normalized_profile = str(scout_profile or "").strip()
        normalized_client_id = str(client_id or "").strip()[:80] or None
        if not normalized_room or not normalized_profile:
            return []

        now = datetime.now(timezone.utc)
        connection_id = self._http_presence_connection_id(
            scout_profile=normalized_profile,
            client_id=normalized_client_id,
        )
        dict_key = self._http_presence_dict_key(normalized_room, connection_id)
        async with self._lock:
            self._cleanup_stale_http_presence_unlocked(now=now)
            existing = self._http_presence.get(dict_key)
            connected_at = existing.connected_at if existing is not None else now
            self._http_presence[dict_key] = _ConnectionMeta(
                room_key=normalized_room,
                scout_profile=normalized_profile,
                client_id=normalized_client_id,
                connection_id=connection_id,
                connected_at=connected_at,
                seen_at=now,
            )
            meta = self._http_presence[dict_key]

        await self._set_presence(meta)
        return await self.presence_snapshot(normalized_room)

    async def presence_snapshot(self, room_key: str) -> list[dict[str, Any]]:
        redis_presence, redis_available = await self._presence_snapshot_from_redis(room_key)
        async with self._lock:
            self._cleanup_stale_http_presence_unlocked()
            local_presence = self._presence_snapshot_local_unlocked(room_key)
        if redis_available:
            if redis_presence:
                return redis_presence
            return local_presence
        return local_presence

    async def profile_has_client_presence(
        self,
        room_key: str,
        *,
        scout_profile: str,
        client_id: str | None,
    ) -> bool:
        # Return True when room/profile presence is already tied to the same client id.
        normalized_room = str(room_key or "").strip().lower()
        normalized_profile = str(scout_profile or "").strip().lower()
        normalized_client_id = str(client_id or "").strip().lower()
        if not normalized_room or not normalized_profile or not normalized_client_id:
            return False

        async with self._lock:
            self._cleanup_stale_http_presence_unlocked()
            for meta in self._connection_meta.values():
                if str(meta.room_key or "").strip().lower() != normalized_room:
                    continue
                if str(meta.scout_profile or "").strip().lower() != normalized_profile:
                    continue
                if str(meta.client_id or "").strip().lower() == normalized_client_id:
                    return True
            for meta in self._http_presence.values():
                if str(meta.room_key or "").strip().lower() != normalized_room:
                    continue
                if str(meta.scout_profile or "").strip().lower() != normalized_profile:
                    continue
                if str(meta.client_id or "").strip().lower() == normalized_client_id:
                    return True

        redis_conn = await self._ensure_redis()
        if redis_conn is None:
            return False
        pattern = f"{self._presence_prefix}{normalized_room}:*"
        try:
            keys = [key async for key in redis_conn.scan_iter(match=pattern, count=200)]
            if not keys:
                return False
            raw_values = await redis_conn.mget(keys)
            for raw in raw_values:
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                payload_profile = str(payload.get("scout_profile") or "").strip().lower()
                payload_client_id = str(payload.get("client_id") or "").strip().lower()
                if payload_profile == normalized_profile and payload_client_id == normalized_client_id:
                    return True
        except Exception as exc:
            logger.warning(
                "Failed to verify room client presence for room %s scout %s: %s",
                normalized_room,
                normalized_profile,
                exc,
            )
        return False

    async def broadcast(self, room_key: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            room_connections = list(self._room_connections.get(room_key, set()))

        if not room_connections:
            return

        async def _send(connection: WebSocket) -> Exception | None:
            try:
                await asyncio.wait_for(
                    connection.send_json(payload),
                    timeout=self._ws_send_timeout_sec,
                )
                return None
            except Exception as exc:
                return exc

        send_results = await asyncio.gather(
            *[_send(connection) for connection in room_connections],
            return_exceptions=False,
        )
        stale = [
            connection
            for connection, send_result in zip(room_connections, send_results)
            if isinstance(send_result, Exception)
        ]

        if not stale:
            return
        disconnect_results = await asyncio.gather(
            *[self.disconnect(websocket) for websocket in stale],
            return_exceptions=True,
        )
        room_presence: dict[str, list[dict[str, Any]]] = {}
        for result in disconnect_results:
            if isinstance(result, Exception):
                continue
            disconnected_room, presence = result
            if disconnected_room:
                room_presence[disconnected_room] = presence
        if room_presence:
            await asyncio.gather(
                *[
                    self.broadcast_presence(disconnected_room, presence)
                    for disconnected_room, presence in room_presence.items()
                ],
                return_exceptions=True,
            )

    async def broadcast_presence(
        self, room_key: str, presence: list[dict[str, Any]] | None = None
    ) -> None:
        payload_presence = presence
        if payload_presence is None:
            payload_presence = await self.presence_snapshot(room_key)
        await self.broadcast(
            room_key,
            {
                "type": "presence",
                "room_key": room_key,
                "members": payload_presence,
            },
        )

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = list(self._presence_keepalive_tasks.values())
            self._presence_keepalive_tasks.clear()
            self._http_presence.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("Scouting room presence task failed during shutdown: %s", exc)
        if self._redis is not None:
            with suppress(Exception):
                await self._redis.close()
            self._redis = None

    async def _presence_keepalive(self, websocket: WebSocket) -> None:
        while True:
            await asyncio.sleep(self._presence_touch_interval_sec)
            async with self._lock:
                meta = self._connection_meta.get(websocket)
            if meta is None:
                return
            meta.seen_at = datetime.now(timezone.utc)
            await self._set_presence(meta)

    def _presence_snapshot_local_unlocked(self, room_key: str) -> list[dict[str, Any]]:
        roster = [
            meta
            for websocket, meta in self._connection_meta.items()
            if meta.room_key == room_key
            and websocket in self._room_connections.get(room_key, set())
        ]
        roster.extend(
            meta
            for meta in self._http_presence.values()
            if meta.room_key == room_key
        )
        counts: Counter[str] = Counter()
        first_connected_by_profile: dict[str, datetime] = {}
        last_connected_by_profile: dict[str, datetime] = {}
        last_seen_by_profile: dict[str, datetime] = {}
        for meta in roster:
            profile = str(meta.scout_profile or "").strip()
            if not profile:
                continue
            counts[profile] += 1
            first_connected = first_connected_by_profile.get(profile)
            if first_connected is None or meta.connected_at < first_connected:
                first_connected_by_profile[profile] = meta.connected_at
            last_connected = last_connected_by_profile.get(profile)
            if last_connected is None or meta.connected_at > last_connected:
                last_connected_by_profile[profile] = meta.connected_at
            last_seen = last_seen_by_profile.get(profile)
            if last_seen is None or meta.seen_at > last_seen:
                last_seen_by_profile[profile] = meta.seen_at
        return [
            {
                "scout_profile": scout_profile,
                "connections": int(count),
                "first_connected_at": (
                    first_connected_by_profile.get(scout_profile).isoformat()
                    if first_connected_by_profile.get(scout_profile) is not None
                    else None
                ),
                "last_connected_at": (
                    last_connected_by_profile.get(scout_profile).isoformat()
                    if last_connected_by_profile.get(scout_profile) is not None
                    else None
                ),
                "last_seen_at": (
                    last_seen_by_profile.get(scout_profile).isoformat()
                    if last_seen_by_profile.get(scout_profile) is not None
                    else None
                ),
            }
            for scout_profile, count in sorted(
                counts.items(),
                key=lambda item: (
                    -item[1],
                    str(last_seen_by_profile.get(item[0]) or ""),
                    item[0],
                ),
            )
        ]

    def _presence_key(self, room_key: str, connection_id: str) -> str:
        return f"{self._presence_prefix}{room_key}:{connection_id}"

    def _http_presence_dict_key(self, room_key: str, connection_id: str) -> str:
        return f"{room_key}:{connection_id}"

    def _http_presence_connection_id(self, *, scout_profile: str, client_id: str | None) -> str:
        seed = str(client_id or scout_profile or "").strip().lower()
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in seed)
        safe = safe.strip("-")[:48]
        if not safe:
            safe = secrets.token_hex(4)
        return f"http-{safe}"

    def _pop_http_presence_unlocked(
        self,
        *,
        room_key: str,
        scout_profile: str,
        client_id: str | None,
    ) -> list[_ConnectionMeta]:
        room_lookup = str(room_key or "").strip().lower()
        scout_lookup = str(scout_profile or "").strip().lower()
        client_lookup = str(client_id or "").strip().lower()
        if not room_lookup or not scout_lookup:
            return []
        removed: list[_ConnectionMeta] = []
        for dict_key, meta in list(self._http_presence.items()):
            if str(meta.room_key or "").strip().lower() != room_lookup:
                continue
            if str(meta.scout_profile or "").strip().lower() != scout_lookup:
                continue
            if client_lookup and str(meta.client_id or "").strip().lower() != client_lookup:
                continue
            removed.append(meta)
            self._http_presence.pop(dict_key, None)
        return removed

    def _cleanup_stale_http_presence_unlocked(self, *, now: datetime | None = None) -> None:
        now_dt = now or datetime.now(timezone.utc)
        stale_keys: list[str] = []
        for dict_key, meta in self._http_presence.items():
            seen_at = meta.seen_at if isinstance(meta.seen_at, datetime) else meta.connected_at
            if not isinstance(seen_at, datetime):
                stale_keys.append(dict_key)
                continue
            age_seconds = (now_dt - seen_at).total_seconds()
            if age_seconds > float(self._presence_ttl_sec):
                stale_keys.append(dict_key)
        for dict_key in stale_keys:
            self._http_presence.pop(dict_key, None)

    async def _ensure_redis(self):
        if not self._redis_presence_enabled:
            return None
        if self._redis is not None:
            return self._redis
        try:
            self._redis = redis_async.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=_REDIS_IO_TIMEOUT_SEC,
                socket_timeout=_REDIS_IO_TIMEOUT_SEC,
            )
            await asyncio.wait_for(self._redis.ping(), timeout=_REDIS_IO_TIMEOUT_SEC)
            return self._redis
        except Exception as exc:
            if not self._redis_warned:
                self._redis_warned = True
                logger.warning(
                    "Scouting room redis presence unavailable; using local-only presence: %s",
                    exc,
                )
            self._redis = None
            return None

    async def _set_presence(self, meta: _ConnectionMeta) -> None:
        redis_conn = await self._ensure_redis()
        if redis_conn is None:
            return
        payload = {
            "room_key": meta.room_key,
            "scout_profile": meta.scout_profile,
            "client_id": meta.client_id,
            "connection_id": meta.connection_id,
            "connected_at": (
                meta.connected_at.isoformat()
                if isinstance(meta.connected_at, datetime)
                else datetime.now(timezone.utc).isoformat()
            ),
            "seen_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await asyncio.wait_for(
                redis_conn.setex(
                    self._presence_key(meta.room_key, meta.connection_id),
                    self._presence_ttl_sec,
                    json.dumps(payload, default=str),
                ),
                timeout=_REDIS_IO_TIMEOUT_SEC,
            )
        except Exception as exc:
            logger.warning(
                "Failed to write room presence for room %s: %s",
                meta.room_key,
                exc,
            )

    async def _delete_presence(self, meta: _ConnectionMeta) -> None:
        redis_conn = await self._ensure_redis()
        if redis_conn is None:
            return
        try:
            await asyncio.wait_for(
                redis_conn.delete(self._presence_key(meta.room_key, meta.connection_id)),
                timeout=_REDIS_IO_TIMEOUT_SEC,
            )
        except Exception as exc:
            logger.warning(
                "Failed to delete room presence for room %s: %s",
                meta.room_key,
                exc,
            )

    async def _presence_snapshot_from_redis(
        self, room_key: str
    ) -> tuple[list[dict[str, Any]], bool]:
        redis_conn = await self._ensure_redis()
        if redis_conn is None:
            return [], False
        pattern = f"{self._presence_prefix}{room_key}:*"
        try:
            keys = await asyncio.wait_for(
                self._scan_presence_keys(redis_conn, pattern),
                timeout=_REDIS_IO_TIMEOUT_SEC,
            )
            if not keys:
                return [], True
            raw_values = await asyncio.wait_for(
                redis_conn.mget(keys),
                timeout=_REDIS_IO_TIMEOUT_SEC,
            )
            counts: Counter[str] = Counter()
            first_connected_by_profile: dict[str, datetime] = {}
            last_connected_by_profile: dict[str, datetime] = {}
            last_seen_by_profile: dict[str, datetime] = {}
            for raw in raw_values:
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                scout_profile = str(payload.get("scout_profile") or "").strip()
                if scout_profile:
                    counts[scout_profile] += 1
                    connected_at = _parse_iso_dt(payload.get("connected_at"))
                    seen_at = _parse_iso_dt(payload.get("seen_at"))
                    if connected_at is not None:
                        first_connected = first_connected_by_profile.get(scout_profile)
                        if first_connected is None or connected_at < first_connected:
                            first_connected_by_profile[scout_profile] = connected_at
                        last_connected = last_connected_by_profile.get(scout_profile)
                        if last_connected is None or connected_at > last_connected:
                            last_connected_by_profile[scout_profile] = connected_at
                    if seen_at is not None:
                        prior_seen = last_seen_by_profile.get(scout_profile)
                        if prior_seen is None or seen_at > prior_seen:
                            last_seen_by_profile[scout_profile] = seen_at
            return [
                {
                    "scout_profile": scout_profile,
                    "connections": int(count),
                    "first_connected_at": (
                        first_connected_by_profile.get(scout_profile).isoformat()
                        if first_connected_by_profile.get(scout_profile) is not None
                        else None
                    ),
                    "last_connected_at": (
                        last_connected_by_profile.get(scout_profile).isoformat()
                        if last_connected_by_profile.get(scout_profile) is not None
                        else None
                    ),
                    "last_seen_at": (
                        last_seen_by_profile.get(scout_profile).isoformat()
                        if last_seen_by_profile.get(scout_profile) is not None
                        else None
                    ),
                }
                for scout_profile, count in sorted(
                    counts.items(),
                    key=lambda item: (
                        -item[1],
                        str(last_seen_by_profile.get(item[0]) or ""),
                        item[0],
                    ),
                )
            ], True
        except Exception as exc:
            logger.warning(
                "Failed to read room presence snapshot for room %s: %s",
                room_key,
                exc,
            )
            return [], False

    async def _scan_presence_keys(self, redis_conn, pattern: str) -> list[str]:
        return [key async for key in redis_conn.scan_iter(match=pattern, count=200)]

def _parse_iso_dt(value: Any) -> datetime | None:
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

scouting_room_hub = ScoutingRoomRealtimeHub()
