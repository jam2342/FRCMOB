from __future__ import annotations

import unittest
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
        "payload": {"points_by_team": points},
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
            # dedup key is namespaced by caller identity ("a" = admin-open in dev)
            self.assertEqual(ctx.params_hash, "a|sess-1")
            self.assertTrue(body["session_key"].startswith("a|"))

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

    def test_unknown_team_skipped_others_persist(self):
        resp = self.client.post(
            "/tracks/on-device-session", json=_session_body("sess-2", include_unknown=True)
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["skipped_unknown_teams"], ["frc9999"])
        self.assertEqual(body["points_persisted"], 3)  # the two valid teams still land
        self.assertEqual(self._count_tracks(), 3)

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

    def test_on_device_tracks_excluded_from_heatmap_by_default(self):
        # On-device tracks are unauthenticated/best-effort: never authoritative by default.
        self.client.post("/tracks/on-device-session", json=_session_body("sess-1"))
        resp = self.client.get(f"/tracks/heatmap/{RED_TEAM}", params={"event_key": EVENT_KEY})
        self.assertEqual(resp.status_code, 200, resp.text)
        hm = resp.json()
        self.assertEqual(hm["total_points"], 0)
        self.assertEqual(hm["match_count"], 0)
        self.assertFalse(hm["include_on_device"])

    def test_on_device_tracks_included_when_opted_in(self):
        self.client.post("/tracks/on-device-session", json=_session_body("sess-1"))
        resp = self.client.get(
            f"/tracks/heatmap/{RED_TEAM}",
            params={"event_key": EVENT_KEY, "include_on_device": "true"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        hm = resp.json()
        self.assertEqual(hm["total_points"], 2)
        self.assertEqual(hm["match_count"], 1)
        self.assertTrue(hm["include_on_device"])

    def test_on_device_tracks_excluded_from_match_replayer_by_default(self):
        self.client.post("/tracks/on-device-session", json=_session_body("sess-1"))
        default = self.client.get(f"/tracks/match/{MATCH_KEY}").json()
        self.assertEqual(len(default["tracks"]), 0)
        opted = self.client.get(
            f"/tracks/match/{MATCH_KEY}", params={"include_on_device": "true"}
        ).json()
        self.assertEqual(len(opted["tracks"]), 3)

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
            self.assertIsNone(rows[0].zone_key)  # arbitrary zone rejected
            self.assertEqual(rows[1].zone_key, "red_alliance_scoring_zone")  # valid one kept

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
        self.assertTrue(resp.json()["session_key"].startswith("r:alice|"))

    def test_on_device_run_excluded_from_authoritative_shift_play(self):
        # The Team Center "Attack vs Defense" view must never select an
        # unauthenticated on-device run as the match's authoritative analysis.
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
