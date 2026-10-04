from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_tracks
from app.api.routes_tracks import ON_DEVICE_ANALYSIS_VERSION, router as tracks_router
from app.core.config import settings
from app.db import models
from app.db.base import Base
from app.db.session import get_db
from tests.workspace_helpers import add_seeded_member, headers_for, seed_workspace

EVENT_KEY = "2026txhou"
MATCH_KEY = "2026txhou_qm1"
RED_TEAM = "frc118"
BLUE_TEAM = "frc254"


def _seed_match(db: Session) -> None:
    db.add(models.Event(event_key=EVENT_KEY, name="Houston", year=2026))
    db.add(models.Team(team_key=RED_TEAM, team_number=118, nickname="Robonauts"))
    db.add(models.Team(team_key=BLUE_TEAM, team_number=254, nickname="ChezyPofs"))
    db.add(
        models.Match(
            match_key=MATCH_KEY,
            event_key=EVENT_KEY,
            comp_level="qm",
            set_number=1,
            match_number=1,
            time=1700000000,
        )
    )
    db.add(models.MatchTeam(match_key=MATCH_KEY, team_key=RED_TEAM, event_key=EVENT_KEY, alliance="red", station="r1"))
    db.add(models.MatchTeam(match_key=MATCH_KEY, team_key=BLUE_TEAM, event_key=EVENT_KEY, alliance="blue", station="b1"))
    db.commit()


def _session_body(session_id: str, *, include_unknown: bool = False) -> dict:
    points = {
        RED_TEAM: [
            {"timeSec": 1.0, "fieldX": 2.0, "fieldY": 3.0, "zoneKey": "red_alliance_scoring_zone", "speedMps": 0.5},
            {"timeSec": 2.0, "fieldX": 2.5, "fieldY": 3.2, "zoneKey": "red_alliance_scoring_zone", "speedMps": 0.6},
        ],
        BLUE_TEAM: [
            {"timeSec": 1.0, "fieldX": 14.0, "fieldY": 5.0, "zoneKey": "blue_alliance_scoring_zone", "speedMps": 0.4},
        ],
    }
    if include_unknown:
        points["frc9999"] = [{"timeSec": 1.0, "fieldX": 8.0, "fieldY": 4.0, "zoneKey": None, "speedMps": None}]
    return {
        "id": session_id,
        "eventKey": EVENT_KEY,
        "matchKey": MATCH_KEY,
        "createdAt": 1700000123,
        "synced": False,
        "payload": {
            "points_by_team": points,
            "schema_version": "on_device_session_v2",
            "model_version": "frc_robot_detector_v2",
            "calibration_version": "manual_corners_v1",
            "calibration_rmse_m": 0.1,
            "calibration_verified": True,
            "capture_source": "video",
            "pose_source": "static",
            "pose_fallback_ratio": 0.1,
            "identity_confidence": 0.95,
            "identity_source": "manual",
            "timing_source": "match_clock",
            "capture_to_match_offset_sec": -8.0,
            "shift1_active_alliance": "red",
            "shift1_source": "official_score_breakdown",
            "execution_provider": "wasm",
            "sampled_frame_count": 3,
            "inference_median_ms": 100.0,
            "inference_p90_ms": 120.0,
            "thermal_drift_pct": 2.0,
        },
    }


class OnDeviceSessionSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(self.engine)
        with self.SessionLocal() as db:
            _seed_match(db)
            workspace, _member, self.workspace_headers = seed_workspace(db)
            self.workspace_id = workspace.id

        app = FastAPI()
        app.include_router(tracks_router)

        def _override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.rollback()
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

        # Default to an authorized caller ("a" = admin/dev namespace) so the
        # functional tests are isolated from the auth gate; auth-specific tests
        # re-patch this locally.
        identity_patcher = mock.patch.object(
            routes_tracks, "on_device_sync_identity", return_value=(True, "a")
        )
        identity_patcher.start()
        self.addCleanup(identity_patcher.stop)

    def tearDown(self) -> None:
        self.client.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _count_tracks(self) -> int:
        with self.SessionLocal() as db:
            return db.query(models.RobotTrack).count()

    def test_persists_per_team_field_tracks(self):
        resp = self.client.post("/tracks/on-device-session", json=_session_body("sess-1"))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["match_key"], MATCH_KEY)
        self.assertEqual(body["event_key"], EVENT_KEY)
        self.assertFalse(body["reused_run"])
        self.assertEqual(body["team_count"], 2)
        self.assertEqual(body["points_persisted"], 3)
        self.assertEqual(body["points_by_team"], {RED_TEAM: 2, BLUE_TEAM: 1})
        self.assertEqual(self._count_tracks(), 3)

        with self.SessionLocal() as db:
            rows = db.query(models.RobotTrack).filter(models.RobotTrack.team_key == RED_TEAM).all()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].source, "on_device_pwa_v1")
            self.assertAlmostEqual(rows[0].field_x, 2.0)
            self.assertEqual(rows[0].bbox_x1, 0.0)  # no pixel data on-device
            ctx = db.query(models.AnalysisRunContext).one()
            self.assertEqual(ctx.analysis_version, ON_DEVICE_ANALYSIS_VERSION)
            self.assertTrue(ctx.params_hash.startswith("od2:"))
            self.assertTrue(body["session_key"].startswith("od2:"))
            session = db.query(models.OnDeviceSession).one()
            self.assertEqual(session.status, "provisional")
            self.assertGreater(session.quality_score, 0.8)
            self.assertEqual(session.quality_details["execution_provider"], "wasm")
            self.assertEqual(session.capture_to_match_offset_sec, -8.0)

    def test_resync_same_session_is_idempotent(self):
        first = self.client.post("/tracks/on-device-session", json=_session_body("sess-1")).json()
        second_resp = self.client.post("/tracks/on-device-session", json=_session_body("sess-1"))
        self.assertEqual(second_resp.status_code, 200, second_resp.text)
        second = second_resp.json()
        self.assertTrue(second["reused_run"])
        self.assertEqual(second["run_id"], first["run_id"])
        # rewritten, not duplicated
        self.assertEqual(self._count_tracks(), 3)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.AnalysisRun).count(), 1)

    def test_exact_replay_after_accept_is_idempotent_and_changed_payload_conflicts(self):
        body = _session_body("sess-accepted-replay")
        created = self.client.post("/tracks/on-device-session", json=body)
        session_id = created.json()["on_device_session_id"]
        with mock.patch.object(routes_tracks, "require_admin_access"):
            reviewed = self.client.post(
                f"/tracks/on-device-session/{session_id}/review",
                json={"status": "accepted", "note": "looks good"},
            )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)

        replay = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "accepted")
        changed = _session_body("sess-accepted-replay")
        changed["payload"]["points_by_team"][RED_TEAM][0]["fieldX"] = 2.1
        conflict = self.client.post("/tracks/on-device-session", json=changed)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        with self.SessionLocal() as db:
            row = db.get(models.OnDeviceSession, session_id)
            self.assertEqual(row.status, "accepted")
            self.assertEqual(row.review_note, "looks good")
            self.assertIsNotNone(row.reviewed_at)
        self.assertEqual(self._count_tracks(), 3)

    def test_exact_replay_after_reject_is_idempotent_and_changed_payload_conflicts(self):
        body = _session_body("sess-rejected-replay")
        created = self.client.post("/tracks/on-device-session", json=body)
        session_id = created.json()["on_device_session_id"]
        with mock.patch.object(routes_tracks, "require_admin_access"):
            reviewed = self.client.post(
                f"/tracks/on-device-session/{session_id}/review",
                json={"status": "rejected", "note": "bad framing"},
            )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)

        replay = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "rejected")
        changed = _session_body("sess-rejected-replay")
        changed["payload"]["identity_confidence"] = 0.5
        conflict = self.client.post("/tracks/on-device-session", json=changed)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        with self.SessionLocal() as db:
            row = db.get(models.OnDeviceSession, session_id)
            self.assertEqual(row.status, "rejected")
            self.assertEqual(row.review_note, "bad framing")
            self.assertIsNotNone(row.reviewed_at)
        self.assertEqual(self._count_tracks(), 3)

    def test_unknown_team_skipped_others_persist(self):
        resp = self.client.post(
            "/tracks/on-device-session", json=_session_body("sess-2", include_unknown=True)
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["skipped_unknown_teams"], ["frc9999"])
        self.assertEqual(body["points_persisted"], 3)  # the two valid teams still land
        self.assertEqual(self._count_tracks(), 3)

    def test_only_unknown_teams_rejected_without_creating_run(self):
        body = _session_body("sess-unknown")
        body["payload"]["points_by_team"] = {
            "frc9999": [{"timeSec": 1.0, "fieldX": 8.0, "fieldY": 4.0}]
        }
        resp = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(resp.status_code, 422, resp.text)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.AnalysisRun).count(), 0)
            self.assertEqual(db.query(models.OnDeviceSession).count(), 0)

    def test_unknown_match_404(self):
        body = _session_body("sess-3")
        body["matchKey"] = "2026txhou_qm99"
        resp = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(resp.status_code, 404)

    def test_empty_payload_422(self):
        body = _session_body("sess-4")
        body["payload"] = {"points_by_team": {}}
        resp = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(resp.status_code, 422)

    def test_unreviewed_tracks_excluded_from_heatmap_by_default(self):
        # A fresh upload is provisional until an operator accepts it.
        self.client.post("/tracks/on-device-session", json=_session_body("sess-1"))
        resp = self.client.get(f"/tracks/heatmap/{RED_TEAM}", params={"event_key": EVENT_KEY})
        self.assertEqual(resp.status_code, 200, resp.text)
        hm = resp.json()
        self.assertEqual(hm["total_points"], 0)
        self.assertEqual(hm["match_count"], 0)
        self.assertFalse(hm["include_unreviewed"])

    def test_unreviewed_tracks_included_when_operator_opts_in(self):
        self.client.post("/tracks/on-device-session", json=_session_body("sess-1"))
        with mock.patch.object(settings, "admin_api_key", "test-admin"):
            refused = self.client.get(
                f"/tracks/heatmap/{RED_TEAM}", params={"event_key": EVENT_KEY, "include_unreviewed": "true"}
            )
            resp = self.client.get(
                f"/tracks/heatmap/{RED_TEAM}",
                params={"event_key": EVENT_KEY, "include_unreviewed": "true"},
                headers={"X-Admin-Key": "test-admin"},
            )
        # Teams' unreviewed uploads: operators only.
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertEqual(resp.status_code, 200, resp.text)
        hm = resp.json()
        self.assertEqual(hm["total_points"], 2)
        self.assertEqual(hm["match_count"], 1)
        self.assertTrue(hm["include_unreviewed"])

    def test_accepted_tracks_reach_the_public_heatmap(self):
        created = self.client.post("/tracks/on-device-session", json=_session_body("sess-accepted-map"))
        session_id = created.json()["on_device_session_id"]
        with mock.patch.object(routes_tracks, "require_admin_access"):
            reviewed = self.client.post(
                f"/tracks/on-device-session/{session_id}/review",
                json={"status": "accepted", "force": True},
            )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        hm = self.client.get(f"/tracks/heatmap/{RED_TEAM}", params={"event_key": EVENT_KEY}).json()
        self.assertEqual(hm["total_points"], 2)
        self.assertEqual(hm["match_count"], 1)

    def test_payload_point_cap_rejected(self):
        body = _session_body("sess-cap")
        with mock.patch.object(settings, "on_device_sync_max_total_points", 2):
            resp = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(resp.status_code, 413, resp.text)
        self.assertEqual(self._count_tracks(), 0)

    def test_too_many_teams_rejected(self):
        body = _session_body("sess-teams")
        with mock.patch.object(settings, "on_device_sync_max_teams", 1):
            resp = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(resp.status_code, 413, resp.text)

    def test_unknown_zone_key_dropped(self):
        body = _session_body("sess-zone")
        body["payload"]["points_by_team"][RED_TEAM][0]["zoneKey"] = "totally_made_up_zone"
        resp = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        with self.SessionLocal() as db:
            rows = (
                db.query(models.RobotTrack)
                .filter(models.RobotTrack.team_key == RED_TEAM)
                .order_by(models.RobotTrack.frame_index.asc())
                .all()
            )
            # Client zone labels are ignored; the server recomputes both from field coordinates.
            self.assertNotEqual(rows[0].zone_key, "totally_made_up_zone")
            self.assertEqual(rows[0].zone_key, rows[1].zone_key)

    def test_out_of_bounds_points_rejected(self):
        body = _session_body("sess-bounds")
        body["payload"]["points_by_team"] = {
            RED_TEAM: [{"timeSec": 1.0, "fieldX": 99.0, "fieldY": 3.0}]
        }
        resp = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(self._count_tracks(), 0)

    def test_run_cap_per_match_enforced(self):
        with mock.patch.object(settings, "on_device_sync_max_runs_per_match", 1):
            first = self.client.post("/tracks/on-device-session", json=_session_body("sess-a"))
            self.assertEqual(first.status_code, 200, first.text)
            # a second, distinct session would create a 2nd run → capped
            second = self.client.post("/tracks/on-device-session", json=_session_body("sess-b"))
        self.assertEqual(second.status_code, 429, second.text)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.AnalysisRun).count(), 1)

    def test_resync_under_cap_still_reuses_run(self):
        # The cap only blocks *new* runs; re-syncing an existing session must still work.
        with mock.patch.object(settings, "on_device_sync_max_runs_per_match", 1):
            self.client.post("/tracks/on-device-session", json=_session_body("sess-a"))
            again = self.client.post("/tracks/on-device-session", json=_session_body("sess-a"))
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue(again.json()["reused_run"])

    def test_unauthenticated_rejected_when_token_required(self):
        with mock.patch.object(routes_tracks, "on_device_sync_identity", return_value=(False, "n")):
            resp = self.client.post("/tracks/on-device-session", json=_session_body("sess-x"))
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(self._count_tracks(), 0)

    def test_room_token_identity_namespaces_session(self):
        with mock.patch.object(
            routes_tracks, "on_device_sync_identity", return_value=(True, "r:alice")
        ):
            resp = self.client.post("/tracks/on-device-session", json=_session_body("sess-1"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["session_key"].startswith("od2:"))
        self.assertNotIn("alice", resp.json()["session_key"])

    def test_room_token_cannot_sync_another_events_match(self):
        with self.SessionLocal() as db:
            db.add(models.Event(event_key="2026other", name="Other", year=2026))
            db.add(models.ScoutingRoom(
                room_key="other-room",
                workspace_id=self.workspace_id,
                event_key="2026other",
                title="Other room",
                archived=False,
            ))
            db.commit()
        with (
            mock.patch.object(
                routes_tracks,
                "on_device_sync_identity",
                return_value=(True, "r:other-room:alice"),
            ),
            mock.patch.object(
                routes_tracks,
                "room_access_payload_from_request",
                return_value={"room_key": "other-room", "role": "editor"},
            ),
        ):
            resp = self.client.post(
                "/tracks/on-device-session",
                json=_session_body("sess-wrong-event"),
                headers=self.workspace_headers,
            )
        self.assertEqual(resp.status_code, 403, resp.text)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.OnDeviceSession).count(), 0)

    def test_workspace_member_can_sync_without_a_room(self):
        # The PWA sends only workspace access; that alone must authorize a scout.
        prior = settings.on_device_sync_require_signed_token
        settings.on_device_sync_require_signed_token = True
        try:
            with mock.patch.object(routes_tracks, "on_device_sync_identity", return_value=(False, "n")):
                anonymous = self.client.post("/tracks/on-device-session", json=_session_body("sess-anon"))
                member = self.client.post(
                    "/tracks/on-device-session", json=_session_body("sess-member"), headers=self.workspace_headers
                )
        finally:
            settings.on_device_sync_require_signed_token = prior
        self.assertEqual(anonymous.status_code, 403, anonymous.text)
        self.assertEqual(member.status_code, 200, member.text)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.OnDeviceSession).count(), 1)

    def test_declared_workspace_must_match_workspace_caller(self):
        body = _session_body("sess-wrong-workspace")
        body["workspaceId"] = self.workspace_id + 1
        with mock.patch.object(routes_tracks, "on_device_sync_identity", return_value=(False, "n")):
            response = self.client.post(
                "/tracks/on-device-session", json=body, headers=self.workspace_headers
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self._count_tracks(), 0)

    def _sync_with_room_token(self, session_id: str, headers: dict[str, str]):
        with (
            mock.patch.object(
                routes_tracks,
                "on_device_sync_identity",
                return_value=(True, "r:team-room:alice"),
            ),
            mock.patch.object(
                routes_tracks,
                "room_access_payload_from_request",
                return_value={"room_key": "team-room", "role": "editor"},
            ),
        ):
            return self.client.post("/tracks/on-device-session", json=_session_body(session_id), headers=headers)

    def test_room_token_sync_requires_current_member_of_the_rooms_workspace(self):
        with self.SessionLocal() as db:
            db.add(models.ScoutingRoom(
                room_key="team-room", workspace_id=self.workspace_id, event_key=EVENT_KEY, archived=False,
            ))
            workspace = db.get(models.TeamWorkspace, self.workspace_id)
            removed = add_seeded_member(db, workspace, display_name="Gone")
            removed.removed_at = datetime.now(timezone.utc)
            other_workspace, _other_member, other_headers = seed_workspace(db, name="Other Team")
            removed_headers = headers_for(removed)
            db.commit()

        self.assertEqual(self._sync_with_room_token("sess-no-workspace", {}).status_code, 403)
        self.assertEqual(self._sync_with_room_token("sess-other-team", other_headers).status_code, 403)
        # Revoked workspace access is a 401 everywhere, room token or not.
        self.assertEqual(self._sync_with_room_token("sess-removed", removed_headers).status_code, 401)
        ok = self._sync_with_room_token("sess-member", self.workspace_headers)
        self.assertEqual(ok.status_code, 200, ok.text)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.OnDeviceSession).count(), 1)

    def test_control_character_only_session_id_rejected(self):
        resp = self.client.post(
            "/tracks/on-device-session",
            json=_session_body("\n\r"),
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.OnDeviceSession).count(), 0)

    def test_review_enforces_quality_threshold(self):
        body = _session_body("sess-review")
        body["payload"].pop("calibration_rmse_m")
        body["payload"]["calibration_verified"] = False
        body["payload"]["capture_source"] = "unknown"
        body["payload"]["pose_source"] = "unknown"
        body["payload"].pop("pose_fallback_ratio")
        body["payload"].pop("identity_confidence")
        body["payload"]["timing_source"] = "unknown"
        created = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(created.status_code, 200, created.text)
        session_id = created.json()["on_device_session_id"]
        with mock.patch.object(routes_tracks, "require_admin_access"):
            rejected = self.client.post(
                f"/tracks/on-device-session/{session_id}/review",
                json={"status": "accepted"},
            )
            forced = self.client.post(
                f"/tracks/on-device-session/{session_id}/review",
                json={"status": "accepted", "force": True, "note": "manual QA"},
            )
            queue = self.client.get(
                "/tracks/on-device-sessions",
                params={"status": "accepted", "event_key": EVENT_KEY},
            )
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(forced.status_code, 200, forced.text)
        self.assertEqual(forced.json()["status"], "accepted")
        self.assertEqual(queue.status_code, 200, queue.text)
        self.assertEqual(queue.json()["count"], 1)

    def test_legacy_payload_scores_zero_even_with_v2_quality_fields(self):
        body = _session_body("sess-legacy-rich")
        body["payload"]["schema_version"] = "on_device_session_v1_legacy"
        created = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(created.status_code, 200, created.text)
        payload = created.json()
        self.assertEqual(payload["quality_score"], 0.0)
        self.assertFalse(payload["quality"]["eligible_for_review"])
        self.assertTrue(payload["quality"]["acceptance_requires_force"])
        self.assertEqual(
            payload["quality"]["promotion_blocker"],
            "legacy_or_unrecognized_schema",
        )

        with mock.patch.object(routes_tracks, "require_admin_access"):
            rejected = self.client.post(
                f"/tracks/on-device-session/{payload['on_device_session_id']}/review",
                json={"status": "accepted"},
            )
        self.assertEqual(rejected.status_code, 409, rejected.text)

    def test_static_handheld_pose_does_not_clear_tuned_threshold(self):
        body = _session_body("sess-static-handheld")
        body["payload"].update(
            {
                "capture_source": "camera",
                "pose_source": "static",
                "identity_confidence": 1.0,
                "timing_source": "manual",
            }
        )
        created = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(created.status_code, 200, created.text)
        payload = created.json()
        self.assertAlmostEqual(payload["quality_score"], 0.69, places=4)
        self.assertEqual(payload["quality"]["minimum_quality_score"], 0.8)
        self.assertFalse(payload["quality"]["eligible_for_review"])
        self.assertFalse(payload["quality"]["ratings_eligible"])
        self.assertFalse(payload["quality"]["training_eligible"])

    def test_on_device_run_excluded_from_authoritative_shift_play(self):
        # The Team Center "Attack vs Defense" view must never select an
        # on-device run as the match's authoritative video analysis.
        from app.services.auto_scout.shift_play import summarize_team_shift_play

        self.client.post("/tracks/on-device-session", json=_session_body("sess-1"))
        with self.SessionLocal() as db:
            summary = summarize_team_shift_play(db, team_key=RED_TEAM, event_key=EVENT_KEY)
        self.assertFalse(summary["available"])
        self.assertEqual(summary["sample_matches"], 0)

    def test_log_injection_control_chars_stripped(self):
        body = _session_body("sess\n1\r2")
        resp = self.client.post("/tracks/on-device-session", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        # control chars removed from the persisted dedup key
        self.assertNotIn("\n", resp.json()["session_key"])
        self.assertNotIn("\r", resp.json()["session_key"])


if __name__ == "__main__":
    unittest.main()
