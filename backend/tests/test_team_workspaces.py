# Team workspaces end to end: joining by code, keeping each team's picklists,
# pit notes, rooms and exports private, and making removal permanent. Runs
# behind the real write middleware with admin enforcement on, as production does.
from __future__ import annotations

import asyncio
import re
import unittest
from unittest import mock

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from app.api import routes_scouting_rooms
from app.api.routes_picklists import router as picklists_router
from app.api.routes_pit_scouting import router as pit_router
from app.api.routes_scouting_insights import router as insights_router
from app.api.routes_workspaces import router as workspaces_router
from app.core.config import settings
from app.core.security import WORKSPACE_ACCESS_HEADER, enforce_write_request_access
from app.db import models
from app.db.base import Base
from app.db.session import get_db
from app.services.workspaces import normalize_join_code

EVENT = "2026txhou"


class TeamWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        asyncio.run(routes_scouting_rooms.scouting_room_hub.shutdown())
        self._orig_presence = bool(routes_scouting_rooms.scouting_room_hub._redis_presence_enabled)
        routes_scouting_rooms.scouting_room_hub._redis_presence_enabled = False
        self._prior = (settings.public_readonly_mode, settings.enforce_admin_auth_for_writes, settings.admin_api_key)
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
            db.add(models.Event(event_key=EVENT, name="Houston", year=2026))
            db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
            db.add(models.Match(match_key=f"{EVENT}_qm1", event_key=EVENT, comp_level="qm",
                                set_number=1, match_number=1, time=1700000000))
            db.commit()

        app = FastAPI()

        @app.middleware("http")
        async def _access_middleware(request, call_next):
            try:
                enforce_write_request_access(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            return await call_next(request)

        for router in (workspaces_router, picklists_router, pit_router, insights_router, routes_scouting_rooms.router):
            app.include_router(router)

        def _override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.rollback()
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)
        self._session_patch = mock.patch.object(routes_scouting_rooms, "SessionLocal", self.SessionLocal)
        self._session_patch.start()

        async def _noop(*_args, **_kwargs):
            return None

        self._broadcast_patches = [
            mock.patch.object(routes_scouting_rooms, "_broadcast_room_message", _noop),
            mock.patch.object(routes_scouting_rooms, "_broadcast_presence_message", _noop),
        ]
        for patch in self._broadcast_patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self._broadcast_patches:
            patch.stop()
        self._session_patch.stop()
        asyncio.run(routes_scouting_rooms.scouting_room_hub.shutdown())
        routes_scouting_rooms.scouting_room_hub._redis_presence_enabled = self._orig_presence
        self.client.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        settings.public_readonly_mode, settings.enforce_admin_auth_for_writes, settings.admin_api_key = self._prior

    # helpers

    def _create(self, name="Robonauts Scouting", display_name="Lead", team=118) -> dict:
        response = self.client.post(
            "/workspaces", json={"name": name, "frc_team_number": team, "display_name": display_name}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _join(self, code: str, display_name: str) -> dict:
        response = self.client.post("/workspaces/join", json={"join_code": code, "display_name": display_name})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def _headers(session: dict) -> dict[str, str]:
        return {WORKSPACE_ACCESS_HEADER: session["access"]["token"]}

    def _picklist(self, headers, title="Morning list") -> dict:
        response = self.client.post(
            "/picklists", json={"event_key": EVENT, "title": title, "slots": [{"team_key": "frc118"}]}, headers=headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["picklist"]

    # join codes

    def test_create_returns_code_leader_and_working_token(self):
        session = self._create()
        self.assertRegex(session["join_code"], r"^[0-9A-HJKMNP-TV-Z]{5}-[0-9A-HJKMNP-TV-Z]{5}$")
        self.assertEqual(session["me"]["role"], "leader")
        self.assertEqual(session["workspace"]["frc_team_number"], 118)
        me = self.client.get("/workspaces/me", headers=self._headers(session))
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual([m["display_name"] for m in me.json()["members"]], ["Lead"])

    def test_join_accepts_lowercase_spaces_and_lookalikes(self):
        code = self._create()["join_code"]
        messy = code.lower().replace("-", " ").replace("0", "o").replace("1", "l")
        self.assertEqual(normalize_join_code(messy), code.replace("-", ""))
        joined = self._join(messy, "Scout Sam")
        self.assertEqual(joined["me"]["role"], "member")
        self.assertEqual(len(joined["members"]), 2)

    def test_wrong_code_and_taken_name_are_rejected(self):
        code = self._create()["join_code"]
        self.assertEqual(
            self.client.post("/workspaces/join", json={"join_code": "ZZZZZ-ZZZZZ", "display_name": "X"}).status_code,
            404,
        )
        taken = self.client.post("/workspaces/join", json={"join_code": code, "display_name": "  lead "})
        self.assertEqual(taken.status_code, 409, taken.text)

    def test_team_data_needs_a_workspace(self):
        missing = self.client.get("/picklists", params={"event_key": EVENT})
        self.assertEqual(missing.status_code, 401)
        self.assertIn("Join or create", missing.json()["detail"])
        forged = self.client.get("/picklists", params={"event_key": EVENT}, headers={WORKSPACE_ACCESS_HEADER: "nope"})
        self.assertEqual(forged.status_code, 401)
        self.assertIn("Rejoin", forged.json()["detail"])

    # isolation between two teams

    def test_picklists_are_private_to_their_workspace(self):
        team_a = self._headers(self._create("A", "Ann"))
        team_b = self._headers(self._create("B", "Ben", team=254))
        picklist = self._picklist(team_a)
        self.assertEqual(picklist["created_by"], "Ann")

        listed = self.client.get("/picklists", params={"event_key": EVENT}, headers=team_b).json()
        self.assertEqual(listed["count"], 0)
        pid = picklist["id"]
        self.assertEqual(self.client.get(f"/picklists/{pid}", headers=team_b).status_code, 404)
        self.assertEqual(
            self.client.put(f"/picklists/{pid}", json={"version": 1, "title": "mine now"}, headers=team_b).status_code,
            404,
        )
        self.assertEqual(self.client.delete(f"/picklists/{pid}", headers=team_b).status_code, 404)
        self.assertEqual(self.client.get(f"/picklists/{pid}", headers=team_a).json()["picklist"]["title"], "Morning list")

    def test_two_teams_keep_separate_pit_notes_on_one_robot(self):
        team_a = self._headers(self._create("A", "Ann"))
        team_b = self._headers(self._create("B", "Ben", team=254))
        for headers, drivetrain in ((team_a, "swerve"), (team_b, "tank")):
            response = self.client.post(
                "/pit-scouting",
                json={"event_key": EVENT, "team_key": "frc118", "payload": {"drivetrain": drivetrain}},
                headers=headers,
            )
            self.assertEqual(response.status_code, 200, response.text)
        a_entry = self.client.get(f"/pit-scouting/{EVENT}/frc118", headers=team_a).json()["entry"]
        b_list = self.client.get("/pit-scouting", params={"event_key": EVENT}, headers=team_b).json()
        self.assertEqual(a_entry["payload"]["drivetrain"], "swerve")
        self.assertEqual(a_entry["scout_profile"], "Ann")
        self.assertEqual([e["payload"]["drivetrain"] for e in b_list["entries"]], ["tank"])

    def test_rooms_and_their_exports_are_private(self):
        team_a = self._headers(self._create("A", "Ann"))
        team_b = self._headers(self._create("B", "Ben", team=254))
        created = self.client.post(
            "/scouting/rooms",
            json={"room_key": "room-a", "event_key": EVENT, "scout_profile": "Ann", "create_if_missing": True},
            headers=team_a,
        )
        self.assertEqual(created.status_code, 200, created.text)
        room_token = created.json()["access"]["room_access_token"]
        saved = self.client.post(
            "/scouting/rooms/room-a/entries",
            json={"entry": {"match_key": f"{EVENT}_qm1", "team_key": "frc118", "form": {"auto_scored": 3}},
                  "scout_profile": "Ann", "client_entry_id": "e1"},
            headers={**team_a, "X-Room-Access-Token": room_token},
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        join_other = self.client.post(
            "/scouting/rooms", json={"room_key": "room-a", "scout_profile": "Ben"}, headers=team_b
        )
        self.assertEqual(join_other.status_code, 404, join_other.text)
        squat = self.client.post(
            "/scouting/rooms",
            json={"room_key": "room-a", "event_key": EVENT, "scout_profile": "Ben", "create_if_missing": True},
            headers=team_b,
        )
        self.assertEqual(squat.status_code, 409, squat.text)
        self.assertEqual(self.client.get("/scouting/rooms/room-a/entries", headers=team_b).status_code, 404)
        self.assertEqual(self.client.get("/scouting/rooms/room-a", headers=team_b).status_code, 404)

        export_a = self.client.get("/scouting/insights/entries-export", params={"event_key": EVENT}, headers=team_a)
        export_b = self.client.get("/scouting/insights/entries-export", params={"event_key": EVENT}, headers=team_b)
        self.assertEqual(export_a.json()["count"], 1)
        self.assertEqual(export_b.json()["count"], 0)
        coverage_b = self.client.get("/scouting/insights/coverage", params={"event_key": EVENT}, headers=team_b)
        self.assertEqual(coverage_b.json()["summary"]["covered_slots"], 0)

    # membership

    def test_removed_member_loses_access_and_old_code_stops_working(self):
        lead_session = self._create()
        lead = self._headers(lead_session)
        old_code = lead_session["join_code"]
        scout_session = self._join(old_code, "Scout Sam")
        scout = self._headers(scout_session)
        self._picklist(lead)
        self.assertEqual(self.client.get("/picklists", params={"event_key": EVENT}, headers=scout).json()["count"], 1)

        # Members can't manage the workspace.
        sam_id = scout_session["me"]["id"]
        self.assertEqual(self.client.post("/workspaces/me/join-code", headers=scout).status_code, 403)
        self.assertEqual(
            self.client.post(f"/workspaces/me/members/{lead_session['me']['id']}/remove", json={}, headers=scout).status_code,
            403,
        )

        removed = self.client.post(f"/workspaces/me/members/{sam_id}/remove", json={}, headers=lead)
        self.assertEqual(removed.status_code, 200, removed.text)
        new_code = removed.json()["join_code"]
        self.assertNotEqual(new_code, old_code)
        self.assertEqual([m["display_name"] for m in removed.json()["members"]], ["Lead"])

        self.assertEqual(self.client.get("/picklists", params={"event_key": EVENT}, headers=scout).status_code, 401)
        self.assertEqual(self.client.get("/workspaces/me", headers=scout).status_code, 401)
        rejoin = self.client.post("/workspaces/join", json={"join_code": old_code, "display_name": "Sam again"})
        self.assertEqual(rejoin.status_code, 404)
        self.assertEqual(self._join(new_code, "New Scout")["me"]["role"], "member")

    def test_leader_rules(self):
        lead_session = self._create()
        lead = self._headers(lead_session)
        scout_session = self._join(lead_session["join_code"], "Scout Sam")
        lead_id = lead_session["me"]["id"]

        self.assertEqual(
            self.client.post(f"/workspaces/me/members/{lead_id}/role", json={"role": "member"}, headers=lead).status_code,
            409,
        )
        self.assertEqual(self.client.post("/workspaces/me/leave", json={}, headers=lead).status_code, 409)
        promoted = self.client.post(
            f"/workspaces/me/members/{scout_session['me']['id']}/role", json={"role": "leader"}, headers=lead
        )
        self.assertEqual(promoted.status_code, 200, promoted.text)
        self.assertEqual(self.client.post("/workspaces/me/leave", json={}, headers=lead).status_code, 200)
        self.assertEqual(self.client.get("/workspaces/me", headers=lead).status_code, 401)

        scout = self._headers(scout_session)
        last = self.client.post("/workspaces/me/leave", json={}, headers=scout)
        self.assertEqual(last.status_code, 409)
        self.assertIn("last member", last.json()["detail"])
        left = self.client.post("/workspaces/me/leave", json={"confirm_last_member": True}, headers=scout)
        self.assertEqual(left.status_code, 200)
        self.assertTrue(left.json()["locked"])
        # The warning says the workspace is locked for good: the old code must not let
        # anyone back in, let alone as its leader.
        rejoin = self.client.post(
            "/workspaces/join", json={"join_code": lead_session["join_code"], "display_name": "Stranger"}
        )
        self.assertEqual(rejoin.status_code, 404)

    def test_admin_recovery_code_makes_the_next_joiner_leader(self):
        session = self._create()
        workspace_id = session["workspace"]["id"]
        with self.SessionLocal() as db:
            for member in db.query(models.TeamWorkspaceMember).all():
                member.role = "member"
            db.commit()
        self.assertEqual(self.client.post(f"/workspaces/admin/{workspace_id}/join-code").status_code, 403)
        recovered = self.client.post(
            f"/workspaces/admin/{workspace_id}/join-code", headers={"X-Admin-Key": "test-admin-key"}
        )
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(self._join(recovered.json()["join_code"], "Mentor")["me"]["role"], "leader")
        listing = self.client.get("/workspaces/admin/list", headers={"X-Admin-Key": "test-admin-key"}).json()
        self.assertEqual(listing["workspaces"][0]["active_members"], 2)
        self.assertNotIn("join_code_hash", listing["workspaces"][0])

    def test_room_identity_is_the_members_name_not_a_typed_one(self):
        lead_session = self._create()
        lead = self._headers(lead_session)
        owner = self._headers(self._join(lead_session["join_code"], "Owner Olive"))
        mallory = self._headers(self._join(lead_session["join_code"], "Mallory"))
        created = self.client.post(
            "/scouting/rooms",
            json={"room_key": "room-own", "event_key": EVENT, "scout_profile": "Owner Olive", "create_if_missing": True},
            headers=owner,
        )
        self.assertEqual(created.json()["access"]["room_role"], "owner")

        # Mallory types the owner's name; the server uses hers, so she stays an editor.
        claimed = self.client.post(
            "/scouting/rooms", json={"room_key": "room-own", "scout_profile": "Owner Olive"}, headers=mallory
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["access"]["room_role"], "editor")
        mallory_room = {**mallory, "X-Room-Access-Token": claimed.json()["access"]["room_access_token"]}
        state = self.client.get("/scouting/rooms/room-own", params={"scout_profile": "Owner Olive"}, headers=mallory_room)
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["access"]["room_role"], "editor")
        kick = self.client.post(
            "/scouting/rooms/room-own/kick",
            json={"scout_profile": "Owner Olive"},
            headers=mallory_room,
        )
        self.assertEqual(kick.status_code, 403, kick.text)

        # The workspace leader runs every team room, whoever opened it.
        led = self.client.post("/scouting/rooms", json={"room_key": "room-own", "scout_profile": "x"}, headers=lead)
        self.assertEqual(led.json()["access"]["room_role"], "owner")

    def test_rename_follows_the_member_through_rooms(self):
        lead_session = self._create()
        scout = self._headers(self._join(lead_session["join_code"], "Sam"))
        created = self.client.post(
            "/scouting/rooms",
            json={"room_key": "room-rn", "event_key": EVENT, "scout_profile": "Sam", "create_if_missing": True},
            headers=scout,
        )
        token = created.json()["access"]["room_access_token"]
        self.client.post(
            "/scouting/rooms/room-rn/entries",
            json={"entry": {"match_key": f"{EVENT}_qm1", "team_key": "frc118"}, "client_entry_id": "e1"},
            headers={**scout, "X-Room-Access-Token": token},
        )
        renamed = self.client.patch("/workspaces/me/profile", json={"display_name": "Samuel"}, headers=scout)
        self.assertEqual(renamed.status_code, 200, renamed.text)
        with self.SessionLocal() as db:
            self.assertEqual(db.get(models.ScoutingRoom, "room-rn").created_by, "Samuel")
            self.assertEqual({e.scout_profile for e in db.query(models.ScoutingRoomEntry).all()}, {"Samuel"})
        rejoined = self.client.post("/scouting/rooms", json={"room_key": "room-rn", "scout_profile": "Samuel"}, headers=scout)
        self.assertEqual(rejoined.json()["access"]["room_role"], "owner")

    def test_a_vacated_name_carries_no_room_rights(self):
        lead_session = self._create()
        lead = self._headers(lead_session)
        owner_session = self._join(lead_session["join_code"], "Olive")
        owner = self._headers(owner_session)
        created = self.client.post(
            "/scouting/rooms",
            json={"room_key": "room-vac", "event_key": EVENT, "scout_profile": "Olive", "create_if_missing": True},
            headers=owner,
        )
        self.assertEqual(created.json()["access"]["room_role"], "owner")
        mallory = self._headers(self._join(lead_session["join_code"], "Mallory"))

        removed = self.client.post(
            f"/workspaces/me/members/{owner_session['me']['id']}/remove", json={"rotate_join_code": False}, headers=lead
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        # Olive's name is free again; taking it must not hand over her room.
        self.assertEqual(
            self.client.patch("/workspaces/me/profile", json={"display_name": "Olive"}, headers=mallory).status_code, 200
        )
        taken = self.client.post("/scouting/rooms", json={"room_key": "room-vac", "scout_profile": "Olive"}, headers=mallory)
        self.assertEqual(taken.status_code, 200, taken.text)
        self.assertNotEqual(taken.json()["access"]["room_role"], "owner")

    def test_a_demoted_leader_cannot_act_on_a_stale_request(self):
        lead_session = self._create()
        lead = self._headers(lead_session)
        other_session = self._join(lead_session["join_code"], "Other")
        # Another leader demotes this one between authorization and action.
        with self.SessionLocal() as db:
            db.get(models.TeamWorkspaceMember, other_session["me"]["id"]).role = "leader"
            db.commit()
        real_lock = __import__("app.api.routes_workspaces", fromlist=["_lock_workspace"])._lock_workspace

        def demote_then_lock(db, actor, *, require_leader):
            with self.SessionLocal() as other_db:
                other_db.get(models.TeamWorkspaceMember, actor.member.id).role = "member"
                other_db.commit()
            return real_lock(db, actor, require_leader=require_leader)

        with mock.patch("app.api.routes_workspaces._lock_workspace", demote_then_lock):
            stale = self.client.post(
                f"/workspaces/me/members/{other_session['me']['id']}/remove", json={}, headers=lead
            )
        self.assertEqual(stale.status_code, 403, stale.text)

    def test_a_silent_socket_closes_even_if_the_disconnect_is_lost(self):
        lead_session = self._create()
        scout_session = self._join(lead_session["join_code"], "Quiet")
        scout = self._headers(scout_session)
        created = self.client.post(
            "/scouting/rooms",
            json={"room_key": "room-quiet", "event_key": EVENT, "scout_profile": "Quiet", "create_if_missing": True},
            headers=scout,
        )
        token = created.json()["access"]["room_access_token"]
        url = (f"/scouting/rooms/room-quiet/ws?scout_profile=Quiet&room_access={token}"
               f"&workspace_access={scout[WORKSPACE_ACCESS_HEADER]}")
        with mock.patch.object(routes_scouting_rooms, "_ROOM_MEMBERSHIP_RECHECK_SEC", 0.2):
            with self.client.websocket_connect(url) as socket:
                self.assertEqual(socket.receive_json()["type"], "snapshot")
                # Removed straight in the database: no disconnect message is sent.
                with self.SessionLocal() as db:
                    from datetime import datetime, timezone

                    db.get(models.TeamWorkspaceMember, scout_session["me"]["id"]).removed_at = datetime.now(timezone.utc)
                    db.commit()
                with self.assertRaises(WebSocketDisconnect) as closed:
                    socket.receive_json()
                self.assertEqual(closed.exception.code, 4401)

    def test_member_reopens_room_from_another_device(self):
        lead = self._headers(self._create())
        body = {"room_key": "room-two-devices", "event_key": EVENT, "scout_profile": "Lead", "create_if_missing": True}
        self.assertEqual(self.client.post("/scouting/rooms", json=body, headers=lead).status_code, 200)
        # A second phone or laptop has no room token yet; it is still the same member.
        again = self.client.post("/scouting/rooms", json={"room_key": "room-two-devices", "client_id": "laptop"}, headers=lead)
        self.assertEqual(again.status_code, 200, again.text)
        state = self.client.get("/scouting/rooms/room-two-devices", params={"scout_profile": "Lead"}, headers=lead)
        self.assertEqual(state.status_code, 200, state.text)

    def test_only_a_current_member_can_be_made_room_leader(self):
        lead_session = self._create()
        lead = self._headers(lead_session)
        created = self.client.post(
            "/scouting/rooms",
            json={"room_key": "room-pro", "event_key": EVENT, "scout_profile": "Lead", "create_if_missing": True},
            headers=lead,
        )
        room = {**lead, "X-Room-Access-Token": created.json()["access"]["room_access_token"]}
        ghost = self.client.post("/scouting/rooms/room-pro/leaders", json={"scout_profile": "Future Scout"}, headers=room)
        self.assertEqual(ghost.status_code, 404, ghost.text)
        self._join(lead_session["join_code"], "Future Scout")
        real = self.client.post("/scouting/rooms/room-pro/leaders", json={"scout_profile": "Future Scout"}, headers=room)
        self.assertEqual(real.status_code, 200, real.text)

    def test_locked_legacy_workspace_cannot_be_joined_or_recovered(self):
        from app.services.workspaces import hash_join_code

        with self.SessionLocal() as db:
            legacy = models.TeamWorkspace(name="Legacy data", join_code_hash=hash_join_code("AAAAA-AAAAA"), is_locked=True)
            db.add(legacy)
            db.commit()
            legacy_id = legacy.id
        refused = self.client.post("/workspaces/join", json={"join_code": "AAAAA-AAAAA", "display_name": "Eve"})
        self.assertEqual(refused.status_code, 404)
        recovery = self.client.post(
            f"/workspaces/admin/{legacy_id}/join-code", headers={"X-Admin-Key": "test-admin-key"}
        )
        self.assertEqual(recovery.status_code, 409, recovery.text)

    def test_removal_closes_an_open_room_socket(self):
        lead_session = self._create()
        lead = self._headers(lead_session)
        scout_session = self._join(lead_session["join_code"], "Scout Sam")
        scout = self._headers(scout_session)
        created = self.client.post(
            "/scouting/rooms",
            json={"room_key": "room-live", "event_key": EVENT, "scout_profile": "Lead", "create_if_missing": True},
            headers=lead,
        )
        self.assertEqual(created.status_code, 200, created.text)
        joined = self.client.post("/scouting/rooms", json={"room_key": "room-live", "scout_profile": "Scout Sam"},
                                  headers=scout)
        self.assertEqual(joined.status_code, 200, joined.text)
        room_token = joined.json()["access"]["room_access_token"]
        url = (f"/scouting/rooms/room-live/ws?scout_profile=Scout%20Sam&room_access={room_token}"
               f"&workspace_access={scout[WORKSPACE_ACCESS_HEADER]}")

        with self.client.websocket_connect(url) as socket:
            self.assertEqual(socket.receive_json()["type"], "snapshot")
            removed = self.client.post(
                f"/workspaces/me/members/{scout_session['me']['id']}/remove", json={}, headers=lead
            )
            self.assertEqual(removed.status_code, 200, removed.text)
            socket.send_json({"type": "request_snapshot"})
            # Exactly one read: a still-open socket would answer with a snapshot.
            with self.assertRaises(WebSocketDisconnect) as closed:
                socket.receive_json()
            self.assertEqual(closed.exception.code, 4401)

        # And a fresh socket without workspace access is refused outright.
        bare = re.sub(r"&workspace_access=[^&]+", "", url)
        with self.assertRaises(WebSocketDisconnect) as refused:
            with self.client.websocket_connect(bare) as socket:
                socket.receive_json()
        self.assertEqual(refused.exception.code, 4401)


if __name__ == "__main__":
    unittest.main()
