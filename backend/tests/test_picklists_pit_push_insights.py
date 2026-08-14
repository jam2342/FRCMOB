# Endpoint tests for picklists, pit scouting, push subscriptions, and
# scouting insights (coverage / raw export).

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_pit_scouting
from app.api.routes_picklists import router as picklists_router
from app.api.routes_pit_scouting import router as pit_scouting_router
from app.api.routes_push import router as push_router
from app.api.routes_scouting_insights import router as insights_router
from app.db import models
from app.db.base import Base
from app.db.session import get_db

class _EndpointTestCase(unittest.TestCase):
    routers = ()

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(self.engine)

        app = FastAPI()
        for router in self.routers:
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

    def tearDown(self) -> None:
        self.client.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

class PicklistEndpointTests(_EndpointTestCase):
    routers = (picklists_router,)

    def _create(self) -> dict:
        response = self.client.post(
            "/picklists",
            json={
                "event_key": "2026test",
                "title": "Smoke",
                "created_by": "Lead",
                "slots": [
                    {"team_key": "frc254"},
                    {"team_key": "frc1678", "tier": "dnp", "dnp_reason": "tippy"},
                    {"team_key": "not-a-team"},
                    {"team_key": "frc254"},  # duplicate dropped
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["picklist"]

    def test_create_normalizes_slots(self):
        picklist = self._create()
        self.assertEqual(picklist["version"], 1)
        self.assertEqual([slot["team_key"] for slot in picklist["slots"]], ["frc254", "frc1678"])
        self.assertEqual(picklist["slots"][1]["tier"], "dnp")
        self.assertEqual(picklist["slots"][1]["dnp_reason"], "tippy")

    def test_update_bumps_version_and_detects_conflicts(self):
        picklist = self._create()
        response = self.client.put(
            f"/picklists/{picklist['id']}",
            json={
                "version": 1,
                "live_mode": True,
                "slots": [
                    {"team_key": "frc254", "status": "picked", "picked_by_alliance": 1},
                ],
            },
        )
        body = response.json()
        self.assertTrue(body["ok"], response.text)
        self.assertEqual(body["picklist"]["version"], 2)
        self.assertTrue(body["picklist"]["live_mode"])
        self.assertEqual(body["picklist"]["slots"][0]["status"], "picked")

        stale = self.client.put(
            f"/picklists/{picklist['id']}",
            json={"version": 1, "title": "stale write"},
        )
        stale_body = stale.json()
        self.assertTrue(stale_body.get("conflict"))
        self.assertEqual(stale_body["picklist"]["version"], 2)

    def test_list_and_delete(self):
        picklist = self._create()
        listing = self.client.get("/picklists", params={"event_key": "2026test"}).json()
        self.assertEqual(listing["count"], 1)
        deleted = self.client.delete(f"/picklists/{picklist['id']}")
        self.assertEqual(deleted.status_code, 200)
        listing = self.client.get("/picklists", params={"event_key": "2026test"}).json()
        self.assertEqual(listing["count"], 0)

    def test_rejects_bad_event_key(self):
        response = self.client.post("/picklists", json={"event_key": "BAD KEY"})
        self.assertEqual(response.status_code, 400)

class PitScoutingEndpointTests(_EndpointTestCase):
    routers = (pit_scouting_router,)

    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._photos_patch = mock.patch.object(
            routes_pit_scouting, "PIT_PHOTOS_ROOT", Path(self._tmpdir.name)
        )
        self._photos_patch.start()

    def tearDown(self) -> None:
        self._photos_patch.stop()
        self._tmpdir.cleanup()
        super().tearDown()

    def test_upsert_and_fetch(self):
        response = self.client.post(
            "/pit-scouting",
            json={
                "event_key": "2026test",
                "team_key": "frc254",
                "scout_profile": "Pit Crew",
                "payload": {
                    "drivetrain": "Swerve",
                    "weight_lbs": 118,
                    "auto_capabilities": ["Mobility", "Scores preload"],
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        # Upsert overwrites the same (event, team) row.
        response = self.client.post(
            "/pit-scouting",
            json={
                "event_key": "2026test",
                "team_key": "frc254",
                "payload": {"drivetrain": "Tank / KOP"},
            },
        )
        self.assertEqual(response.status_code, 200)

        listing = self.client.get("/pit-scouting", params={"event_key": "2026test"}).json()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["entries"][0]["payload"]["drivetrain"], "Tank / KOP")

    def test_photo_upload_and_delete(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        response = self.client.post(
            "/pit-scouting/photo",
            json={
                "event_key": "2026test",
                "team_key": "frc254",
                "image_base64": base64.b64encode(jpeg).decode(),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        photo = response.json()["photo"]
        self.assertTrue(photo.startswith("/media/pit_photos/2026test/frc254/"))
        stored = Path(self._tmpdir.name) / "2026test" / "frc254" / Path(photo).name
        self.assertTrue(stored.is_file())

        response = self.client.post(
            "/pit-scouting/photo/delete",
            json={"event_key": "2026test", "team_key": "frc254", "photo_path": photo},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["entry"]["photos"], [])
        self.assertFalse(stored.exists())

    def test_photo_rejects_non_image(self):
        response = self.client.post(
            "/pit-scouting/photo",
            json={
                "event_key": "2026test",
                "team_key": "frc254",
                "image_base64": base64.b64encode(b"plain text").decode(),
            },
        )
        self.assertEqual(response.status_code, 400)

class PushEndpointTests(_EndpointTestCase):
    routers = (push_router,)

    def test_public_key_unconfigured(self):
        response = self.client.get("/push/public-key")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["configured"])

    def test_subscribe_update_unsubscribe(self):
        payload = {
            "endpoint": "https://fcm.example/abc",
            "keys": {"p256dh": "k", "auth": "a"},
            "event_key": "2026test",
            "team_keys": ["frc254", "frc254", "bogus"],
            "prefs": {"match_lead_minutes": 200, "shift_alerts": True, "room_key": "ROOM-1"},
        }
        response = self.client.post("/push/subscribe", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        sub = response.json()["subscription"]
        self.assertEqual(sub["team_keys"], ["frc254"])
        self.assertEqual(sub["prefs"]["match_lead_minutes"], 60)  # clamped
        self.assertEqual(sub["prefs"]["room_key"], "room-1")

        # Re-subscribing the same endpoint updates instead of duplicating.
        response = self.client.post("/push/subscribe", json={**payload, "team_keys": ["frc1678"]})
        self.assertEqual(response.json()["subscription"]["team_keys"], ["frc1678"])

        response = self.client.post(
            "/push/unsubscribe", json={"endpoint": "https://fcm.example/abc"}
        )
        self.assertEqual(response.status_code, 200)

    def test_subscribe_requires_https_endpoint(self):
        response = self.client.post(
            "/push/subscribe",
            json={"endpoint": "http://insecure", "keys": {"p256dh": "k", "auth": "a"}},
        )
        self.assertEqual(response.status_code, 400)

class ScoutingInsightsEndpointTests(_EndpointTestCase):
    routers = (insights_router,)

    def _seed(self) -> None:
        with self.SessionLocal() as db:
            db.add(models.Event(event_key="2026test", name="Test Event", year=2026))
            for team in ("frc1", "frc2", "frc3", "frc4", "frc5", "frc6"):
                db.add(models.Team(team_key=team, team_number=int(team[3:]), nickname=team))
            db.add(
                models.Match(
                    match_key="2026test_qm1",
                    event_key="2026test",
                    comp_level="qm",
                    set_number=1,
                    match_number=1,
                    time=None,
                )
            )
            stations = [
                ("frc1", "red", "1"), ("frc2", "red", "2"), ("frc3", "red", "3"),
                ("frc4", "blue", "1"), ("frc5", "blue", "2"), ("frc6", "blue", "3"),
            ]
            for team, alliance, station in stations:
                db.add(
                    models.MatchTeam(
                        match_key="2026test_qm1",
                        team_key=team,
                        event_key="2026test",
                        alliance=alliance,
                        station=station,
                    )
                )
            db.add(
                models.ScoutingRoom(room_key="room-a", event_key="2026test", title="Room A")
            )
            db.add(
                models.ScoutingRoomEntry(
                    room_key="room-a",
                    event_key="2026test",
                    match_key="2026test_qm1",
                    team_key="frc1",
                    scout_profile="Scout One",
                    payload={"form": {"auto_scored": 6, "teleop_scored": 12}},
                )
            )
            db.commit()

    def test_coverage_grid_leaderboard(self):
        self._seed()
        response = self.client.get(
            "/scouting/insights/coverage", params={"event_key": "2026test"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["summary"]["total_slots"], 6)
        self.assertEqual(body["summary"]["covered_slots"], 1)
        self.assertEqual(len(body["grid"]), 1)
        self.assertEqual(body["leaderboard"][0]["scout_profile"], "Scout One")
        self.assertEqual(body["leaderboard"][0]["entry_count"], 1)
        self.assertEqual(body["leaderboard"][0]["best_qual_streak"], 1)

    def test_entries_export(self):
        self._seed()
        response = self.client.get(
            "/scouting/insights/entries-export", params={"event_key": "2026test"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["count"], 1)
        entry = body["entries"][0]
        self.assertEqual(entry["team_key"], "frc1")
        self.assertEqual(entry["entry"]["form"]["teleop_scored"], 12)

if __name__ == "__main__":
    unittest.main()
