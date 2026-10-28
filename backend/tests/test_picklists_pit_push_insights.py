# Endpoint tests for picklists, pit scouting, push subscriptions, and
# scouting insights (coverage / raw export).

from __future__ import annotations

import base64
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

from app.core.security import WORKSPACE_ACCESS_HEADER
from app.api import routes_pit_scouting
from app.api.routes_picklists import router as picklists_router
from app.api.routes_pit_scouting import router as pit_scouting_router
from app.api.routes_push import router as push_router
from app.api.routes_scouting_insights import router as insights_router
from app.db import models
from app.db.base import Base
from app.db.session import get_db
from tests.workspace_helpers import seed_workspace
from tests.image_helpers import jpeg_bytes

PUSH_TEST_KEYS = {
    # Standard P-256 generator, a public test point, with a 16-byte auth secret.
    "p256dh": base64.urlsafe_b64encode(bytes.fromhex(
        "04"
        "6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296"
        "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5"
    )).decode().rstrip("="),
    "auth": base64.urlsafe_b64encode(b"0123456789abcdef").decode().rstrip("="),
}

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
        with self.SessionLocal() as db:
            workspace, _member, self.workspace_headers = seed_workspace(db)
            self.workspace_id = workspace.id

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
        self.app = app
        self.client = TestClient(app, headers=self.workspace_headers)

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
        jpeg = jpeg_bytes()
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
        # Display links are signed; the stored path stays the identifier.
        urls = response.json()["entry"]["photo_urls"]
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith(photo + "?exp=") and "&sig=" in urls[0])

        response = self.client.post(
            "/pit-scouting/photo/delete",
            json={"event_key": "2026test", "team_key": "frc254", "photo_path": photo},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["entry"]["photos"], [])
        self.assertFalse(stored.exists())

    def _upload(self, data: bytes) -> Path:
        response = self.client.post(
            "/pit-scouting/photo",
            json={"event_key": "2026test", "team_key": "frc254", "image_base64": base64.b64encode(data).decode()},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return Path(self._tmpdir.name) / "2026test" / "frc254" / Path(response.json()["photo"]).name

    def test_photo_location_metadata_is_dropped(self):
        import io

        from PIL import Image

        exif = Image.Exif()
        exif[0x0110] = "Pixel 9"  # camera model
        exif[0x8825] = {1: "N", 2: (29.0, 45.0, 0.0)}  # GPS latitude
        output = io.BytesIO()
        Image.new("RGB", (4, 2), (30, 60, 90)).save(output, format="JPEG", exif=exif)
        with Image.open(self._upload(output.getvalue())) as stored:
            self.assertEqual(len(stored.getexif()), 0)
            self.assertEqual(stored.size, (4, 2))

    def test_photo_without_metadata_is_stored_unchanged(self):
        plain = jpeg_bytes()
        self.assertEqual(self._upload(plain).read_bytes(), plain)

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

    def setUp(self):
        super().setUp()
        from app.services.push import subscriptions

        self.registration_count = 0

        def consume(*_args):
            self.registration_count += 1
            return self.registration_count

        self.registration_cache = mock.Mock(redis_client=mock.Mock(eval=consume))
        self.cache_patch = mock.patch.object(subscriptions, "get_cache", return_value=self.registration_cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        with self.SessionLocal() as db:
            db.add(models.Event(event_key="2026test", name="Test Event", year=2026))
            db.commit()

    def _subscribe(self, endpoint="one", *, anonymous=False, **extra):
        # The client sends workspace credentials by default; an empty header is a fan.
        headers = {WORKSPACE_ACCESS_HEADER: ""} if anonymous else None
        return self.client.post("/push/subscribe", headers=headers, json={
            "endpoint": f"https://fcm.googleapis.com/fcm/send/{endpoint}",
            "keys": PUSH_TEST_KEYS,
            **extra,
        })

    def test_public_key_unconfigured(self):
        response = self.client.get("/push/public-key")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["configured"])

    def test_subscribe_update_unsubscribe(self):
        with self.SessionLocal() as db:
            db.add(models.ScoutingRoom(room_key="room-1", workspace_id=self.workspace_id))
            db.commit()
        payload = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
            "keys": PUSH_TEST_KEYS,
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
        # Alerts go out under the member's own name, whatever the client sent.
        self.assertEqual(sub["prefs"]["scout_profile"], "Lead")
        self.assertNotIn("member_key", sub["prefs"])

        # Re-subscribing the same endpoint updates instead of duplicating.
        response = self.client.post("/push/subscribe", json={**payload, "team_keys": ["frc1678"]})
        self.assertEqual(response.json()["subscription"]["team_keys"], ["frc1678"])

        response = self.client.post(
            "/push/unsubscribe", json={"endpoint": "https://fcm.googleapis.com/fcm/send/abc"}
        )
        self.assertEqual(response.status_code, 200)

    def test_shift_alerts_need_the_rooms_workspace(self):
        with self.SessionLocal() as db:
            db.add(models.ScoutingRoom(room_key="room-1", workspace_id=self.workspace_id))
            db.commit()
        payload = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/spy",
            "keys": PUSH_TEST_KEYS,
            "prefs": {"shift_alerts": True, "room_key": "room-1", "scout_profile": "Lead"},
        }
        outsider = TestClient(self.app)
        self.assertEqual(outsider.post("/push/subscribe", json=payload).status_code, 403)
        outsider.close()

    def test_subscribe_requires_https_endpoint(self):
        response = self.client.post(
            "/push/subscribe",
            json={"endpoint": "http://insecure", "keys": PUSH_TEST_KEYS},
        )
        self.assertEqual(response.status_code, 400)

    def test_subscribe_only_accepts_browser_push_services(self):
        # Anything else would make the server POST wherever a caller points it.
        for endpoint in (
            "https://evil.example/hook",
            "https://127.0.0.1/x",
            "https://fcm.googleapis.com:8443/x",
            "https://user:pw@fcm.googleapis.com/x",
            "https://fcm.googleapis.com.evil.example/x",
        ):
            response = self.client.post(
                "/push/subscribe", json={"endpoint": endpoint, "keys": PUSH_TEST_KEYS}
            )
            self.assertEqual(response.status_code, 400, endpoint)
        for endpoint in (
            "https://fcm.googleapis.com/fcm/send/x",
            "https://updates.push.services.mozilla.com/wpush/v2/x",
            "https://web.push.apple.com/x",
            "https://db5p.notify.windows.com/w/?token=x",
        ):
            response = self.client.post(
                "/push/subscribe", json={"endpoint": endpoint, "keys": PUSH_TEST_KEYS}
            )
            self.assertEqual(response.status_code, 200, endpoint)

    def test_sender_skips_stored_foreign_endpoints_and_sets_a_timeout(self):
        from app.services.push import sender

        with self.SessionLocal() as db:
            foreign = models.PushSubscription(
                endpoint="https://evil.example/hook", keys=PUSH_TEST_KEYS, team_keys=[], prefs={}, enabled=True, notified={}
            )
            real = models.PushSubscription(
                endpoint="https://fcm.googleapis.com/fcm/send/ok", keys=PUSH_TEST_KEYS, team_keys=[], prefs={}, enabled=True, notified={}
            )
            db.add_all([foreign, real])
            db.commit()
            with mock.patch.object(sender, "push_configured", return_value=True), \
                    mock.patch.object(sender, "webpush", create=True) as webpush:
                self.assertFalse(sender.send_web_push(db, foreign, {"title": "x"}))
                self.assertTrue(sender.send_web_push(db, real, {"title": "x"}))
            db.commit()
            self.assertEqual(webpush.call_count, 1)
            self.assertEqual(webpush.call_args.kwargs["timeout"], sender.PUSH_SEND_TIMEOUT_SEC)
            self.assertIsNone(db.query(models.PushSubscription).filter_by(endpoint="https://evil.example/hook").one_or_none())

    def test_test_send_is_rate_limited(self):
        from app.api import routes_push

        payload = {"endpoint": "https://fcm.googleapis.com/fcm/send/t", "keys": PUSH_TEST_KEYS}
        self.assertEqual(self.client.post("/push/subscribe", json=payload).status_code, 200)
        counts: dict[str, int] = {}

        def incr(key):  # noqa: ANN001
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        fake_redis = mock.Mock(incr=incr)
        with mock.patch.object(routes_push, "push_configured", return_value=True), \
                mock.patch.object(routes_push, "send_web_push", return_value=True), \
                mock.patch.object(routes_push, "get_cache", return_value=mock.Mock(redis_client=fake_redis)):
            codes = [
                self.client.post("/push/test", json={"endpoint": payload["endpoint"]}).status_code
                for _ in range(routes_push.TEST_SENDS_PER_WINDOW + 1)
            ]
        self.assertEqual(codes, [200] * routes_push.TEST_SENDS_PER_WINDOW + [429])

    def test_fan_subscription_remains_anonymous(self):
        with TestClient(self.app) as fan:
            response = fan.post("/push/subscribe", json={
                "endpoint": "https://fcm.googleapis.com/fcm/send/fan",
                "keys": PUSH_TEST_KEYS, "event_key": "2026test", "team_keys": ["frc254"],
            })
        self.assertEqual(response.status_code, 200, response.text)

    def test_rejects_malformed_encryption_material_before_admission(self):
        def encoded(raw):
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")
        invalid_keys = [
            {**PUSH_TEST_KEYS, "p256dh": "k"},
            {**PUSH_TEST_KEYS, "p256dh": "!" * 87},
            {**PUSH_TEST_KEYS, "p256dh": "A" * 10000},
            {**PUSH_TEST_KEYS, "p256dh": encoded(b"\x04" + b"\0" * 64)},  # off curve
            {**PUSH_TEST_KEYS, "p256dh": encoded(b"\x03" + b"\0" * 64)},  # wrong format
            {**PUSH_TEST_KEYS, "auth": encoded(b"short")},
            {**PUSH_TEST_KEYS, "auth": "A" * 10000},
            {**PUSH_TEST_KEYS, "auth": PUSH_TEST_KEYS["auth"] + "!"},
        ]
        for keys in invalid_keys:
            self.assertEqual(self._subscribe(keys=keys).status_code, 400)
        self.assertEqual(self.registration_count, 0)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.PushSubscription).count(), 0)

    def test_creation_budget_survives_unsubscribe_and_preserves_updates(self):
        from app.core.config import settings

        with mock.patch.object(settings, "push_subscriptions_max_new_per_hour", 1):
            self.assertEqual(self._subscribe(anonymous=True).status_code, 200)
            self.assertEqual(self._subscribe(anonymous=True, team_keys=["frc254"]).status_code, 200)
            self.assertEqual(self.registration_count, 1)
            self.assertEqual(self._subscribe("two", anonymous=True).status_code, 429)
            self.client.post("/push/unsubscribe", json={"endpoint": "https://fcm.googleapis.com/fcm/send/one"})
            self.assertEqual(self._subscribe(anonymous=True).status_code, 429)

    def test_total_cap_preserves_existing_subscription_updates(self):
        from app.core.config import settings

        with mock.patch.object(settings, "push_subscriptions_max_total", 1):
            self.assertEqual(self._subscribe(anonymous=True).status_code, 200)
            self.assertEqual(self._subscribe("two", anonymous=True).status_code, 429)
            self.assertEqual(self._subscribe(anonymous=True, team_keys=["frc118"]).status_code, 200)
        self.assertEqual(self.registration_count, 1)

    def test_anonymous_flood_cannot_lock_a_team_out_of_alerts(self):
        from app.core.config import settings

        with mock.patch.object(settings, "push_subscriptions_max_total", 1), \
                mock.patch.object(settings, "push_subscriptions_max_new_per_hour", 1):
            self.assertEqual(self._subscribe("fan", anonymous=True).status_code, 200)
            self.assertEqual(self._subscribe("fan-two", anonymous=True).status_code, 429)
            # A workspace member still gets in: own hourly budget, room past the anonymous total.
            self.assertEqual(self._subscribe("member").status_code, 200)
            # The ceiling still holds for everyone.
            self.assertEqual(self._subscribe("member-two").status_code, 429)

    def test_unknown_event_is_unavailable_without_admission(self):
        response = self._subscribe(event_key="2026unknown")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.registration_count, 0)

    def test_production_new_registration_fails_closed_when_redis_is_down(self):
        from app.core.config import settings

        self.assertEqual(self._subscribe().status_code, 200)
        self.registration_cache.redis_client = None
        with mock.patch.object(settings, "app_env", "production"):
            self.assertEqual(self._subscribe("two").status_code, 503)
            self.assertEqual(self._subscribe(team_keys=["frc118"]).status_code, 200)
        self.registration_cache.redis_client = mock.Mock(eval=mock.Mock(side_effect=RuntimeError("offline")))
        with mock.patch.object(settings, "app_env", "production"):
            self.assertEqual(self._subscribe("two").status_code, 503)

    def test_expired_subscriptions_are_purged_before_capacity_check(self):
        from app.core.config import settings

        self.assertEqual(self._subscribe("expired").status_code, 200)
        with self.SessionLocal() as db:
            row = db.query(models.PushSubscription).one()
            row.updated_at = datetime.now(timezone.utc) - timedelta(days=181)
            db.commit()
        with mock.patch.object(settings, "push_subscriptions_max_total", 1):
            self.assertEqual(self._subscribe("fresh").status_code, 200)
        with self.SessionLocal() as db:
            self.assertEqual([row.endpoint for row in db.query(models.PushSubscription).all()], [
                "https://fcm.googleapis.com/fcm/send/fresh",
            ])

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
                models.ScoutingRoom(room_key="room-a", workspace_id=self.workspace_id, event_key="2026test", title="Room A")
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

    def test_team_notes_lists_what_scouts_wrote_and_tagged(self):
        self._seed()
        with self.SessionLocal() as db:
            db.add(models.Match(match_key="2026test_qm2", event_key="2026test", comp_level="qm", set_number=1, match_number=2))
            db.add(models.ScoutingRoomEntry(
                room_key="room-a", event_key="2026test", match_key="2026test_qm1", team_key="frc2",
                scout_profile="Scout One",
                payload={"notes": "  Pinned hard for 40s  ", "tags": ["defense", "DEFENSE", "made_up", 7], "saved_at_ms": 1000},
            ))
            db.add(models.ScoutingRoomEntry(
                room_key="room-a", event_key="2026test", match_key="2026test_qm2", team_key="frc2",
                scout_profile="Scout Two",
                payload={"notes": "", "tags": ["broke_down", "defense"], "saved_at_ms": 2000},
            ))
            # Neither notes nor tags: counted as a report, not listed.
            db.add(models.ScoutingRoomEntry(
                room_key="room-a", event_key="2026test", match_key="2026test_qm2", team_key="frc2",
                scout_profile="Scout Three", payload={"notes": "   "},
            ))
            # Another workspace's room never shows.
            other = models.TeamWorkspace(name="Other", join_code_hash="other-hash")
            db.add(other)
            db.flush()
            db.add(models.ScoutingRoom(room_key="room-b", workspace_id=other.id, event_key="2026test"))
            db.add(models.ScoutingRoomEntry(
                room_key="room-b", event_key="2026test", match_key="2026test_qm1", team_key="frc2",
                scout_profile="Spy", payload={"notes": "secret", "tags": ["tipped"]},
            ))
            db.commit()

        response = self.client.get("/scouting/insights/team-notes", params={"team_key": "frc2", "event_key": "2026test"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["reports"], 3)
        self.assertEqual([(n["scout_profile"], n["notes"], n["tags"]) for n in body["notes"]], [
            ("Scout Two", "", ["broke_down", "defense"]),
            ("Scout One", "Pinned hard for 40s", ["defense"]),
        ])
        self.assertEqual(body["tag_counts"], [{"tag": "defense", "count": 2}, {"tag": "broke_down", "count": 1}])

        season = self.client.get("/scouting/insights/team-notes", params={"team_key": "frc2"}).json()
        self.assertEqual(len(season["notes"]), 2)
        self.assertIsNone(season["event_key"])

    def test_team_notes_rejects_a_bad_team_key(self):
        response = self.client.get("/scouting/insights/team-notes", params={"team_key": "254; drop"})
        self.assertEqual(response.status_code, 422)

if __name__ == "__main__":
    unittest.main()
