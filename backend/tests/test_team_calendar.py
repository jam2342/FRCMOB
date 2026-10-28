from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_matches import router
from app.db import models
from app.db.session import get_db
from app.services.events.calendar import team_calendar
from tests.conftest import DBTestCase


class TeamCalendarTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self.db.add(models.Event(event_key="2026mndu", name="Lake Superior Regional, Duluth", year=2026))
        for n in range(1, 8):
            self.db.add(models.Team(team_key=f"frc{n}", team_number=n))
        lineups = [
            ("2026mndu_qm2", "qm", 1, 2, 2_000_000_000, 2_000_000_600, (1, 2, 3), (4, 5, 6)),
            ("2026mndu_qm1", "qm", 1, 1, 1_999_999_000, None, (4, 5, 6), (7, 1, 2)),
            ("2026mndu_qm3", "qm", 1, 3, 2_000_001_000, None, (3, 4, 5), (6, 7, 2)),  # not ours
            ("2026mndu_sf1m1", "sf", 1, 1, None, None, (1, 3, 5), (2, 4, 6)),  # no time yet
        ]
        for key, level, set_number, number, time, predicted, red, blue in lineups:
            self.db.add(models.Match(match_key=key, event_key="2026mndu", comp_level=level, set_number=set_number, match_number=number, time=time, predicted_time=predicted))
            for alliance, side in (("red", red), ("blue", blue)):
                for station, n in enumerate(side, start=1):
                    self.db.add(models.MatchTeam(match_key=key, team_key=f"frc{n}", event_key="2026mndu", alliance=alliance, station=str(station)))
        self.db.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def test_lists_only_this_teams_timed_matches_in_order(self):
        response = self.client.get("/matches/event/2026mndu/team/1/calendar.ics")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("text/calendar"))
        self.assertEqual(response.headers["cache-control"], "public, max-age=300")
        body = response.text
        self.assertTrue(body.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(body.endswith("END:VCALENDAR\r\n"))
        self.assertEqual(body.count("BEGIN:VEVENT"), 2)
        self.assertLess(body.index("UID:2026mndu_qm1-frc1@frcmob"), body.index("UID:2026mndu_qm2-frc1@frcmob"))
        self.assertNotIn("qm3", body)
        self.assertNotIn("sf1m1", body)
        # TBA's predicted time wins over the published one, and the match lasts ten minutes.
        self.assertIn("DTSTART:20330518T034320Z", body)
        self.assertIn("DTEND:20330518T035320Z", body)
        self.assertIn("SUMMARY:1 · Qual 2 · Red", body)
        self.assertIn("SUMMARY:1 · Qual 1 · Blue", body)
        # Commas are escaped in text values.
        self.assertIn("LOCATION:Lake Superior Regional\\, Duluth", body)
        unfolded = body.replace("\r\n ", "")
        self.assertIn("With 7\\, 2 vs 4\\, 5\\, 6.", unfolded)
        self.assertTrue(all(len(line.encode()) <= 75 for line in body.split("\r\n")))

    def test_unknown_event_or_bad_team(self):
        self.assertEqual(self.client.get("/matches/event/2026none/team/1/calendar.ics").status_code, 404)
        self.assertEqual(self.client.get("/matches/event/2026mndu/team/abc/calendar.ics").status_code, 404)

    def test_a_team_with_no_matches_gets_an_empty_calendar(self):
        body = team_calendar(event_key="e", event_name="E", team_key="frc9", matches=[], now=0)
        self.assertIn("X-WR-CALNAME:9 at E", body)
        self.assertNotIn("VEVENT", body)
