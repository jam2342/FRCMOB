# Request-size cap, health detail, API docs and where a workspace token may travel.
from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.requests import HTTPConnection

from app.api.body_limit import BodyLimitMiddleware
from app.core import security
from app.core.config import settings


class _Body(BaseModel):
    text: str


def _limited_app() -> FastAPI:
    app = FastAPI()

    @app.post("/json")
    def json_route(body: _Body):
        return {"length": len(body.text)}

    @app.post("/raw")
    async def raw_route(request: Request):
        return {"length": len(await request.body())}

    app.add_middleware(BodyLimitMiddleware)
    return app


class BodyLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(settings, "max_request_body_bytes", 2048)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(_limited_app())

    def test_bodies_under_the_cap_pass(self):
        resp = self.client.post("/json", json={"text": "x" * 1000})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"length": 1000})

    def test_declared_oversize_is_refused_before_the_route_runs(self):
        with mock.patch.object(_Body, "model_validate", side_effect=AssertionError("parsed")):
            resp = self.client.post("/json", json={"text": "x" * 5000})
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.json()["detail"], "Request body is too large.")

    def test_streamed_oversize_is_cut_off_with_413(self):
        def chunks():
            for _ in range(10):
                yield b"y" * 1000

        for path in ("/raw", "/json"):
            resp = self.client.post(path, content=chunks(), headers={"content-type": "application/json"})
            self.assertEqual(resp.status_code, 413, path)

    def test_websockets_and_gets_are_untouched(self):
        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"ok": True}

        app.add_middleware(BodyLimitMiddleware)
        self.assertEqual(TestClient(app).get("/ping").json(), {"ok": True})


class HealthDetailTests(unittest.TestCase):
    def setUp(self) -> None:
        from app import main

        self.client = TestClient(main.app)
        patcher = mock.patch.multiple(settings, admin_api_key="test-admin-key-at-least-32-bytes-long")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_visitors_get_status_not_configuration(self):
        health = self.client.get("/health").json()
        self.assertIn("write_auth", health)  # Settings and Events read it
        for key in ("env_validation", "integrations"):
            self.assertNotIn(key, health)
        self.assertNotIn("session_token_secret_configured", health["write_auth"])
        with mock.patch("app.main.SessionLocal"), mock.patch("app.main.redis.from_url"):
            deep = self.client.get("/health/deep").json()
        self.assertIn("env_ok", deep)
        self.assertNotIn("env_validation", deep)

    def test_admins_still_see_the_full_report(self):
        headers = {"X-Admin-Key": "test-admin-key-at-least-32-bytes-long"}
        health = self.client.get("/health", headers=headers).json()
        for key in ("env_validation", "integrations"):
            self.assertIn(key, health)
        with mock.patch("app.main.SessionLocal"), mock.patch("app.main.redis.from_url"):
            deep = self.client.get("/health/deep", headers=headers).json()
        self.assertIn("env_validation", deep)


class ApiDocsTests(unittest.TestCase):
    def test_docs_are_off_in_production(self):
        from app import main

        prop = mock.PropertyMock(return_value=True)
        with mock.patch.object(type(settings), "is_production_like", new_callable=lambda: prop):
            self.assertEqual(set(main._docs_urls().values()), {None})
        prop.return_value = False
        with mock.patch.object(type(settings), "is_production_like", new_callable=lambda: prop):
            self.assertEqual(main._docs_urls()["openapi_url"], "/openapi.json")


class WorkspaceTokenTransportTests(unittest.TestCase):
    def _connection(self, scope_type: str, *, query: bytes = b"", headers=None) -> HTTPConnection:
        return HTTPConnection({
            "type": scope_type,
            "path": "/x",
            "query_string": query,
            "headers": headers or [],
        })

    def test_url_token_counts_only_on_websockets(self):
        query = f"{security.WORKSPACE_ACCESS_QUERY_PARAM}=tok".encode()
        self.assertEqual(security.workspace_access_token_from_request(self._connection("websocket", query=query)), "tok")
        self.assertEqual(security.workspace_access_token_from_request(self._connection("http", query=query)), "")
        header = [(security.WORKSPACE_ACCESS_HEADER.lower().encode(), b"hdr")]
        self.assertEqual(security.workspace_access_token_from_request(self._connection("http", headers=header)), "hdr")


if __name__ == "__main__":
    unittest.main()
