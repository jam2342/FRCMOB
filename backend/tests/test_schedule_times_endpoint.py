from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes_matches import router as matches_router
from app.core.config import settings
from app.db import models
from app.db.base import Base
from app.db.session import get_db

EVENT = "2026txhou"


class ScheduleTimesEndpointTests(unittest.TestCase):
    # The response model once dropped predicted/actual times the handler had set,
    # so this goes through the real route and its response_model.
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False)
        Base.metadata.create_all(engine)
        with self.SessionLocal() as db:
            db.add(models.Event(event_key=EVENT, name="Houston", year=2026))
            db.add(models.Match(
                match_key=f"{EVENT}_qm1", event_key=EVENT, comp_level="qm", set_number=1, match_number=1,
                time=1_700_000_000, predicted_time=1_700_000_900, actual_time=1_700_001_000,
            ))
            db.commit()
        app = FastAPI()
        app.include_router(matches_router)

        def _db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _db
        self.client = TestClient(app)
        patcher = mock.patch.object(settings, "tba_auth_key", "")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_schedule_returns_predicted_and_actual_times(self):
        resp = self.client.get(f"/matches/event/{EVENT}/schedule", params={"includeLiveResults": "false", "includeTeamNicknames": "false"})
        self.assertEqual(resp.status_code, 200, resp.text)
        row = resp.json()["matches"][0]
        self.assertEqual(row["scheduled_time"], 1_700_000_000)
        self.assertEqual(row["predicted_time"], 1_700_000_900)
        self.assertEqual(row["actual_time"], 1_700_001_000)


class ScheduleEventKeyTests(ScheduleTimesEndpointTests):
    def test_two_character_event_code_is_accepted(self):
        with self.SessionLocal() as db:
            db.add(models.Event(event_key="2026bc", name="BattleCry", year=2026))
            db.add(models.Match(match_key="2026bc_qm1", event_key="2026bc", comp_level="qm", set_number=1, match_number=1, time=1))
            db.commit()
        resp = self.client.get("/matches/event/2026bc/schedule", params={"includeLiveResults": "false", "includeTeamNicknames": "false"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["count"], 1)

    def test_unpublished_schedule_is_empty_not_a_gateway_error(self):
        with self.SessionLocal() as db:
            db.add(models.Event(event_key="2026bvci", name="2026BVCI", year=2026))
            db.commit()
        with mock.patch.object(settings, "public_readonly_mode", False):
            resp = self.client.get("/matches/event/2026bvci/schedule", params={"includeLiveResults": "false"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["count"], 0)
        self.assertFalse(resp.json()["published"])


class SchedulePaginationTests(ScheduleTimesEndpointTests):
    def test_a_page_has_the_right_matches_and_their_teams(self):
        with self.SessionLocal() as db:
            for number in range(2, 6):
                db.add(models.Match(
                    match_key=f"{EVENT}_qm{number}", event_key=EVENT, comp_level="qm",
                    set_number=1, match_number=number, time=1_700_000_000 + number,
                ))
                team_key = f"frc{number}"
                db.add(models.Team(team_key=team_key, team_number=number, nickname=f"Team {number}"))
                db.add(models.MatchTeam(
                    match_key=f"{EVENT}_qm{number}", team_key=team_key, event_key=EVENT, alliance="red", station="r1",
                ))
            db.commit()
        resp = self.client.get(
            f"/matches/event/{EVENT}/schedule",
            params={"includeLiveResults": "false", "limit": 2, "offset": 2},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total_count"], 5)
        self.assertEqual([row["match_key"] for row in body["matches"]], [f"{EVENT}_qm3", f"{EVENT}_qm4"])
        self.assertEqual([row["red"][0]["team_key"] for row in body["matches"]], ["frc3", "frc4"])
