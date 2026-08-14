from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.pool import StaticPool

from app.api import routes_scouting_rooms
from app.core.config import settings
from app.db import models
from app.db.base import Base


class ScoutingRoomIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(self.engine)
        self.db = self.SessionLocal()
        self.db.add(models.Event(event_key="2026txhou", name="Houston", year=2026))
        self.db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
        self.db.add(
            models.Match(
                match_key="2026txhou_qm1",
                event_key="2026txhou",
                comp_level="qm",
                set_number=1,
                match_number=1,
                time=1700000000,
            )
        )
        self.db.add(
            models.ScoutingRoom(
                room_key="room-test",
                event_key="2026txhou",
                title="test room",
                created_by="ScoutA",
            )
        )
        self.db.commit()
        self.room = self.db.get(models.ScoutingRoom, "room-test")
        assert self.room is not None

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_same_client_entry_id_is_idempotent(self):
        payload = {
            "id": "entry-local-1",
            "event_key": "2026txhou",
            "match_key": "2026txhou_qm1",
            "team_key": "frc118",
            "points": {"total": 22},
        }
        row_a, created_a = routes_scouting_rooms._persist_room_entry(
            self.db,
            room=self.room,
            entry_payload=payload,
            scout_profile="ScoutA",
            client_entry_id="client-entry-1",
        )
        row_b, created_b = routes_scouting_rooms._persist_room_entry(
            self.db,
            room=self.room,
            entry_payload=payload,
            scout_profile="ScoutA",
            client_entry_id="client-entry-1",
        )
        self.assertTrue(created_a)
        self.assertFalse(created_b)
        self.assertEqual(row_a.id, row_b.id)

    def test_entry_overall_rating_prefers_explicit_payload(self):
        score = routes_scouting_rooms._entry_overall_scout_rating(
            {
                "overall_scout_rating": {
                    "score_0_100": 87.4,
                }
            }
        )
        self.assertEqual(score, 87.4)

    def test_entry_overall_rating_fallback_from_manual_driver_points(self):
        score = routes_scouting_rooms._entry_overall_scout_rating(
            {
                "manual_rating": {
                    "score_0_100": 80,
                    "breakdown": {"discipline_0_1": 0.9},
                },
                "driver_competency": {"score_0_100": 70},
                "points": {"total": 30, "endgame": 20},
            }
        )
        self.assertIsNotNone(score)
        assert score is not None
        self.assertGreater(score, 70.0)
        self.assertLess(score, 90.0)

    def test_require_scout_profile_rejects_blank(self):
        with self.assertRaises(HTTPException) as context:
            routes_scouting_rooms._require_scout_profile("   ", context="joining a scouting room")
        self.assertEqual(context.exception.status_code, 400)

    def test_resolve_room_leader_prefers_owner_when_present(self):
        leader_profile, leader_source = routes_scouting_rooms._resolve_room_leader(
            self.room,
            [
                {
                    "scout_profile": "ScoutB",
                    "connections": 1,
                    "last_connected_at": "2026-03-05T01:00:00+00:00",
                    "last_seen_at": "2026-03-05T01:01:00+00:00",
                },
                {
                    "scout_profile": "ScoutA",
                    "connections": 1,
                    "last_connected_at": "2026-03-05T00:30:00+00:00",
                    "last_seen_at": "2026-03-05T01:00:30+00:00",
                },
            ],
        )
        self.assertEqual(leader_profile, "ScoutA")
        self.assertEqual(leader_source, "owner_present")

    def test_resolve_room_leader_fallbacks_to_longest_present_member_when_owner_absent(self):
        leader_profile, leader_source = routes_scouting_rooms._resolve_room_leader(
            self.room,
            [
                {
                    "scout_profile": "ScoutB",
                    "connections": 1,
                    "first_connected_at": "2026-03-05T00:40:00+00:00",
                    "last_connected_at": "2026-03-05T01:00:00+00:00",
                    "last_seen_at": "2026-03-05T01:00:00+00:00",
                },
                {
                    "scout_profile": "ScoutC",
                    "connections": 1,
                    "first_connected_at": "2026-03-05T00:50:00+00:00",
                    "last_connected_at": "2026-03-05T01:05:00+00:00",
                    "last_seen_at": "2026-03-05T01:05:30+00:00",
                },
            ],
        )
        self.assertEqual(leader_profile, "ScoutB")
        self.assertEqual(leader_source, "presence_fallback")

    def test_resolve_room_role_with_presence_promotes_fallback_leader(self):
        role = routes_scouting_rooms._resolve_room_role_with_presence(
            self.room,
            "ScoutB",
            presence=[
                {
                    "scout_profile": "ScoutB",
                    "connections": 1,
                    "first_connected_at": "2026-03-05T00:40:00+00:00",
                    "last_connected_at": "2026-03-05T01:00:00+00:00",
                    "last_seen_at": "2026-03-05T01:03:00+00:00",
                },
                {
                    "scout_profile": "ScoutC",
                    "connections": 1,
                    "first_connected_at": "2026-03-05T00:50:00+00:00",
                    "last_connected_at": "2026-03-05T01:05:00+00:00",
                    "last_seen_at": "2026-03-05T01:06:00+00:00",
                },
            ],
            secondary_leader_profiles=[],
        )
        self.assertEqual(role, routes_scouting_rooms.ROOM_ROLE_OWNER)

    def test_resolve_room_role_with_presence_keeps_non_leader_as_editor(self):
        role = routes_scouting_rooms._resolve_room_role_with_presence(
            self.room,
            "ScoutC",
            presence=[
                {
                    "scout_profile": "ScoutB",
                    "connections": 1,
                    "first_connected_at": "2026-03-05T00:40:00+00:00",
                    "last_connected_at": "2026-03-05T01:00:00+00:00",
                    "last_seen_at": "2026-03-05T01:03:00+00:00",
                },
                {
                    "scout_profile": "ScoutC",
                    "connections": 1,
                    "first_connected_at": "2026-03-05T00:50:00+00:00",
                    "last_connected_at": "2026-03-05T01:05:00+00:00",
                    "last_seen_at": "2026-03-05T01:06:00+00:00",
                },
            ],
            secondary_leader_profiles=[],
        )
        self.assertEqual(role, routes_scouting_rooms.ROOM_ROLE_EDITOR)

    def test_upsert_room_assignment_and_assignments_for_scout(self):
        row = routes_scouting_rooms._upsert_room_assignment(
            self.db,
            room=self.room,
            match_key="2026txhou_qm1",
            team_key="frc118",
            assigned_scout_profile="Scout B",
            assigned_by_scout_profile="ScoutA",
            event_key="2026txhou",
        )
        self.assertEqual(row.room_key, "room-test")
        self.assertEqual(row.match_key, "2026txhou_qm1")
        self.assertEqual(row.team_key, "frc118")
        rows = routes_scouting_rooms._load_room_assignments(
            self.db,
            "room-test",
            event_key="2026txhou",
        )
        scoped = routes_scouting_rooms._assignments_for_scout(rows, "scout b")
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped[0].id, row.id)

    def test_upsert_room_assignment_retries_once_on_stale_data_error(self):
        original_commit = self.db.commit
        stale_once = {"raised": False}

        def _flaky_commit():
            if not stale_once["raised"]:
                stale_once["raised"] = True
                raise StaleDataError("simulated concurrent row update")
            return original_commit()

        self.db.commit = _flaky_commit  # type: ignore[assignment]
        try:
            row = routes_scouting_rooms._upsert_room_assignment(
                self.db,
                room=self.room,
                match_key="2026txhou_qm1",
                team_key="frc118",
                assigned_scout_profile="Scout Retry",
                assigned_by_scout_profile="ScoutA",
                event_key="2026txhou",
            )
        finally:
            self.db.commit = original_commit  # type: ignore[assignment]

        self.assertTrue(stale_once["raised"])
        self.assertEqual(str(row.match_key), "2026txhou_qm1")
        self.assertEqual(str(row.team_key), "frc118")
        self.assertEqual(str(row.assigned_scout_profile), "Scout Retry")

    def test_room_role_owner_includes_secondary_leaders(self):
        self.db.add(
            models.ScoutingRoomLeader(
                room_key="room-test",
                scout_profile="Scout B",
                scout_profile_norm="scout b",
                added_by_scout_profile="ScoutA",
                added_by_scout_profile_norm="scouta",
            )
        )
        self.db.commit()
        secondary = routes_scouting_rooms._load_room_secondary_leader_profiles(self.db, "room-test")
        role = routes_scouting_rooms._resolve_room_role(
            self.room,
            "Scout B",
            secondary_leader_profiles=secondary,
        )
        self.assertEqual(role, routes_scouting_rooms.ROOM_ROLE_OWNER)

    def test_upsert_and_remove_room_secondary_leader(self):
        _row, created = routes_scouting_rooms._upsert_room_secondary_leader(
            self.db,
            room=self.room,
            scout_profile="Scout C",
            added_by_scout_profile="ScoutA",
        )
        self.assertTrue(created)
        secondary = routes_scouting_rooms._load_room_secondary_leader_profiles(self.db, "room-test")
        self.assertIn("Scout C", secondary)
        removed = routes_scouting_rooms._remove_room_secondary_leader(
            self.db,
            room=self.room,
            scout_profile="Scout C",
        )
        self.assertTrue(removed)
        secondary_after = routes_scouting_rooms._load_room_secondary_leader_profiles(self.db, "room-test")
        self.assertNotIn("Scout C", secondary_after)

    def test_upsert_room_backfills_missing_creator_for_existing_room(self):
        self.db.add(
            models.ScoutingRoom(
                room_key="room-no-owner",
                event_key="2026txhou",
                title="legacy room",
                created_by=None,
            )
        )
        self.db.commit()

        room = routes_scouting_rooms._upsert_room(
            self.db,
            room_key="room-no-owner",
            event_key="2026txhou",
            title="legacy room",
            created_by="Owner Scout",
        )
        self.assertEqual(str(room.created_by or ""), "Owner Scout")

        role = routes_scouting_rooms._resolve_room_role_with_presence(
            room,
            "Owner Scout",
            presence=[],
            secondary_leader_profiles=[],
        )
        self.assertEqual(role, routes_scouting_rooms.ROOM_ROLE_OWNER)

    def test_room_profile_claim_conflicts_when_profile_is_already_active(self):
        conflict = routes_scouting_rooms._room_profile_claim_conflicts(
            room_key="room-test",
            scout_profile="ScoutA",
            presence=[{"scout_profile": "ScoutA", "connections": 1}],
            existing_room_access_payload=None,
        )
        self.assertTrue(conflict)

    def test_room_profile_claim_does_not_conflict_with_bound_existing_token(self):
        issued = routes_scouting_rooms.issue_room_access_token(
            room_key="room-test",
            scout_profile="ScoutA",
        )
        payload = routes_scouting_rooms.parse_room_access_token(issued.get("token"))
        conflict = routes_scouting_rooms._room_profile_claim_conflicts(
            room_key="room-test",
            scout_profile="ScoutA",
            presence=[{"scout_profile": "ScoutA", "connections": 1}],
            existing_room_access_payload=payload,
        )
        self.assertFalse(conflict)

    def test_room_profile_claim_does_not_conflict_when_profile_not_present(self):
        conflict = routes_scouting_rooms._room_profile_claim_conflicts(
            room_key="room-test",
            scout_profile="ScoutA",
            presence=[{"scout_profile": "ScoutB", "connections": 1}],
            existing_room_access_payload=None,
        )
        self.assertFalse(conflict)

    def test_websocket_write_access_is_not_admin_gated(self):
        prior_public_readonly_mode = bool(settings.public_readonly_mode)
        prior_enforce_admin = bool(settings.enforce_admin_auth_for_writes)
        prior_admin_key = str(settings.admin_api_key or "")
        settings.public_readonly_mode = False
        settings.enforce_admin_auth_for_writes = True
        settings.admin_api_key = "required-for-other-writes"

        class DummyWebSocket:
            headers: dict[str, str] = {}
            query_params: dict[str, str] = {}

        try:
            allowed, reason = routes_scouting_rooms._websocket_write_access_allowed(DummyWebSocket())
            self.assertTrue(allowed)
            self.assertIsNone(reason)
        finally:
            settings.public_readonly_mode = prior_public_readonly_mode
            settings.enforce_admin_auth_for_writes = prior_enforce_admin
            settings.admin_api_key = prior_admin_key

    def test_missing_room_leaders_table_error_detection(self):
        self.assertTrue(
            routes_scouting_rooms._is_missing_room_leaders_table_error(
                Exception('relation "scouting_room_leaders" does not exist')
            )
        )
        self.assertTrue(
            routes_scouting_rooms._is_missing_room_leaders_table_error(
                Exception("no such table: scouting_room_leaders")
            )
        )
        self.assertFalse(
            routes_scouting_rooms._is_missing_room_leaders_table_error(
                Exception("relation \"some_other_table\" does not exist")
            )
        )

    def test_missing_table_error_with_sqlstate_is_scoped_to_requested_table(self):
        class _Orig:
            pgcode = "42P01"

            def __str__(self) -> str:
                return 'relation "some_other_table" does not exist'

        scoped_exc = Exception("wrapped")
        setattr(scoped_exc, "orig", _Orig())
        self.assertFalse(
            routes_scouting_rooms._is_missing_table_error(scoped_exc, "scouting_room_leaders")
        )
        self.assertTrue(
            routes_scouting_rooms._is_missing_table_error(scoped_exc)
        )

    def test_table_privilege_error_detection_handles_postgres_message(self):
        self.assertTrue(
            routes_scouting_rooms._is_table_privilege_error(
                Exception('permission denied for table "scouting_room_assignments"'),
                "scouting_room_assignments",
            )
        )

    def test_runtime_room_table_bootstrap_blocked_in_production_like_env(self):
        prior_app_env = str(getattr(settings, "app_env", "") or "")
        settings.app_env = "production"
        try:
            created = routes_scouting_rooms._bootstrap_missing_room_table(
                self.db,
                "scouting_room_leaders",
                trigger_exc=Exception("missing table"),
            )
            self.assertFalse(created)
        finally:
            settings.app_env = prior_app_env

    def test_room_access_token_handles_malformed_secret_bytes(self):
        prior_admin_key = str(settings.admin_api_key or "")
        prior_session_secret = str(getattr(settings, "admin_session_token_secret", "") or "")
        prior_app_env = str(getattr(settings, "app_env", "") or "")
        # Lone surrogate simulates malformed env decoding in some runtimes.
        settings.app_env = "development"
        settings.admin_session_token_secret = ""
        settings.admin_api_key = "\udce2broken-secret"
        try:
            issued = routes_scouting_rooms.issue_room_access_token(
                room_key="room-test",
                scout_profile="ScoutA",
                role="owner",
            )
            payload = routes_scouting_rooms.parse_room_access_token(issued.get("token"))
            self.assertIsNotNone(payload)
            self.assertEqual(str((payload or {}).get("room_key") or ""), "room-test")
            self.assertEqual(str((payload or {}).get("scout_profile") or ""), "ScoutA")
        finally:
            settings.app_env = prior_app_env
            settings.admin_api_key = prior_admin_key
            settings.admin_session_token_secret = prior_session_secret

    def test_room_access_token_requires_configured_secret_in_production_like_env(self):
        prior_admin_key = str(settings.admin_api_key or "")
        prior_session_secret = str(getattr(settings, "admin_session_token_secret", "") or "")
        prior_app_env = str(getattr(settings, "app_env", "") or "")
        settings.app_env = "production"
        settings.admin_session_token_secret = ""
        settings.admin_api_key = ""
        try:
            with self.assertRaises(RuntimeError):
                routes_scouting_rooms.issue_room_access_token(
                    room_key="room-test",
                    scout_profile="ScoutA",
                    role="owner",
                )
        finally:
            settings.app_env = prior_app_env
            settings.admin_api_key = prior_admin_key
            settings.admin_session_token_secret = prior_session_secret

    def test_dev_fallback_token_secret_is_stable_without_explicit_secret(self):
        from app.core import security

        prior_admin_key = str(settings.admin_api_key or "")
        prior_session_secret = str(getattr(settings, "admin_session_token_secret", "") or "")
        prior_app_env = str(getattr(settings, "app_env", "") or "")
        settings.app_env = "development"
        settings.admin_session_token_secret = ""
        settings.admin_api_key = ""
        try:
            first = security._token_secret_bytes()
            second = security._token_secret_bytes()
            self.assertTrue(isinstance(first, bytes) and len(first) > 0)
            self.assertEqual(first, second)
        finally:
            settings.app_env = prior_app_env
            settings.admin_api_key = prior_admin_key
            settings.admin_session_token_secret = prior_session_secret


if __name__ == "__main__":
    unittest.main()
