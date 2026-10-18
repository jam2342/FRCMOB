# Cost bounds for anonymous workspaces (creation budget, pit photo quota, empty
# workspace cleanup) and the CSP violation counter.
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_pit_scouting, routes_telemetry
from app.api.routes_maintenance import _security_alerts
from app.api.routes_workspaces import router as workspaces_router
from app.core.config import settings
from app.db import models
from app.db.base import Base
from app.db.session import get_db
from app.services import workspace_limits
from tests.workspace_helpers import add_seeded_member, seed_workspace
from tests.image_helpers import jpeg_bytes


class _FakePipe:
    def __init__(self, redis: "_FakeRedis") -> None:
        self.redis = redis
        self.ops: list = []

    def hincrby(self, key, field, amount):  # noqa: ANN001
        self.ops.append(("hincrby", key, field, amount))

    def hexists(self, key, field):  # noqa: ANN001
        self.ops.append(("hexists", key, field))

    def expire(self, key, seconds):  # noqa: ANN001
        self.ops.append(("expire", key, seconds))

    def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "hincrby":
                bucket = self.redis.hashes.setdefault(op[1], {})
                bucket[op[2]] = str(int(bucket.get(op[2], "0")) + op[3])
            elif op[0] == "hexists":
                results.append(op[2] in self.redis.hashes.get(op[1], {}))
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def incr(self, key):  # noqa: ANN001
        self.values[key] = str(int(self.values.get(key, "0")) + 1)
        return int(self.values[key])

    def expire(self, key, seconds):  # noqa: ANN001
        return True

    def set(self, key, value, ex=None):  # noqa: ANN001
        self.values[key] = value

    def get(self, key):  # noqa: ANN001
        return self.values.get(key)

    def mget(self, keys):  # noqa: ANN001
        return [self.values.get(key) for key in keys]

    def hlen(self, key):  # noqa: ANN001
        return len(self.hashes.get(key, {}))

    def hgetall(self, key):  # noqa: ANN001
        return dict(self.hashes.get(key, {}))

    def pipeline(self):
        return _FakePipe(self)


class _DbCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(self.engine)
        self.redis = _FakeRedis()
        patcher = mock.patch.object(workspace_limits, "get_cache", return_value=mock.Mock(redis_client=self.redis))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _client(self, router, headers=None) -> TestClient:  # noqa: ANN001
        app = FastAPI()
        app.include_router(router)

        def _db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.rollback()
                db.close()

        app.dependency_overrides[get_db] = _db
        client = TestClient(app, headers=headers or {})
        self.addCleanup(client.close)
        return client


class CreationBudgetTests(_DbCase):
    def test_site_wide_hourly_budget_then_a_clear_429_and_an_ops_alert(self):
        client = self._client(workspaces_router)
        body = {"name": "Team", "display_name": "Lead"}
        with mock.patch.object(settings, "workspace_create_max_per_hour", 2):
            codes = [client.post("/workspaces", json=body).status_code for _ in range(3)]
            refused = client.post("/workspaces", json=body)
            status = workspace_limits.creation_budget_status()
        self.assertEqual(codes, [200, 200, 429])
        self.assertIn("Try again in a few minutes", refused.json()["detail"])
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.TeamWorkspace).count(), 2)
        self.assertEqual(status["created_this_hour"], 4)
        self.assertEqual(len(status["budget_hit_hours_24h"]), 1)
        codes = [alert["code"] for alert in _security_alerts({"workspace_creation": status})]
        self.assertEqual(codes, ["workspace_creation_budget_reached"])

    def test_without_redis_creation_still_works(self):
        with mock.patch.object(workspace_limits, "get_cache", return_value=mock.Mock(redis_client=None)):
            self.assertTrue(workspace_limits.take_workspace_creation_slot())


class PitPhotoQuotaTests(_DbCase):
    def setUp(self) -> None:
        super().setUp()
        with self.SessionLocal() as db:
            _workspace, _member, headers = seed_workspace(db)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(routes_pit_scouting, "PIT_PHOTOS_ROOT", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self._client(routes_pit_scouting.router, headers)

    def _upload(self, team: str, event: str = "2026test"):
        jpeg = jpeg_bytes()
        return self.client.post(
            "/pit-scouting/photo",
            json={"event_key": event, "team_key": team, "image_base64": base64.b64encode(jpeg).decode()},
        )

    def test_quota_counts_the_whole_event_and_frees_up_after_a_delete(self):
        with mock.patch.object(settings, "pit_photo_quota_bytes_per_workspace_event", len(jpeg_bytes()) * 2):
            first = self._upload("frc254")
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(self._upload("frc1678").status_code, 200)
            refused = self._upload("frc971")
            self.assertEqual(refused.status_code, 413)
            self.assertIn("Delete a few old ones", refused.json()["detail"])
            # Another event has its own allowance.
            self.assertEqual(self._upload("frc971", event="2026other").status_code, 200)
            deleted = self.client.post(
                "/pit-scouting/photo/delete",
                json={"event_key": "2026test", "team_key": "frc254", "photo_path": first.json()["photo"]},
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(self._upload("frc971").status_code, 200)


class EmptyWorkspaceCleanupTests(_DbCase):
    def _workspace(self, db, name, *, age_days=60, seen_days=None, locked=False, extra_members=0):  # noqa: ANN001
        workspace, member, _ = seed_workspace(db, name=name)
        created = datetime.now(timezone.utc) - timedelta(days=age_days)
        workspace.created_at = created
        workspace.is_locked = locked
        member.joined_at = created
        member.last_seen_at = None if seen_days is None else datetime.now(timezone.utc) - timedelta(days=seen_days)
        for index in range(extra_members):
            extra = add_seeded_member(db, workspace, display_name=f"Scout {index}")
            extra.joined_at = created
        db.commit()
        return workspace.id

    def test_removes_only_old_unused_single_member_workspaces_with_nothing_in_them(self):
        with self.SessionLocal() as db:
            spam = self._workspace(db, "spam")
            stale_seen = self._workspace(db, "seen long ago", seen_days=45)
            recent = self._workspace(db, "new", age_days=3)
            seen = self._workspace(db, "seen this week", seen_days=5)
            locked = self._workspace(db, "locked", locked=True)
            team = self._workspace(db, "real team", extra_members=2)
            with_picklist = self._workspace(db, "has a picklist")
            db.add(models.EventPicklist(workspace_id=with_picklist, event_key="2026test", title="P", slots=[], version=1))
            with_room = self._workspace(db, "has a room")
            db.add(models.ScoutingRoom(room_key="room-x", workspace_id=with_room))
            db.commit()

            result = workspace_limits.delete_abandoned_empty_workspaces(db)

            left = {row.id for row in db.query(models.TeamWorkspace).all()}
        self.assertEqual(result["deleted_workspaces"], 2)
        self.assertEqual(left, {recent, seen, locked, team, with_picklist, with_room})
        self.assertNotIn(spam, left)
        self.assertNotIn(stale_seen, left)
        with self.SessionLocal() as db:
            self.assertEqual(
                db.query(models.TeamWorkspaceMember).filter(models.TeamWorkspaceMember.workspace_id.in_([spam, stale_seen])).count(),
                0,
            )


class CspViolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = _FakeRedis()
        patcher = mock.patch.object(routes_telemetry, "get_cache", return_value=mock.Mock(redis_client=self.redis))
        patcher.start()
        self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(routes_telemetry.router)
        self.client = TestClient(app)

    def _post(self, violations):  # noqa: ANN001
        return self.client.post(
            "/telemetry/csp", content=json.dumps({"violations": violations}), headers={"content-type": "text/plain"}
        )

    def test_counts_clean_reports_and_flattens_anything_odd(self):
        resp = self._post([
            {"page": "/match-center", "directive": "frame-src", "blocked": "https://custom-player.example"},
            {"page": "/match-center", "directive": "frame-src", "blocked": "https://custom-player.example"},
            {"page": "/home", "directive": "script-src-elem", "blocked": "inline"},
            {"page": "/home?team=frc254", "directive": "img-src", "blocked": "https://x.example/path?q=secret"},
            {"page": "/home", "directive": "not-a-directive", "blocked": "inline"},
        ])
        self.assertEqual(resp.status_code, 204)
        rows = routes_telemetry.csp_violation_rows(1)
        as_tuples = {(row["page"], row["directive"], row["blocked"], row["count"]) for row in rows}
        self.assertEqual(as_tuples, {
            ("/match-center", "frame-src", "https://custom-player.example", 2),
            ("/home", "script-src-elem", "inline", 1),
            ("other", "img-src", "other", 1),
        })
        alerts = _security_alerts({"csp_violations_24h": rows})
        self.assertEqual([alert["code"] for alert in alerts], ["csp_violations"])
        self.assertIn("custom-player.example", alerts[0]["message"])

    def test_bad_bodies_are_refused_and_summary_needs_admin(self):
        self.assertEqual(self._post([]).status_code, 400)
        self.assertEqual(
            self.client.post("/telemetry/csp", content=b"nope", headers={"content-type": "text/plain"}).status_code, 400
        )
        with mock.patch.object(settings, "admin_api_key", "k" * 40), \
                mock.patch.object(settings, "enforce_admin_auth_for_writes", True), \
                mock.patch.object(settings, "app_env", "production"):
            self.assertEqual(self.client.get("/telemetry/csp/summary").status_code, 403)
            admin = self.client.get("/telemetry/csp/summary", headers={"X-Admin-Key": "k" * 40})
        self.assertEqual(admin.status_code, 200, admin.text)

    def test_anonymous_reports_pass_the_write_guard(self):
        from starlette.requests import Request

        from app.core import security

        scope = {"type": "http", "method": "POST", "path": "/api/telemetry/csp", "headers": [], "query_string": b""}
        with mock.patch.object(settings, "enforce_admin_auth_for_writes", True), \
                mock.patch.object(settings, "admin_api_key", "k" * 40), \
                mock.patch.object(settings, "app_env", "production"):
            security.enforce_write_request_access(Request(scope))
            # The guard is live: a write elsewhere without the key is refused.
            other = dict(scope, path="/api/automation/regional/season/2026/tick")
            with self.assertRaises(Exception):
                security.enforce_write_request_access(Request(other))


if __name__ == "__main__":
    unittest.main()
