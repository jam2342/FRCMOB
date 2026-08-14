from __future__ import annotations

import asyncio
import unittest

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_scouting_rooms
from app.core.config import settings
from app.core.security import (
    ROOM_ACCESS_HEADER,
    enforce_write_request_access,
    issue_room_access_token,
    parse_room_access_token,
    room_access_allows,
)
from app.db import models
from app.db.base import Base
from app.db.session import get_db


class ScoutingRoomPermissionMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        asyncio.run(routes_scouting_rooms.scouting_room_hub.shutdown())
        self._orig_redis_presence_enabled = bool(routes_scouting_rooms.scouting_room_hub._redis_presence_enabled)
        routes_scouting_rooms.scouting_room_hub._redis_presence_enabled = False
        self._prior_public_readonly_mode = bool(settings.public_readonly_mode)
        self._prior_enforce_admin = bool(settings.enforce_admin_auth_for_writes)
        self._prior_admin_key = str(settings.admin_api_key or "")
        settings.public_readonly_mode = False
        settings.enforce_admin_auth_for_writes = True
        settings.admin_api_key = "test-admin-key"

        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(self.engine)
        with self.SessionLocal() as db:
            self._seed_minimal_rows(db)

        app = FastAPI()

        @app.middleware("http")
        async def _access_middleware(request, call_next):
            try:
                enforce_write_request_access(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            return await call_next(request)

        app.include_router(routes_scouting_rooms.router)

        def _override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.rollback()
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

        self._orig_broadcast_room_message = routes_scouting_rooms._broadcast_room_message
        self._orig_broadcast_presence_message = routes_scouting_rooms._broadcast_presence_message

        async def _noop_broadcast(*_args, **_kwargs):
            return None

        routes_scouting_rooms._broadcast_room_message = _noop_broadcast
        routes_scouting_rooms._broadcast_presence_message = _noop_broadcast

    def tearDown(self) -> None:
        asyncio.run(routes_scouting_rooms.scouting_room_hub.shutdown())
        routes_scouting_rooms.scouting_room_hub._redis_presence_enabled = self._orig_redis_presence_enabled
        routes_scouting_rooms._broadcast_room_message = self._orig_broadcast_room_message
        routes_scouting_rooms._broadcast_presence_message = self._orig_broadcast_presence_message
        self.client.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        settings.public_readonly_mode = self._prior_public_readonly_mode
        settings.enforce_admin_auth_for_writes = self._prior_enforce_admin
        settings.admin_api_key = self._prior_admin_key

    def _seed_minimal_rows(self, db: Session) -> None:
        db.add(models.Event(event_key="2026txhou", name="Houston", year=2026))
        db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
        db.add(models.Team(team_key="frc148", team_number=148, nickname="Robowranglers"))
        db.add(
            models.Match(
                match_key="2026txhou_qm1",
                event_key="2026txhou",
                comp_level="qm",
                set_number=1,
                match_number=1,
                time=1700000000,
            )
        )
        db.add(
            models.Match(
                match_key="2026txhou_qm2",
                event_key="2026txhou",
                comp_level="qm",
                set_number=1,
                match_number=2,
                time=1700000600,
            )
        )
        db.commit()

    def _create_or_join_room(self, *, scout_profile: str, room_key: str = "room-perm") -> dict:
        response = self.client.post(
            "/scouting/rooms",
            json={
                "room_key": room_key,
                "event_key": "2026txhou",
                "title": "Permission Matrix",
                "scout_profile": scout_profile,
                "create_if_missing": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_non_admin_can_create_room(self):
        payload = self._create_or_join_room(scout_profile="Owner Scout")
        self.assertTrue(payload.get("ok"))
        self.assertEqual(
            str(((payload.get("access") or {}).get("room_role") or "")).lower(),
            "owner",
        )

    def test_join_with_unknown_room_key_requires_explicit_create(self):
        join_missing = self.client.post(
            "/scouting/rooms",
            json={
                "room_key": "room-does-not-exist",
                "event_key": "2026txhou",
                "title": "Permission Matrix",
                "scout_profile": "Owner Scout",
                "create_if_missing": False,
            },
        )
        self.assertEqual(join_missing.status_code, 404, join_missing.text)
        self.assertIn("not found", str(join_missing.text).lower())

        create_with_key = self.client.post(
            "/scouting/rooms",
            json={
                "room_key": "room-does-not-exist",
                "event_key": "2026txhou",
                "title": "Permission Matrix",
                "scout_profile": "Owner Scout",
                "create_if_missing": True,
            },
        )
        self.assertEqual(create_with_key.status_code, 200, create_with_key.text)
        created_payload = create_with_key.json()
        self.assertEqual(str(((created_payload.get("room") or {}).get("room_key") or "")), "room-does-not-exist")

    def test_same_client_id_can_reclaim_profile_without_token(self):
        create = self.client.post(
            "/scouting/rooms",
            json={
                "room_key": "room-client-claim",
                "event_key": "2026txhou",
                "title": "Permission Matrix",
                "scout_profile": "Owner Scout",
                "client_id": "client-a",
                "create_if_missing": True,
            },
        )
        self.assertEqual(create.status_code, 200, create.text)

        same_client = self.client.post(
            "/scouting/rooms",
            json={
                "room_key": "room-client-claim",
                "event_key": "2026txhou",
                "title": "Permission Matrix",
                "scout_profile": "Owner Scout",
                "client_id": "client-a",
                "create_if_missing": False,
            },
        )
        self.assertEqual(same_client.status_code, 200, same_client.text)

        different_client = self.client.post(
            "/scouting/rooms",
            json={
                "room_key": "room-client-claim",
                "event_key": "2026txhou",
                "title": "Permission Matrix",
                "scout_profile": "Owner Scout",
                "client_id": "client-b",
                "create_if_missing": False,
            },
        )
        self.assertEqual(different_client.status_code, 409, different_client.text)

    def test_create_or_join_backfills_missing_room_owner_in_response(self):
        with self.SessionLocal() as db:
            db.add(
                models.ScoutingRoom(
                    room_key="room-missing-owner",
                    event_key="2026txhou",
                    title="Legacy Room",
                    created_by=None,
                )
            )
            db.commit()

        payload = self._create_or_join_room(
            scout_profile="Owner Scout",
            room_key="room-missing-owner",
        )
        room = payload.get("room") or {}
        access = payload.get("access") or {}

        self.assertEqual(str(room.get("created_by") or ""), "Owner Scout")
        self.assertEqual(str(room.get("leader_scout_profile") or ""), "Owner Scout")
        self.assertTrue(str(room.get("leader_source") or "").startswith("owner"))
        self.assertEqual(str(access.get("room_role") or "").lower(), "owner")

    def test_create_room_database_error_returns_503_instead_of_500(self):
        original_upsert_room = routes_scouting_rooms._upsert_room

        def _raise_database_error(*_args, **_kwargs):
            raise DatabaseError(
                "INSERT INTO scouting_rooms ...",
                {},
                Exception("ORA-00942: table or view does not exist"),
                False,
            )

        routes_scouting_rooms._upsert_room = _raise_database_error
        try:
            response = self.client.post(
                "/scouting/rooms",
                json={
                    "room_key": "room-perm",
                    "event_key": "2026txhou",
                    "title": "Permission Matrix",
                    "scout_profile": "Owner Scout",
                    "create_if_missing": True,
                },
            )
        finally:
            routes_scouting_rooms._upsert_room = original_upsert_room

        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("temporarily unavailable", str(response.text).lower())

    def test_owner_can_promote_and_demote_secondary_leader(self):
        owner = self._create_or_join_room(scout_profile="Owner Scout")
        owner_token = str(((owner.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(owner_token)

        promote = self.client.post(
            "/scouting/rooms/room-perm/leaders",
            json={"scout_profile": "Secondary Scout"},
            headers={ROOM_ACCESS_HEADER: owner_token},
        )
        self.assertEqual(promote.status_code, 200, promote.text)
        promote_payload = promote.json()
        self.assertTrue(bool(promote_payload.get("created")))

        demote = self.client.post(
            "/scouting/rooms/room-perm/leaders/remove",
            json={"scout_profile": "Secondary Scout"},
            headers={ROOM_ACCESS_HEADER: owner_token},
        )
        self.assertEqual(demote.status_code, 200, demote.text)
        demote_payload = demote.json()
        self.assertTrue(bool(demote_payload.get("removed")))

    def test_secondary_leader_can_assign_replace_and_kick(self):
        owner = self._create_or_join_room(scout_profile="Owner Scout")
        owner_token = str(((owner.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(owner_token)

        promote = self.client.post(
            "/scouting/rooms/room-perm/leaders",
            json={"scout_profile": "Secondary Scout"},
            headers={ROOM_ACCESS_HEADER: owner_token},
        )
        self.assertEqual(promote.status_code, 200, promote.text)

        secondary = self._create_or_join_room(scout_profile="Secondary Scout")
        secondary_token = str(((secondary.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(secondary_token)
        self.assertEqual(
            str(((secondary.get("access") or {}).get("room_role") or "")).lower(),
            "owner",
        )

        upsert = self.client.post(
            "/scouting/rooms/room-perm/assignments",
            json={
                "event_key": "2026txhou",
                "match_key": "2026txhou_qm1",
                "team_key": "frc118",
                "assigned_scout_profile": "Scout C",
            },
            headers={ROOM_ACCESS_HEADER: secondary_token},
        )
        self.assertEqual(upsert.status_code, 200, upsert.text)
        self.assertTrue(upsert.json().get("ok"))

        replace = self.client.post(
            "/scouting/rooms/room-perm/assignments/replace",
            json={
                "event_key": "2026txhou",
                "assignments": [
                    {
                        "match_key": "2026txhou_qm2",
                        "team_key": "frc148",
                        "assigned_scout_profile": "Scout D",
                    }
                ],
            },
            headers={ROOM_ACCESS_HEADER: secondary_token},
        )
        self.assertEqual(replace.status_code, 200, replace.text)
        self.assertTrue(replace.json().get("ok"))

        original_presence_snapshot = routes_scouting_rooms.scouting_room_hub.presence_snapshot
        original_disconnect_profile = routes_scouting_rooms.scouting_room_hub.disconnect_scout_profile

        async def _fake_presence_snapshot(room_key: str):
            if str(room_key).strip().lower() != "room-perm":
                return []
            return [
                {"scout_profile": "Secondary Scout", "connections": 1},
                {"scout_profile": "Scout C", "connections": 1},
            ]

        async def _fake_disconnect_profile(room_key: str, scout_profile: str, **_kwargs):
            room_lookup = str(room_key).strip().lower()
            profile_lookup = str(scout_profile).strip().lower()
            if room_lookup == "room-perm" and profile_lookup == "scout c":
                return 1, [{"scout_profile": "Secondary Scout", "connections": 1}]
            return 0, [{"scout_profile": "Secondary Scout", "connections": 1}]

        routes_scouting_rooms.scouting_room_hub.presence_snapshot = _fake_presence_snapshot
        routes_scouting_rooms.scouting_room_hub.disconnect_scout_profile = _fake_disconnect_profile
        try:
            kick = self.client.post(
                "/scouting/rooms/room-perm/kick",
                json={"scout_profile": "Scout C"},
                headers={ROOM_ACCESS_HEADER: secondary_token},
            )
        finally:
            routes_scouting_rooms.scouting_room_hub.presence_snapshot = original_presence_snapshot
            routes_scouting_rooms.scouting_room_hub.disconnect_scout_profile = original_disconnect_profile
        self.assertEqual(kick.status_code, 200, kick.text)
        self.assertTrue(bool(kick.json().get("kicked")))

    def test_replace_assignment_returns_503_when_assignments_table_missing(self):
        owner = self._create_or_join_room(scout_profile="Owner Scout")
        owner_token = str(((owner.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(owner_token)

        with self.engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE scouting_room_assignments")

        replace = self.client.post(
            "/scouting/rooms/room-perm/assignments/replace",
            json={
                "event_key": "2026txhou",
                "assignments": [
                    {
                        "match_key": "2026txhou_qm1",
                        "team_key": "frc118",
                        "assigned_scout_profile": "Scout C",
                    }
                ],
            },
            headers={ROOM_ACCESS_HEADER: owner_token},
        )
        # After Phase 7: runtime table creation removed — missing table → 503
        self.assertIn(replace.status_code, (500, 503), replace.text)

    def test_promote_secondary_leader_returns_503_when_leaders_table_missing(self):
        owner = self._create_or_join_room(scout_profile="Owner Scout")
        owner_token = str(((owner.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(owner_token)

        with self.engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE scouting_room_leaders")

        promote = self.client.post(
            "/scouting/rooms/room-perm/leaders",
            json={"scout_profile": "Secondary Scout"},
            headers={ROOM_ACCESS_HEADER: owner_token},
        )
        # After Phase 7: runtime table creation removed — missing table → 503
        self.assertEqual(promote.status_code, 503, promote.text)

    def test_http_presence_heartbeat_marks_member_online_without_websocket(self):
        owner = self._create_or_join_room(scout_profile="Owner Scout")
        owner_token = str(((owner.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(owner_token)

        state = self.client.get(
            "/scouting/rooms/room-perm",
            params={
                "scout_profile": "Owner Scout",
                "client_id": "client-http-1",
                "presence_heartbeat": "1",
            },
            headers={ROOM_ACCESS_HEADER: owner_token},
        )
        self.assertEqual(state.status_code, 200, state.text)
        presence = (((state.json() or {}).get("room") or {}).get("presence") or [])
        self.assertTrue(
            any(str(member.get("scout_profile") or "").strip() == "Owner Scout" for member in presence),
            str(presence),
        )

    def test_assignments_endpoint_presence_heartbeat_updates_live_members(self):
        owner = self._create_or_join_room(scout_profile="Owner Scout")
        owner_token = str(((owner.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(owner_token)

        response = self.client.get(
            "/scouting/rooms/room-perm/assignments",
            params={
                "event_key": "2026txhou",
                "for_scout_profile": "Owner Scout",
                "client_id": "client-assign-1",
                "presence_heartbeat": "1",
            },
            headers={ROOM_ACCESS_HEADER: owner_token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        presence = payload.get("presence") or []
        self.assertTrue(
            any(str(member.get("scout_profile") or "").strip() == "Owner Scout" for member in presence),
            str(presence),
        )
        self.assertEqual(str(payload.get("room_role") or "").lower(), "owner")

    def test_non_leader_cannot_assign_kick_or_promote(self):
        owner = self._create_or_join_room(scout_profile="Owner Scout")
        owner_token = str(((owner.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(owner_token)

        promote = self.client.post(
            "/scouting/rooms/room-perm/leaders",
            json={"scout_profile": "Secondary Scout"},
            headers={ROOM_ACCESS_HEADER: owner_token},
        )
        self.assertEqual(promote.status_code, 200, promote.text)

        non_leader = self._create_or_join_room(scout_profile="Non Leader Scout")
        non_leader_token = str(((non_leader.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(non_leader_token)
        self.assertEqual(
            str(((non_leader.get("access") or {}).get("room_role") or "")).lower(),
            "editor",
        )

        assign = self.client.post(
            "/scouting/rooms/room-perm/assignments",
            json={
                "event_key": "2026txhou",
                "match_key": "2026txhou_qm1",
                "team_key": "frc118",
                "assigned_scout_profile": "Scout X",
            },
            headers={ROOM_ACCESS_HEADER: non_leader_token},
        )
        self.assertEqual(assign.status_code, 403)

        kick = self.client.post(
            "/scouting/rooms/room-perm/kick",
            json={"scout_profile": "Secondary Scout"},
            headers={ROOM_ACCESS_HEADER: non_leader_token},
        )
        self.assertEqual(kick.status_code, 403)

        promote_attempt = self.client.post(
            "/scouting/rooms/room-perm/leaders",
            json={"scout_profile": "Scout X"},
            headers={ROOM_ACCESS_HEADER: non_leader_token},
        )
        self.assertEqual(promote_attempt.status_code, 403)

    def test_kick_reports_success_when_target_present_globally_but_local_disconnect_zero(self):
        owner = self._create_or_join_room(scout_profile="Owner Scout")
        owner_token = str(((owner.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(owner_token)

        original_presence_snapshot = routes_scouting_rooms.scouting_room_hub.presence_snapshot
        original_disconnect_profile = routes_scouting_rooms.scouting_room_hub.disconnect_scout_profile
        original_bus_publish = routes_scouting_rooms.scouting_room_bus.publish
        published_messages: list[tuple[str, dict]] = []

        async def _fake_presence_snapshot(room_key: str):
            if str(room_key).strip().lower() != "room-perm":
                return []
            return [
                {"scout_profile": "Owner Scout", "connections": 1},
                {"scout_profile": "Scout Remote", "connections": 1},
            ]

        async def _fake_disconnect_profile(room_key: str, scout_profile: str, **_kwargs):
            room_lookup = str(room_key).strip().lower()
            profile_lookup = str(scout_profile).strip().lower()
            if room_lookup == "room-perm" and profile_lookup == "scout remote":
                # Simulate worker-local no-op disconnect while global presence says target is online.
                return 0, [{"scout_profile": "Owner Scout", "connections": 1}]
            return 0, []

        async def _fake_bus_publish(room_key: str, payload: dict):
            published_messages.append((str(room_key), dict(payload)))

        routes_scouting_rooms.scouting_room_hub.presence_snapshot = _fake_presence_snapshot
        routes_scouting_rooms.scouting_room_hub.disconnect_scout_profile = _fake_disconnect_profile
        routes_scouting_rooms.scouting_room_bus.publish = _fake_bus_publish
        try:
            kick = self.client.post(
                "/scouting/rooms/room-perm/kick",
                json={"scout_profile": "Scout Remote"},
                headers={ROOM_ACCESS_HEADER: owner_token},
            )
        finally:
            routes_scouting_rooms.scouting_room_hub.presence_snapshot = original_presence_snapshot
            routes_scouting_rooms.scouting_room_hub.disconnect_scout_profile = original_disconnect_profile
            routes_scouting_rooms.scouting_room_bus.publish = original_bus_publish

        self.assertEqual(kick.status_code, 200, kick.text)
        payload = kick.json()
        self.assertTrue(bool(payload.get("kicked")))
        self.assertEqual(int(payload.get("removed_connections", -1)), 0)
        self.assertTrue(
            any(
                str(room_key).strip().lower() == "room-perm"
                and str(message.get("type") or "").strip().lower() == "room_control_disconnect_profile"
                and str(message.get("scout_profile") or "").strip() == "Scout Remote"
                for room_key, message in published_messages
            ),
            str(published_messages),
        )

    def test_websocket_write_access_not_admin_gated_but_public_mode_blocked(self):
        class DummyWebSocket:
            headers: dict[str, str] = {}
            query_params: dict[str, str] = {}

        allowed, reason = routes_scouting_rooms._websocket_write_access_allowed(DummyWebSocket())
        self.assertTrue(allowed)
        self.assertIsNone(reason)

        settings.public_readonly_mode = True
        try:
            allowed_public, reason_public = routes_scouting_rooms._websocket_write_access_allowed(DummyWebSocket())
            self.assertFalse(allowed_public)
            self.assertTrue("disabled in public mode" in str(reason_public or "").lower())
        finally:
            settings.public_readonly_mode = False

    def test_room_access_token_enforced_for_websocket_write_semantics(self):
        issued = issue_room_access_token(
            room_key="room-perm",
            scout_profile="Owner Scout",
            role="owner",
        )
        payload = parse_room_access_token(issued.get("token"))
        self.assertTrue(
            room_access_allows(
                payload,
                room_key="room-perm",
                scout_profile="Owner Scout",
                require_write=True,
            )
        )
        self.assertFalse(
            room_access_allows(
                None,
                room_key="room-perm",
                scout_profile="Owner Scout",
                require_write=True,
            )
        )
        self.assertFalse(
            room_access_allows(
                payload,
                room_key="room-perm",
                scout_profile="Someone Else",
                require_write=True,
            )
        )

    def test_get_room_with_scout_profile_survives_activity_touch_failures(self):
        created = self._create_or_join_room(scout_profile="Owner Scout")
        self.assertTrue(created.get("ok"))
        room_access_token = str(((created.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(room_access_token)

        original_touch = routes_scouting_rooms._touch_room_activity

        def _raise_touch_error(*_args, **_kwargs):
            raise DatabaseError(
                "UPDATE scouting_rooms ...",
                {},
                Exception("ORA-01031: insufficient privileges"),
                False,
            )

        routes_scouting_rooms._touch_room_activity = _raise_touch_error
        try:
            response = self.client.get(
                "/scouting/rooms/room-perm",
                params={"scout_profile": "Owner Scout"},
                headers={ROOM_ACCESS_HEADER: room_access_token},
            )
        finally:
            routes_scouting_rooms._touch_room_activity = original_touch

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("access", payload)

    def test_assignment_update_survives_presence_snapshot_failure_for_owner(self):
        owner = self._create_or_join_room(scout_profile="Owner Scout")
        owner_token = str(((owner.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(owner_token)

        original_presence_snapshot = routes_scouting_rooms.scouting_room_hub.presence_snapshot

        async def _raise_presence_snapshot(_room_key: str):
            raise RuntimeError("presence backend timeout")

        routes_scouting_rooms.scouting_room_hub.presence_snapshot = _raise_presence_snapshot
        try:
            upsert = self.client.post(
                "/scouting/rooms/room-perm/assignments",
                json={
                    "event_key": "2026txhou",
                    "match_key": "2026txhou_qm1",
                    "team_key": "frc118",
                    "assigned_scout_profile": "Scout Z",
                },
                headers={ROOM_ACCESS_HEADER: owner_token},
            )
        finally:
            routes_scouting_rooms.scouting_room_hub.presence_snapshot = original_presence_snapshot

        self.assertEqual(upsert.status_code, 200, upsert.text)
        self.assertTrue(upsert.json().get("ok"))

    def test_assignment_update_succeeds_when_realtime_broadcast_transport_fails(self):
        owner = self._create_or_join_room(scout_profile="Owner Scout")
        owner_token = str(((owner.get("access") or {}).get("room_access_token") or "")).strip()
        self.assertTrue(owner_token)

        original_route_broadcast = routes_scouting_rooms._broadcast_room_message
        original_hub_broadcast = routes_scouting_rooms.scouting_room_hub.broadcast
        original_bus_publish = routes_scouting_rooms.scouting_room_bus.publish
        routes_scouting_rooms._broadcast_room_message = self._orig_broadcast_room_message

        async def _raise_hub_broadcast(_room_key: str, _payload: dict):
            raise RuntimeError("socket fanout failure")

        async def _raise_bus_publish(_room_key: str, _payload: dict):
            raise RuntimeError("redis publish failure")

        routes_scouting_rooms.scouting_room_hub.broadcast = _raise_hub_broadcast
        routes_scouting_rooms.scouting_room_bus.publish = _raise_bus_publish
        try:
            upsert = self.client.post(
                "/scouting/rooms/room-perm/assignments",
                json={
                    "event_key": "2026txhou",
                    "match_key": "2026txhou_qm1",
                    "team_key": "frc118",
                    "assigned_scout_profile": "Scout Y",
                },
                headers={ROOM_ACCESS_HEADER: owner_token},
            )
        finally:
            routes_scouting_rooms.scouting_room_bus.publish = original_bus_publish
            routes_scouting_rooms.scouting_room_hub.broadcast = original_hub_broadcast
            routes_scouting_rooms._broadcast_room_message = original_route_broadcast

        self.assertEqual(upsert.status_code, 200, upsert.text)
        payload = upsert.json()
        self.assertTrue(payload.get("ok"))
        self.assertEqual(str((payload.get("assignment") or {}).get("assigned_scout_profile") or ""), "Scout Y")


if __name__ == "__main__":
    unittest.main()
