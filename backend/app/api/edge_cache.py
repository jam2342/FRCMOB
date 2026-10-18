# Caching headers for every GET, decided in one place.
#
# Public data (schedules, ratings, intel, logos) is the same for every visitor, so a
# CDN in front of the API can answer it near the visitor instead of in Phoenix. The
# rule for the CDN goes in a standard header (CDN-Cache-Control by default, see
# settings.edge_cache_header) and browsers get "no-cache" plus an ETag: they keep
# a copy, revalidate it, and a 304 costs no body. Everything not listed below is
# private and never stored anywhere, and so is any request carrying admin
# credentials (admins can ask for data others can't, e.g. unreviewed tracks).

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Policy:
    max_age: int
    stale_while_revalidate: int


POLICIES = {
    # Changes with every match: live scores, rankings, predictions.
    "live": Policy(5, 15),
    # Changes when ratings are rebuilt.
    "event": Policy(60, 600),
    # Event lists and search.
    "catalog": Policy(600, 86400),
    # Logos and robot photos.
    "static": Policy(86400, 604800),
}
# An event whose last match was more than SETTLED_AFTER_SEC ago doesn't change.
SETTLED = Policy(3600, 604800)
SETTLED_AFTER_SEC = 3 * 86400
_SETTLED_REFRESH_SEC = 1800
PRIVATE = "private, no-store"
# Bodies above this are passed through untouched (no ETag); none of the JSON
# routes come close, media files never get buffered.
_MAX_BUFFER_BYTES = 8 * 1024 * 1024

# (path pattern, policy, where the event key is: a path group, a query parameter,
# or None). Paths are matched without the /api prefix the web app adds.
_RULES: list[tuple[re.Pattern[str], str, str | None]] = [
    (re.compile(r"^/events/suggested$"), "catalog", None),
    (re.compile(r"^/events/search$"), "catalog", None),
    (re.compile(r"^/teams/search$"), "catalog", None),
    (re.compile(r"^/events/(?P<ev>[^/]+)/(?:schedule-with-synergy|rankings|predictions|pick-values|forecast|alliance-outlook|selection-model)$"), "live", "path"),
    (re.compile(r"^/events/(?P<ev>[^/]+)/awards$"), "event", "path"),
    (re.compile(r"^/matches/event/(?P<ev>[^/]+)/(?:schedule|team-live-form|live-stream|alliances)$"), "live", "path"),
    (re.compile(r"^/matches/schedules$"), "live", None),
    (re.compile(r"^/matches/(?P<ev>[0-9]{4}[a-z0-9]+)_[^/]+/phases$"), "event", "path"),
    (re.compile(r"^/scouting/event/(?P<ev>[^/]+)/(?:ratings|teams/history)$"), "event", "path"),
    (re.compile(r"^/scouting/team/[^/]+/breakdown$"), "event", "query"),
    (re.compile(r"^/teams/event/(?P<ev>[^/]+)/intel$"), "event", "path"),
    (re.compile(r"^/teams/[^/]+/intel$"), "event", "query"),
    (re.compile(r"^/teams/[^/]+/(?:logo|robot-image)$"), "static", None),
    (re.compile(r"^/teams/[^/]+/rating-history$"), "event", None),
    (re.compile(r"^/tracks/(?:heatmap|shift-play)/[^/]+$"), "event", "query"),
]

_settled: set[str] = set()
_settled_loaded_at = 0.0
_settled_refreshing = False


def _load_settled_events() -> set[str]:
    from sqlalchemy import func

    from app.db import models
    from app.db.session import SessionLocal

    cutoff = int(time.time()) - SETTLED_AFTER_SEC
    db = SessionLocal()
    try:
        last_match = func.max(func.coalesce(models.Match.actual_time, models.Match.predicted_time, models.Match.time))
        rows = db.query(models.Match.event_key, last_match).group_by(models.Match.event_key).all()
        return {str(key) for key, last in rows if last is not None and int(last) < cutoff}
    finally:
        db.close()


async def _refresh_settled() -> None:
    global _settled, _settled_loaded_at, _settled_refreshing
    try:
        _settled = await asyncio.to_thread(_load_settled_events)
    except Exception as exc:
        logger.warning("edge_cache: settled events unavailable: %s", exc)
    finally:
        _settled_loaded_at = time.monotonic()
        _settled_refreshing = False


def _maybe_refresh_settled() -> None:
    # Never waits: the first requests after start use short TTLs until it loads.
    global _settled_refreshing
    if _settled_refreshing or time.monotonic() - _settled_loaded_at < _SETTLED_REFRESH_SEC:
        return
    _settled_refreshing = True
    asyncio.get_running_loop().create_task(_refresh_settled())


def policy_for(path: str, query_string: str) -> Policy | None:
    normalized = path[4:] if path.startswith("/api/") else path
    query = parse_qs(query_string, keep_blank_values=True)
    if query.get("refresh", ["false"])[0].lower() == "true":
        return None
    # A team's own unreviewed recordings (tracks routes) are for that team only.
    # Parsing booleans again is unsafe: FastAPI accepts 1/yes/on and uses the last
    # duplicate parameter. Its mere presence makes this a potentially private read.
    if "include_workspace" in query:
        return None
    for pattern, name, event_source in _RULES:
        match = pattern.match(normalized)
        if match is None:
            continue
        policy = POLICIES[name]
        event_key = ""
        if event_source == "path":
            event_key = match.group("ev")
        elif event_source == "query":
            event_key = (query.get("event_key") or [""])[0]
        if event_key and name in ("live", "event") and event_key.strip().lower() in _settled:
            return SETTLED
        return policy
    return None


def _has_admin_credentials(headers: list[tuple[bytes, bytes]]) -> bool:
    admin_header = str(getattr(settings.security, "admin_api_header", "") or "X-Admin-Key").lower().encode()
    for name, value in headers:
        # Authorization too: security.py accepts an admin session as a Bearer token.
        if name in (admin_header, b"x-admin-key", b"x-admin-session", b"authorization", b"x-workspace-access", b"x-room-access-token") and value.strip():
            return True
    return False


def _if_none_match(headers: list[tuple[bytes, bytes]]) -> set[str]:
    for name, value in headers:
        if name == b"if-none-match":
            return {tag.strip() for tag in value.decode("latin-1").split(",") if tag.strip()}
    return set()


class EdgeCacheMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "GET" or not settings.edge_cache_enabled:
            await self.app(scope, receive, send)
            return
        _maybe_refresh_settled()
        request_headers = list(scope.get("headers") or [])
        start: Message | None = None
        chunks: list[bytes] = []
        size = 0
        passthrough = False

        async def flush_buffer() -> None:
            nonlocal passthrough
            passthrough = True
            if start is not None:
                await send(start)
            for chunk in chunks:
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            chunks.clear()

        async def wrapped_send(message: Message) -> None:
            nonlocal start, size, passthrough
            if passthrough:
                await send(message)
                return
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=list(message.get("headers") or []))
                content_type = headers.get("content-type", "")
                if message.get("status") != 200 or "application/json" not in content_type:
                    if message.get("status") == 200 and "cache-control" not in headers:
                        headers["Cache-Control"] = PRIVATE
                    passthrough = True
                    await send({**message, "headers": headers.raw})
                    return
                start = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            body = message.get("body", b"")
            chunks.append(body)
            size += len(body)
            if size > _MAX_BUFFER_BYTES:
                await flush_buffer()
                return
            if message.get("more_body"):
                return
            await self._finish(scope, request_headers, start, b"".join(chunks), send)

        await self.app(scope, receive, wrapped_send)

    async def _finish(
        self,
        scope: Scope,
        request_headers: list[tuple[bytes, bytes]],
        start: Message | None,
        body: bytes,
        send: Send,
    ) -> None:
        assert start is not None
        headers = MutableHeaders(raw=list(start.get("headers") or []))
        policy = None if _has_admin_credentials(request_headers) else policy_for(
            scope.get("path", ""), (scope.get("query_string") or b"").decode("latin-1")
        )
        edge_header = str(settings.edge_cache_header or "CDN-Cache-Control")
        if policy is None:
            headers["Cache-Control"] = PRIVATE
            if edge_header.lower() in headers:
                del headers[edge_header]
        else:
            headers["Cache-Control"] = "no-cache"
            headers[edge_header] = f"max-age={policy.max_age}, stale-while-revalidate={policy.stale_while_revalidate}"
        etag = f'W/"{hashlib.blake2b(body, digest_size=12).hexdigest()}"'
        headers["ETag"] = etag
        if etag in _if_none_match(request_headers) or "*" in _if_none_match(request_headers):
            for name in ("content-length", "content-type", "content-encoding"):
                if name in headers:
                    del headers[name]
            await send({"type": "http.response.start", "status": 304, "headers": headers.raw})
            await send({"type": "http.response.body", "body": b""})
            return
        await send({**start, "headers": headers.raw})
        await send({"type": "http.response.body", "body": body})
