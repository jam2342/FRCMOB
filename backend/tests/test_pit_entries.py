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

    def _save(self, headers, payload=None, changes=None, team="frc254"):
        body = {"event_key": EVENT, "team_key": team, "payload": payload or {}}
        if changes is not None:
            body["changes"] = changes
        response = self.client.post("/pit-scouting", json=body, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["entry"]

    def test_two_scouts_changing_different_answers_both_keep_theirs(self):
        self._save(self.lead_h, changes={"drivetrain": {"to": "swerve"}})
        # Alex opened the team before that save, saw nothing, and answered weight only.
        entry = self._save(self.alex_h, payload={"weight_lbs": 112}, changes={"weight_lbs": {"to": 112}})
        self.assertEqual(entry["payload"], {"drivetrain": "swerve", "weight_lbs": 112})
        self.assertEqual(entry["conflicts"], [])

    def test_the_same_answer_changed_differently_keeps_the_other_one_as_a_conflict(self):
        self._save(self.lead_h, changes={"drivetrain": {"to": "swerve"}})
        entry = self._save(self.alex_h, changes={"drivetrain": {"to": "tank"}})
        self.assertEqual(entry["payload"], {"drivetrain": "tank"})
        [conflict] = entry["conflicts"]
        self.assertEqual({k: conflict[k] for k in ("field", "kept", "other", "other_by")}, {"field": "drivetrain", "kept": "tank", "other": "swerve", "other_by": "Lead"})
        # Picking one (here keeping Alex's) settles it.
        entry = self._save(self.lead_h, changes={"drivetrain": {"from": "tank", "to": "tank"}})
        self.assertEqual(entry["conflicts"], [])
        self.assertEqual(entry["payload"], {"drivetrain": "tank"})

    def test_agreeing_or_unchanged_answers_are_not_conflicts(self):
        self._save(self.lead_h, changes={"drivetrain": {"to": "swerve"}, "weight_lbs": {"to": None}})
        # Same value from both, and a blank number vs no answer, are the same answer.
        entry = self._save(self.alex_h, changes={"drivetrain": {"to": "swerve"}, "weight_lbs": {"from": None, "to": 90}})
        self.assertEqual(entry["conflicts"], [])
        self.assertEqual(entry["payload"], {"drivetrain": "swerve", "weight_lbs": 90})

    def test_a_save_queued_offline_replays_as_a_merge(self):
        # Alex opened the team when only drivetrain was known, went offline, changed notes.
        self._save(self.lead_h, changes={"drivetrain": {"to": "swerve"}})
        queued = {"notes": {"to": "climbs L2"}}
        # Meanwhile the lead adds weight and changes the drivetrain.
        self._save(self.lead_h, changes={"weight_lbs": {"to": 120}, "drivetrain": {"from": "swerve", "to": "tank"}})
        entry = self._save(self.alex_h, payload={"drivetrain": "swerve", "notes": "climbs L2"}, changes=queued)
        self.assertEqual(entry["payload"], {"drivetrain": "tank", "weight_lbs": 120, "notes": "climbs L2"})
        self.assertEqual(entry["conflicts"], [])

    def test_clearing_an_answer_removes_it_and_an_older_app_still_replaces(self):
        self._save(self.lead_h, changes={"drivetrain": {"to": "swerve"}, "notes": {"to": "x"}})
        entry = self._save(self.alex_h, changes={"notes": {"from": "x"}})
        self.assertEqual(entry["payload"], {"drivetrain": "swerve"})
        self._save(self.lead_h, changes={"drivetrain": {"to": "tank"}})
        entry = self._save(self.alex_h, changes={"drivetrain": {"from": "swerve", "to": "mecanum"}})
        self.assertEqual(len(entry["conflicts"]), 1)
        # No "changes": the whole payload replaces, conflicts go with it.
        entry = self._save(self.alex_h, payload={"weight_lbs": 1})
        self.assertEqual(entry["payload"], {"weight_lbs": 1})
        self.assertEqual(entry["conflicts"], [])

    def test_bad_values_in_changes_are_rejected_like_the_payload(self):
        body = '{"event_key": "%s", "team_key": "frc254", "changes": {"weight_lbs": {"to": NaN}}}' % EVENT
        response = self.client.post("/pit-scouting", content=body, headers={**self.alex_h, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400, response.text)
        too_many = {f"f{i}": {"to": 1} for i in range(routes_pit_scouting.MAX_PAYLOAD_FIELDS + 1)}
        response = self.client.post("/pit-scouting", json={"event_key": EVENT, "team_key": "frc254", "changes": too_many}, headers=self.alex_h)
        self.assertEqual(response.status_code, 400, response.text)

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
