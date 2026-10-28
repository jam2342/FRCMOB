# Tells open pages the moment an event's results change, so a phone at the venue
# shows a score seconds after the server has it instead of after the edge cache
# (15-30 s) and its own poll interval.
#
# The live results cache (routes_matches._cached_live_results_for_event) records a
# version of each event's results in Redis whenever it rebuilds. A scheduler job
# rebuilds it every few seconds for events someone is watching, and each API worker
# reads the versions of its sockets' events and tells those sockets when one changes.
# Pages then refetch with the version in the URL (a new edge-cache key), so the first
# phone gets the fresh answer from the origin and the rest from the edge.
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from contextlib import suppress
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from app.services.cache import get_cache

logger = logging.getLogger(__name__)

_VERSION_PREFIX = "live:results_version:"
_VERSION_TTL_SEC = 6 * 3600
_WATCHED_PREFIX = "live:watched:"
# A watched event stays watched this long after its last socket checked in.
_WATCHED_TTL_SEC = 30
EVENT_KEY_RE = re.compile(r"^\d{4}[a-z0-9]{1,16}$")
# Bounds on what anonymous sockets can cost: per worker, and events refreshed per tick.
MAX_SOCKETS_PER_WORKER = 3000
MAX_WATCHED_EVENTS = 40
POLL_SEC = 2.0
_WATCH_REFRESH_SEC = 10.0


def results_version(results: dict[str, dict[str, Any]]) -> str:
    # What a page shows of a result: whether the match is done and its score.
    parts = sorted(
        (key, bool(row.get("is_completed")), row.get("red_score"), row.get("blue_score"))
        for key, row in results.items()
        if isinstance(row, dict)
    )
    return hashlib.sha1(json.dumps(parts, default=str).encode("utf-8")).hexdigest()[:12]  # nosec B324 - not security


def note_results(event_key: str, results: dict[str, dict[str, Any]]) -> None:
    client = get_cache().redis_client
    if client is None or not results:
        return
    try:
        client.set(_VERSION_PREFIX + event_key, results_version(results), ex=_VERSION_TTL_SEC)
    except Exception:
        logger.warning("live_signal.note_failed event=%s", event_key, exc_info=True)


def _versions(event_keys: list[str]) -> dict[str, str | None]:
    client = get_cache().redis_client
    if client is None or not event_keys:
        return {}
    pipe = client.pipeline()
    for key in event_keys:
        pipe.get(_VERSION_PREFIX + key)
        pipe.set(_WATCHED_PREFIX + key, "1", ex=_WATCHED_TTL_SEC)
    values = pipe.execute()
    return {key: values[2 * i] for i, key in enumerate(event_keys)}


def watched_events() -> list[str]:
    client = get_cache().redis_client
    if client is None:
        return []
    events: list[str] = []
    for raw in client.scan_iter(match=_WATCHED_PREFIX + "*", count=200):
        key = (raw.decode() if isinstance(raw, bytes) else str(raw))[len(_WATCHED_PREFIX):]
        if EVENT_KEY_RE.fullmatch(key):
            events.append(key)
        if len(events) >= MAX_WATCHED_EVENTS:
            break
    return sorted(events)


class LiveResultsHub:
    # The sockets of one API worker, by event, and one loop telling them about changes.
    def __init__(self) -> None:
        self._sockets: dict[str, set[WebSocket]] = {}
        self._versions: dict[str, str | None] = {}
        self._task: asyncio.Task | None = None

    @property
    def socket_count(self) -> int:
        return sum(len(sockets) for sockets in self._sockets.values())

    async def serve(self, websocket: WebSocket, event_key: str) -> None:
        if not EVENT_KEY_RE.fullmatch(event_key):
            await websocket.close(code=4400, reason="Unknown event")
            return
        if self.socket_count >= MAX_SOCKETS_PER_WORKER:
            await websocket.close(code=1013, reason="Busy, try again shortly")
            return
        await websocket.accept()
        sockets = self._sockets.setdefault(event_key, set())
        sockets.add(websocket)
        self._ensure_loop()
        try:
            current = (await asyncio.to_thread(_versions, [event_key])).get(event_key)
            self._versions.setdefault(event_key, current)
            await websocket.send_json({"type": "results", "event_key": event_key, "version": current})
            while True:
                # Pages send nothing that matters; reading notices the disconnect.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("live_signal.socket_closed event=%s", event_key, exc_info=True)
        finally:
            sockets.discard(websocket)
            if not sockets:
                self._sockets.pop(event_key, None)
                self._versions.pop(event_key, None)

    def _ensure_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        while self._sockets:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("live_signal.tick_failed", exc_info=True)
            await asyncio.sleep(POLL_SEC)

    async def tick(self) -> None:
        events = list(self._sockets)
        if not events:
            return
        latest = await asyncio.to_thread(_versions, events)
        for event_key, version in latest.items():
            if version is None or self._versions.get(event_key) == version:
                continue
            self._versions[event_key] = version
            message = {"type": "results", "event_key": event_key, "version": version}
            for socket in list(self._sockets.get(event_key, ())):
                with suppress(Exception):
                    await asyncio.wait_for(socket.send_json(message), timeout=2.0)

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task
        for sockets in list(self._sockets.values()):
            for socket in list(sockets):
                with suppress(Exception):
                    await socket.close(code=1001)
        self._sockets.clear()
        self._versions.clear()


live_results_hub = LiveResultsHub()
