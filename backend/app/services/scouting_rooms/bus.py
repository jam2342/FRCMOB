from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
import os
import secrets
from typing import Any, Awaitable, Callable

from redis import asyncio as redis_async

from app.core.config import settings

logger = logging.getLogger(__name__)
_REDIS_IO_TIMEOUT_SEC = 1.5

RoomBusHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class ScoutingRoomRedisBus:
    def __init__(self) -> None:
        self._enabled = bool(getattr(settings, "scouting_rooms_redis_pubsub_enabled", True))
        self._redis = None
        self._pubsub = None
        self._listener_task: asyncio.Task | None = None
        self._started = False
        self._handler: RoomBusHandler | None = None
        self._channel_prefix = str(
            getattr(settings, "scouting_rooms_channel_prefix", "scouting:rooms:")
        ).strip() or "scouting:rooms:"
        self._instance_id = f"{os.getpid()}-{secrets.token_hex(4)}"
        self._warned_unavailable = False

    @property
    def instance_id(self) -> str:
        return self._instance_id

    async def start(self, *, on_message: RoomBusHandler) -> None:
        if self._started:
            self._handler = on_message
            return
        self._handler = on_message
        if not self._enabled:
            logger.info("Scouting room redis pub/sub is disabled by configuration")
            self._started = True
            return
        try:
            self._redis = redis_async.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=_REDIS_IO_TIMEOUT_SEC,
                socket_timeout=_REDIS_IO_TIMEOUT_SEC,
            )
            await asyncio.wait_for(self._redis.ping(), timeout=_REDIS_IO_TIMEOUT_SEC)
            self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
            await asyncio.wait_for(
                self._pubsub.psubscribe(f"{self._channel_prefix}*"),
                timeout=_REDIS_IO_TIMEOUT_SEC,
            )
            self._listener_task = asyncio.create_task(self._listen_loop())
            self._started = True
            logger.info("Scouting room redis pub/sub started")
        except Exception as exc:
            self._redis = None
            self._pubsub = None
            self._listener_task = None
            self._started = True
            logger.warning(
                "Scouting room redis pub/sub unavailable; using local-only realtime: %s",
                exc,
            )

    async def stop(self) -> None:
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("Scouting room redis listener task failed during shutdown: %s", exc)
        self._listener_task = None

        if self._pubsub is not None:
            with suppress(Exception):
                await self._pubsub.close()
        self._pubsub = None

        if self._redis is not None:
            with suppress(Exception):
                await self._redis.close()
        self._redis = None
        self._handler = None
        self._started = False

    async def publish(self, room_key: str, payload: dict[str, Any]) -> None:
        if not room_key:
            return
        if self._redis is None:
            if self._enabled and not self._warned_unavailable:
                self._warned_unavailable = True
                logger.warning(
                    "Scouting room redis bus publish skipped because redis connection is unavailable"
                )
            return
        message = dict(payload)
        message["_origin_instance_id"] = self._instance_id
        try:
            await self._redis.publish(
                f"{self._channel_prefix}{room_key}",
                json.dumps(message, default=str),
            )
        except Exception as exc:
            logger.warning("Scouting room redis publish failed for room %s: %s", room_key, exc)

    async def _listen_loop(self) -> None:
        pubsub = self._pubsub
        if pubsub is None:
            return
        while True:
            try:
                item = await pubsub.get_message(timeout=1.0)
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type not in {"message", "pmessage"}:
                    continue
                raw_channel = item.get("channel")
                channel = str(raw_channel or "")
                if not channel.startswith(self._channel_prefix):
                    continue
                room_key = channel[len(self._channel_prefix) :].strip().lower()
                if not room_key:
                    continue
                raw_data = item.get("data")
                payload: dict[str, Any] | None = None
                if isinstance(raw_data, dict):
                    payload = dict(raw_data)
                elif isinstance(raw_data, str):
                    try:
                        parsed = json.loads(raw_data)
                        if isinstance(parsed, dict):
                            payload = parsed
                    except Exception:
                        payload = None
                if payload is None:
                    continue
                origin = str(payload.get("_origin_instance_id") or "").strip()
                if origin and origin == self._instance_id:
                    continue
                payload.pop("_origin_instance_id", None)
                handler = self._handler
                if handler is not None:
                    await handler(room_key, payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Scouting room redis listener error: %s", exc)
                await asyncio.sleep(1.0)


scouting_room_bus = ScoutingRoomRedisBus()
