# Caps request bodies before any route reads them.
#
# FastAPI parses a JSON body before the handler runs, so the workspace and room
# checks inside a handler come too late to stop a huge upload. A declared size is
# refused up front; a streamed body is counted and cut off once it passes the cap.
# Caddy enforces the same cap at the edge; this covers direct hits on the app.

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

_DETAIL = "Request body is too large."


class _BodyTooLarge(Exception):
    pass


def _limit() -> int:
    return max(1024, int(getattr(settings, "max_request_body_bytes", 10 * 1024 * 1024) or 0))


async def _send_413(send: Send) -> None:
    body = json.dumps({"detail": _DETAIL}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = _limit()
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > limit:
                    await _send_413(send)
                    return
                break

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            # Whatever the route made of a cut-off body (often a 400), the answer is 413.
            if exceeded:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            pass
        except Exception:
            if not exceeded:
                raise
        if exceeded and not response_started:
            await _send_413(send)
