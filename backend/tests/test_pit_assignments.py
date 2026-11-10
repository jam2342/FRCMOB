# Endpoint tests for pit-scouting assignments: who walks to which pit.

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes_pit_scouting import router as pit_scouting_router
from app.api.routes_workspaces import router as workspaces_router
from app.db import models
from app.db.base import Base
from app.db.session import get_db
from tests.workspace_helpers import add_seeded_member, headers_for, seed_workspace

EVENT = "2026arc"
BASE = "/pit-scouting/assignments"


class PitAssignmentTests(unittest.TestCase):
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
            sam = add_seeded_member(db, workspace, display_name="Sam")
            other_ws, other_leader, self.other_h = seed_workspace(db, name="Other", display_name="Otto")
            db.commit()
            self.workspace_id = workspace.id
            self.lead_id, self.alex_id, self.sam_id = leader.id, alex.id, sam.id
            self.other_leader_id = other_leader.id
            self.other_ws_id = other_ws.id
            self.alex_h = headers_for(alex)
            self.sam_h = headers_for(sam)

        app = FastAPI()
        app.include_router(pit_scouting_router)
        app.include_router(workspaces_router)

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

    def _post(self, path: str, headers: dict, **body):
        return self.client.post(f"{BASE}{path}", json={"event_key": EVENT, **body}, headers=headers)

    def _list(self, headers: dict, event: str = EVENT) -> list[dict]:
        response = self.client.get(BASE, params={"event_key": event}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        return body["assignments"]

    def _by_team(self, headers: dict) -> dict[str, int | None]:
        return {row["team_key"]: row["member_id"] for row in self._list(headers)}

    def test_list_starts_empty_and_needs_a_workspace(self):
        self.assertEqual(self._list(self.alex_h), [])
        self.assertEqual(self.client.get(BASE, params={"event_key": EVENT}).status_code, 401)

    def test_claim_lists_with_name_and_is_idempotent(self):
        response = self._post("/claim", self.alex_h, team_key="frc254")
        self.assertEqual(response.status_code, 200, response.text)
        assignment = response.json()["assignment"]
        self.assertEqual(assignment["member_id"], self.alex_id)
        self.assertEqual(assignment["member_name"], "Alex")
        self.assertEqual(assignment["source"], "claim")
        self.assertEqual(assignment["assigned_by_member_id"], self.alex_id)
        self.assertTrue(assignment["updated_at"])
        again = self._post("/claim", self.alex_h, team_key="FRC254")
        self.assertEqual(again.status_code, 200)
        rows = self._list(self.sam_h)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"team_key", "member_id", "member_name", "source", "assigned_by_member_id", "updated_at"})

    def test_claim_conflict_names_the_holder(self):
        self._post("/claim", self.alex_h, team_key="frc254")
        response = self._post("/claim", self.sam_h, team_key="frc254")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Alex", response.json()["detail"])
        self.assertEqual(self._by_team(self.sam_h), {"frc254": self.alex_id})

    def test_leader_can_force_a_claim_but_members_cannot(self):
        self._post("/claim", self.alex_h, team_key="frc254")
        self.assertEqual(self._post("/claim", self.sam_h, team_key="frc254", force=True).status_code, 403)
        response = self._post("/claim", self.lead_h, team_key="frc254", force=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._by_team(self.sam_h), {"frc254": self.lead_id})

    def test_claim_rejects_bad_keys(self):
        self.assertEqual(self._post("/claim", self.alex_h, team_key="nope").status_code, 400)
        response = self.client.post(f"{BASE}/claim", json={"event_key": "BAD KEY", "team_key": "frc1"}, headers=self.alex_h)
        self.assertEqual(response.status_code, 400)

    def test_release_rules(self):
        self._post("/claim", self.alex_h, team_key="frc254")
        self._post("/claim", self.alex_h, team_key="frc1678")
        self.assertEqual(self._post("/release", self.sam_h, team_key="frc254").status_code, 403)
        self.assertEqual(self._post("/release", self.alex_h, team_key="frc254").status_code, 200)
        self.assertEqual(self._by_team(self.sam_h), {"frc1678": self.alex_id})
        self.assertEqual(self._post("/release", self.lead_h, team_key="frc1678").status_code, 200)
        self.assertEqual(self._list(self.sam_h), [])
        # Releasing a free team is fine.
        again = self._post("/release", self.sam_h, team_key="frc1678")
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["ok"])

    def test_set_is_leader_only_and_validates_member(self):
        self.assertEqual(self._post("/set", self.alex_h, team_key="frc254", member_id=self.alex_id).status_code, 403)
        response = self._post("/set", self.lead_h, team_key="frc254", member_id=self.sam_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["assignment"]["source"], "leader")
        self.assertEqual(response.json()["assignment"]["assigned_by_member_id"], self.lead_id)
        # Reassigning overwrites; a foreign or unknown member is rejected.
        self._post("/set", self.lead_h, team_key="frc254", member_id=self.alex_id)
        self.assertEqual(self._by_team(self.lead_h), {"frc254": self.alex_id})
        self.assertEqual(self._post("/set", self.lead_h, team_key="frc254", member_id=self.other_leader_id).status_code, 404)
        self.assertEqual(self._post("/set", self.lead_h, team_key="frc254", member_id=99999).status_code, 404)
        self.assertEqual(self._by_team(self.lead_h), {"frc254": self.alex_id})
        cleared = self._post("/set", self.lead_h, team_key="frc254", member_id=None)
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(self._list(self.lead_h), [])

    def test_auto_split_is_leader_only(self):
        response = self._post("/auto-split", self.alex_h, team_keys=["frc1"], member_ids=[self.alex_id])
        self.assertEqual(response.status_code, 403)

    def test_auto_split_balances_and_is_deterministic(self):
        teams = ["frc1678", "frc254", "frc33", "frc118", "frc2056", "frc148", "frc971"]
        members = [self.sam_id, self.alex_id, self.lead_id]
        response = self._post("/auto-split", self.lead_h, team_keys=teams, member_ids=members)
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()["assignments"]
        self.assertEqual([row["team_key"] for row in rows], ["frc33", "frc118", "frc148", "frc254", "frc971", "frc1678", "frc2056"])
        self.assertTrue(all(row["source"] == "auto" for row in rows))
        counts = sorted(sum(1 for row in rows if row["member_id"] == m) for m in members)
        self.assertEqual(counts, [2, 2, 3])
        first = {row["team_key"]: row["member_id"] for row in rows}
        # Lowest member id deals first regardless of the order the request listed them.
        self.assertEqual(first["frc33"], min(members))

        self._post("/auto-split", self.lead_h, team_keys=teams, member_ids=members, replace_existing=True)
        self.assertEqual(self._by_team(self.lead_h), first)
        self._post("/auto-split", self.lead_h, team_keys=list(reversed(teams)), member_ids=list(reversed(members)), replace_existing=True)
        self.assertEqual(self._by_team(self.lead_h), first)

    def test_auto_split_skips_scouted_teams_when_asked(self):
        with self.SessionLocal() as db:
            db.add(models.PitScoutingEntry(workspace_id=self.workspace_id, event_key=EVENT, team_key="frc254", payload={"a": 1}, photos=[]))
            db.commit()
        rows = self._post("/auto-split", self.lead_h, team_keys=["frc254", "frc1678"], member_ids=[self.alex_id]).json()["assignments"]
        self.assertEqual([row["team_key"] for row in rows], ["frc1678"])
        rows = self._post(
            "/auto-split", self.lead_h, team_keys=["frc254", "frc1678"], member_ids=[self.alex_id], only_unscouted=False
        ).json()["assignments"]
        self.assertEqual([row["team_key"] for row in rows], ["frc254", "frc1678"])

    def test_auto_split_keeps_existing_unless_replace(self):
        self._post("/set", self.lead_h, team_key="frc254", member_id=self.sam_id)
        self._post("/auto-split", self.lead_h, team_keys=["frc254", "frc1678"], member_ids=[self.alex_id])
        by_team = self._by_team(self.lead_h)
        self.assertEqual(by_team, {"frc254": self.sam_id, "frc1678": self.alex_id})
        self._post("/auto-split", self.lead_h, team_keys=["frc254", "frc1678"], member_ids=[self.alex_id], replace_existing=True)
        self.assertEqual(self._by_team(self.lead_h), {"frc254": self.alex_id, "frc1678": self.alex_id})

    def test_auto_split_validates_input(self):
        bad_member = self._post("/auto-split", self.lead_h, team_keys=["frc254"], member_ids=[self.other_leader_id])
        self.assertEqual(bad_member.status_code, 404)
        self.assertEqual(self._post("/auto-split", self.lead_h, team_keys=["frc254"], member_ids=[]).status_code, 422)
        too_many = self._post("/auto-split", self.lead_h, team_keys=[f"frc{n}" for n in range(1, 202)], member_ids=[self.alex_id])
        self.assertEqual(too_many.status_code, 422)
        self.assertEqual(self._post("/auto-split", self.lead_h, team_keys=["bad"], member_ids=[self.alex_id]).status_code, 400)
        self.assertEqual(self._list(self.lead_h), [])

    def test_removing_a_member_frees_their_teams(self):
        self._post("/claim", self.alex_h, team_key="frc254")
        self._post("/claim", self.sam_h, team_key="frc1678")
        response = self.client.post(f"/workspaces/me/members/{self.alex_id}/remove", json={}, headers=self.lead_h)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._by_team(self.lead_h), {"frc1678": self.sam_id})
        # The freed team can be claimed by someone else.
        self.assertEqual(self._post("/claim", self.sam_h, team_key="frc254").status_code, 200)

    def test_leaving_the_workspace_frees_your_teams(self):
        self._post("/claim", self.alex_h, team_key="frc254")
        response = self.client.post("/workspaces/me/leave", json={}, headers=self.alex_h)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._list(self.lead_h), [])

    def test_workspaces_are_isolated(self):
        self._post("/claim", self.alex_h, team_key="frc254")
        self.assertEqual(self._list(self.other_h), [])
        # The other workspace can take the same team independently.
        self.assertEqual(self._post("/claim", self.other_h, team_key="frc254").status_code, 200)
        self.assertEqual(self._by_team(self.lead_h), {"frc254": self.alex_id})
        # It cannot assign, or auto-split onto, this workspace's members.
        self.assertEqual(self._post("/set", self.other_h, team_key="frc254", member_id=self.alex_id).status_code, 404)
        self.assertEqual(self._post("/auto-split", self.other_h, team_keys=["frc1"], member_ids=[self.alex_id]).status_code, 404)
        # Releasing in its own workspace leaves ours alone.
        self._post("/release", self.other_h, team_key="frc254")
        self.assertEqual(self._by_team(self.lead_h), {"frc254": self.alex_id})
        with self.SessionLocal() as db:
            rows = db.execute(select(models.PitAssignment)).scalars().all()
            self.assertEqual([(row.workspace_id, row.member_id) for row in rows], [(self.workspace_id, self.alex_id)])

    def test_events_are_separate(self):
        self._post("/claim", self.alex_h, team_key="frc254")
        self.assertEqual(self._list(self.alex_h, event="2026cmp"), [])


if __name__ == "__main__":
    unittest.main()
