# Endpoint tests for pit entries: saving answers and the first-save race.

from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_pit_scouting
from app.api.routes_pit_scouting import router as pit_scouting_router
from app.db import models
from app.db.base import Base
from app.db.session import get_db
from tests.workspace_helpers import add_seeded_member, headers_for, seed_workspace

EVENT = "2026arc"


class PitEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(self.engine)
        with self.SessionLocal() as db:
            workspace, leader, self.lead_h = seed_workspace(db, display_name="Lead")
            alex = add_seeded_member(db, workspace, display_name="Alex")
            db.commit()
            self.workspace_id = workspace.id
            self.alex_id = alex.id
            self.alex_h = headers_for(alex)

        app = FastAPI()
        app.include_router(pit_scouting_router)

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

    def _rows(self) -> list[models.PitScoutingEntry]:
        with self.SessionLocal() as db:
            return list(db.execute(select(models.PitScoutingEntry)).scalars())

    def test_first_save_that_loses_the_insert_race_updates_the_winner(self):
        with self.SessionLocal() as db:
            db.add(models.PitScoutingEntry(workspace_id=self.workspace_id, event_key=EVENT, team_key="frc254", payload={}, photos=["/media/pit_photos/x.jpg"]))
            db.commit()
        real = routes_pit_scouting._load_entry
        calls = {"n": 0}

        def blind_first(*args, **kwargs):
            # The first lookup sees no row, as if a queued photo's insert landed right after it.
            calls["n"] += 1
            return None if calls["n"] == 1 else real(*args, **kwargs)

        with mock.patch.object(routes_pit_scouting, "_load_entry", blind_first):
            response = self.client.post("/pit-scouting", json={"event_key": EVENT, "team_key": "frc254", "payload": {"drivetrain": "swerve"}}, headers=self.alex_h)
        self.assertEqual(response.status_code, 200, response.text)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].payload, {"drivetrain": "swerve"})
        self.assertEqual(rows[0].photos, ["/media/pit_photos/x.jpg"])

    def test_a_persistent_insert_conflict_is_a_409_not_a_500(self):
        with self.SessionLocal() as db:
            db.add(models.PitScoutingEntry(workspace_id=self.workspace_id, event_key=EVENT, team_key="frc254", payload={}, photos=[]))
            db.commit()
        with mock.patch.object(routes_pit_scouting, "_load_entry", lambda *a, **k: None):
            response = self.client.post("/pit-scouting", json={"event_key": EVENT, "team_key": "frc254", "payload": {"a": 1}}, headers=self.alex_h)
        self.assertEqual(response.status_code, 409, response.text)

    def test_non_finite_numbers_are_a_400_not_a_500(self):
        for raw in ("NaN", "Infinity", "-Infinity"):
            body = '{"event_key": "%s", "team_key": "frc254", "payload": {"weight_lbs": %s}}' % (EVENT, raw)
            response = self.client.post("/pit-scouting", content=body, headers={**self.alex_h, "Content-Type": "application/json"})
            self.assertEqual(response.status_code, 400, f"{raw}: {response.text}")
            self.assertIn("weight_lbs", response.json()["detail"])
        self.assertEqual(self._rows(), [])

    def test_auto_split_hands_out_teams_that_only_have_photos(self):
        with self.SessionLocal() as db:
            db.add(models.PitScoutingEntry(workspace_id=self.workspace_id, event_key=EVENT, team_key="frc254", payload={}, photos=["/media/pit_photos/x.jpg"]))
            db.add(models.PitScoutingEntry(workspace_id=self.workspace_id, event_key=EVENT, team_key="frc1678", payload={"a": 1}, photos=[]))
            db.commit()
        response = self.client.post(
            "/pit-scouting/assignments/auto-split",
            json={"event_key": EVENT, "team_keys": ["frc254", "frc1678"], "member_ids": [self.alex_id]},
            headers=self.lead_h,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["team_key"] for row in response.json()["assignments"]], ["frc254"])


if __name__ == "__main__":
    unittest.main()
