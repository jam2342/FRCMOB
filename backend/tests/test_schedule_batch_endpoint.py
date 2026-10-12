from __future__ import annotations

from unittest import mock

from app.api import routes_matches
from tests.test_schedule_times_endpoint import EVENT, ScheduleTimesEndpointTests

from app.db import models


class ScheduleBatchEndpointTests(ScheduleTimesEndpointTests):
    def setUp(self) -> None:
        super().setUp()
        with self.SessionLocal() as db:
            db.add(models.Event(event_key="2026bc", name="BattleCry", year=2026))
            db.add(models.Match(match_key="2026bc_qm1", event_key="2026bc", comp_level="qm", set_number=1, match_number=1, time=5))
            db.commit()
        patcher = mock.patch("app.db.session.SessionLocal", self.SessionLocal)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_batch_returns_exactly_what_each_single_request_returns(self):
        resp = self.client.get("/matches/schedules", params={"event_keys": f"{EVENT}, 2026BC,{EVENT}"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(list(body["schedules"]), [EVENT, "2026bc"])
        for key in (EVENT, "2026bc"):
            single = self.client.get(f"/matches/event/{key}/schedule").json()
            self.assertEqual(body["schedules"][key], single)
        self.assertIn("max-age", resp.headers["cache-control"])

    def test_one_failing_event_does_not_fail_the_batch(self):
        real = routes_matches.get_event_schedule

        def flaky(event_key, *args, **kwargs):  # noqa: ANN001
            if event_key == "2026bc":
                raise RuntimeError("boom")
            return real(event_key, *args, **kwargs)

        with mock.patch.object(routes_matches, "get_event_schedule", side_effect=flaky):
            resp = self.client.get("/matches/schedules", params={"event_keys": f"{EVENT},2026bc"})
        self.assertEqual(resp.status_code, 200, resp.text)
        schedules = resp.json()["schedules"]
        self.assertTrue(schedules[EVENT]["ok"])
        self.assertEqual(schedules["2026bc"], {"ok": False, "status": 502, "detail": "This schedule couldn't be loaded right now."})

    def test_limits_and_bad_keys_are_refused(self):
        too_many = ",".join(f"2026e{i}" for i in range(routes_matches.SCHEDULE_BATCH_MAX_EVENTS + 1))
        self.assertEqual(self.client.get("/matches/schedules", params={"event_keys": too_many}).status_code, 400)
        self.assertEqual(self.client.get("/matches/schedules", params={"event_keys": "../etc"}).status_code, 400)
        self.assertEqual(self.client.get("/matches/schedules", params={"event_keys": " , "}).status_code, 400)
