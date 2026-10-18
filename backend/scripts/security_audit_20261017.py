"""Local-only evidence for the 2026-10-17 security audit; not a passing safety gate.

Run from the repository root with a clean environment:
  env -i PATH=/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin \
    .venv/bin/python backend/scripts/security_audit_20261017.py

No dotenv files, live data, or external connections are used. Assertions describe
the vulnerable behavior at the audited commit. A fix should invalidate that proof.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import sys
import tempfile
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from tests.disposable_config import configure_disposable_database  # noqa: E402

settings = configure_disposable_database(
    "postgresql+psycopg://audit:audit@127.0.0.1:1/audit"
)
settings.redis_url = "redis://127.0.0.1:1/0"


def no_network(*args, **kwargs):
    raise RuntimeError("Network connections are forbidden in this audit proof.")


def cache_proof() -> list[dict]:
    from app.api import edge_cache
    from tests.test_routes_tracks_on_device_sync import (
        EVENT_KEY, RED_TEAM, OnDeviceSessionSyncTests, _session_body,
    )

    fixture = OnDeviceSessionSyncTests()
    fixture.setUp()
    fixture.client.app.add_middleware(edge_cache.EdgeCacheMiddleware)
    observations = []
    try:
        created = fixture.client.post(
            "/tracks/on-device-session", headers=fixture.workspace_headers,
            json=_session_body("disposable-cache-audit"),
        )
        assert created.status_code == 200 and created.json()["status"] == "provisional"
        for query in (
            "include_workspace=true", "include_workspace=1", "include_workspace=yes",
            "include_workspace=on", "include_workspace=false&include_workspace=true",
        ):
            response = fixture.client.get(
                f"/tracks/heatmap/{RED_TEAM}?event_key={EVENT_KEY}&{query}",
                headers=fixture.workspace_headers,
            )
            assert response.status_code == 200 and response.json()["total_points"] == 2
            assert response.json()["own_unreviewed_matches"] == 1
            observation = {
                "query": query, "private_points": response.json()["total_points"],
                "cache_control": response.headers.get("cache-control"),
                "cdn_cache_control": response.headers.get("cdn-cache-control"),
            }
            if query == "include_workspace=true":
                assert observation["cache_control"] == "private, no-store"
            else:
                assert observation["cdn_cache_control"] == "max-age=60, stale-while-revalidate=600"
            observations.append(observation)
    finally:
        fixture.tearDown()
    return observations


def identity_proof() -> dict:
    from urllib.parse import urlencode

    from app.core.security import WORKSPACE_ACCESS_HEADER
    from app.db import models
    from tests.test_team_workspaces import EVENT, TeamWorkspaceTests

    fixture = TeamWorkspaceTests()
    fixture.setUp()
    try:
        with mock.patch("app.api.routes_workspaces.take_workspace_creation_slot", return_value=True):
            lead = fixture._create()
            lead_headers = fixture._headers(lead)
            scout = fixture._join(lead["join_code"], "Scout")
            scout_headers = fixture._headers(scout)
            attacker = fixture._join(lead["join_code"], "Other")
            attacker_headers = fixture._headers(attacker)
            created = fixture.client.post("/scouting/rooms", headers=lead_headers, json={
                "room_key": "audit-room", "event_key": EVENT, "create_if_missing": True,
            })
            assert created.status_code == 200
            joined = fixture.client.post("/scouting/rooms", headers=scout_headers, json={"room_key": "audit-room"}).json()
            scout_headers["X-Room-Access-Token"] = joined["access"]["room_access_token"]
            entry = {"match_key": f"{EVENT}_qm1", "team_key": "frc118", "saved_at_ms": 1000, "points": {"total": 10}}
            saved = fixture.client.post("/scouting/rooms/audit-room/entries", headers=scout_headers,
                                        json={"entry": entry, "client_entry_id": "visible-report-id"})
            assert saved.status_code == 200
            removed = fixture.client.post(f"/workspaces/me/members/{scout['me']['id']}/remove", headers=lead_headers, json={})
            assert removed.status_code == 200
            renamed = fixture.client.patch("/workspaces/me/profile", headers=attacker_headers, json={"display_name": "Scout"})
            assert renamed.status_code == 200
            joined = fixture.client.post("/scouting/rooms", headers=attacker_headers, json={"room_key": "audit-room"}).json()
            attacker_headers["X-Room-Access-Token"] = joined["access"]["room_access_token"]
            changed = fixture.client.post("/scouting/rooms/audit-room/entries", headers=attacker_headers, json={
                "entry": {**entry, "saved_at_ms": 2000, "points": {"total": 999}}, "client_entry_id": "visible-report-id",
            })
            assert changed.status_code == 200 and not changed.json()["created"]
            assert changed.json()["entry"]["entry"]["points"]["total"] == 999
            url = "/scouting/rooms/audit-room/ws?" + urlencode({
                "scout_profile": "Scout", "room_access": joined["access"]["room_access_token"],
                "workspace_access": attacker_headers[WORKSPACE_ACCESS_HEADER],
            })
            with fixture.client.websocket_connect(url) as websocket:
                assert websocket.receive_json()["type"] == "snapshot"
                renamed = fixture.client.patch("/workspaces/me/profile", headers=attacker_headers, json={"display_name": "Renamed"})
                assert renamed.status_code == 200
                websocket.send_json({"type": "save_entry", "entry": {**entry, "saved_at_ms": 3000}, "client_entry_id": "after-rename"})
                assert websocket.receive_json()["type"] == "entry_ack"
            with fixture.SessionLocal() as db:
                row = db.query(models.ScoutingRoomEntry).filter_by(client_entry_id="after-rename").one()
                current = db.get(models.TeamWorkspaceMember, attacker["me"]["id"]).display_name
                assert row.scout_profile == "Scout" and current == "Renamed"
            return {"original_member_id": scout["me"]["id"], "attacker_member_id": attacker["me"]["id"],
                    "overwritten_total": 999, "socket_persisted_name": "Scout", "current_name": "Renamed"}
    finally:
        fixture.tearDown()


def run() -> dict:
    # TestClient transports ASGI requests in process. Block Internet sockets before
    # importing the app, including accidental connections by upstream adapters.
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(socket.socket, "connect", no_network))
        stack.enter_context(mock.patch.object(socket, "create_connection", no_network))
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app import main
        from app.api import edge_cache, routes_pit_scouting
        from app.db import models
        from app.db.base import Base
        from app.db.session import get_db
        from app.services import workspace_limits
        from app.services.push import match_alerts
        from tests.test_workspace_abuse_limits import _FakeRedis
        from tests.workspace_helpers import seed_workspace

        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(engine, "connect")
        def foreign_keys_on(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(engine)

        def disposable_db():
            with factory() as db:
                try:
                    yield db
                finally:
                    db.rollback()

        main.app.dependency_overrides[get_db] = disposable_db
        stack.callback(main.app.dependency_overrides.clear)
        stack.callback(engine.dispose)
        stack.enter_context(mock.patch.object(edge_cache, "_maybe_refresh_settled"))
        redis = _FakeRedis()
        stack.enter_context(mock.patch.object(
            workspace_limits, "get_cache", return_value=mock.Mock(redis_client=redis)
        ))
        # Do not run production lifespan/startup jobs.
        client = TestClient(main.app)
        stack.callback(client.close)
        with factory() as db:
            _, _, headers = seed_workspace(db, name="Disposable audit team")

        result = {}
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6"
            "AAAAAElFTkSuQmCC"
        )
        photos = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="frcmob-audit-photos-")))
        stack.enter_context(mock.patch.object(routes_pit_scouting, "PIT_PHOTOS_ROOT", photos))
        stack.enter_context(mock.patch.object(settings, "pit_photo_quota_bytes_per_workspace_event", len(image)))
        body = {"event_key": "2026auditone", "team_key": "frc1", "image_base64": base64.b64encode(image).decode()}
        first = client.post("/pit-scouting/photo", json=body, headers=headers)
        blocked = client.post("/pit-scouting/photo", json=body, headers=headers)
        another = client.post("/pit-scouting/photo", json={**body, "event_key": "2026audittwo"}, headers=headers)
        assert [first.status_code, blocked.status_code, another.status_code] == [200, 413, 200]
        total = sum(path.stat().st_size for path in photos.rglob("*") if path.is_file())
        with factory() as db:
            assert db.query(models.Event).count() == 0
        assert total == 2 * len(image)
        result["photo_quota_bypass"] = {
            "statuses": [first.status_code, blocked.status_code, another.status_code],
            "quota_per_event_bytes": len(image), "total_written_bytes": total,
            "real_events_in_database": 0,
        }

        statuses = []
        for index in range(3):
            response = client.post("/push/subscribe", json={
                "endpoint": f"https://fcm.googleapis.com/fcm/send/disposable-audit-{index}",
                "keys": {"p256dh": "not-a-real-key", "auth": "also-not-real"},
                "event_key": f"2026audit{index}",
            })
            statuses.append(response.status_code)
        assert statuses == [200, 200, 200]
        fake_tba = mock.Mock()
        fake_tba.event_matches.return_value = []
        with mock.patch.object(match_alerts, "push_configured", return_value=True), \
                mock.patch.object(match_alerts, "TBAClient", return_value=fake_tba):
            with factory() as db:
                count = db.query(models.PushSubscription).count()
                match_alerts.run_match_alert_tick(db)
        assert count == 3 and fake_tba.event_matches.call_count == 3
        result["anonymous_push_persistence_and_scheduler_fanout"] = {
            "registration_statuses": statuses, "stored_rows": count,
            "upstream_calls_intercepted_per_tick": fake_tba.event_matches.call_count,
            "real_network_calls": 0,
        }

        stack.enter_context(mock.patch.object(settings, "workspace_create_max_per_hour", 2))
        statuses = [client.post("/workspaces", json={"name": "Disposable", "display_name": "Lead"}).status_code for _ in range(3)]
        assert statuses == [200, 200, 429]
        result["global_signup_budget_exhaustion"] = {"statuses": statuses, "site_wide_hourly_limit": 2}
        result["private_recording_cache_policy"] = cache_proof()
        result["recycled_and_stale_scout_identity"] = identity_proof()
        return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
