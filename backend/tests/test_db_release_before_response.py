from __future__ import annotations

import asyncio
import unittest

from fastapi.routing import APIRoute

from app.db.session import get_db
from app.main import app


def _api_routes(routes):
    # Included routers are kept as one object each; their contexts carry the routes.
    for route in routes:
        if isinstance(route, APIRoute):
            yield route.path, route.methods, route.endpoint, route.dependant, route.response_model
        elif hasattr(route, "effective_route_contexts"):
            for ctx in route.effective_route_contexts():
                original = getattr(ctx, "original_route", None)
                if isinstance(original, APIRoute):
                    yield ctx.path, ctx.methods, ctx.endpoint, ctx.dependant, original.response_model


def _depends_on_get_db(dependant) -> bool:
    return any(sub.call is get_db or _depends_on_get_db(sub) for sub in dependant.dependencies)


class ReleaseDbBeforeResponseTests(unittest.TestCase):
    def test_plain_routes_with_a_response_model_return_their_connection_first(self):
        # FastAPI validates a plain-def route's response_model in the threadpool after the
        # route returns, while get_db's session still holds its connection: under load
        # that deadlocked both workers (threads waiting on connections held by requests
        # waiting on threads). Such routes must close the session as they return.
        missing, checked = [], 0
        for path, methods, endpoint, dependant, response_model in _api_routes(app.routes):
            if response_model is None or asyncio.iscoroutinefunction(endpoint) or not _depends_on_get_db(dependant):
                continue
            checked += 1
            direct_db = any(sub.call is get_db and sub.name == "db" for sub in dependant.dependencies)
            if not (getattr(endpoint, "closes_db_before_response", False) and direct_db):
                missing.append(f"{sorted(methods)} {path}")
        self.assertGreaterEqual(checked, 7)
        self.assertEqual(missing, [])

    def test_the_session_is_closed_before_the_route_returns(self):
        from app.db.session import close_db_before_response

        class _Db:
            closed = False

            def close(self):
                self.closed = True

        db = _Db()
        seen_open = []

        @close_db_before_response
        def route(db):
            seen_open.append(not db.closed)
            return {"ok": True}

        self.assertEqual(route(db=db), {"ok": True})
        self.assertEqual(seen_open, [True])
        self.assertTrue(db.closed)
