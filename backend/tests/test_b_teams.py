from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import models
from app.services.events.ingest import ingest_event
from tests.conftest import DBTestCase
from tests.test_alliance_results import EVENT, _FakeTBA, _match

MIGRATION = Path(__file__).resolve().parents[1]/"alembic/versions/20261115_0035_b_team_numbers.py"


def _repair(db):
    spec = importlib.util.spec_from_file_location("b_team_numbers", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    connection = db.connection()
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
    db.commit()


class BTeamTests(DBTestCase):
    def test_a_b_team_seen_only_in_matches_gets_its_number(self):
        match = _match(1)
        match["alliances"]["blue"]["team_keys"] = ["frc1", "frc2", "frc2403B"]
        ingest_event(EVENT, _FakeTBA([match]), self.db, run_post_compute=False)
        team = self.db.get(models.Team, "frc2403B")
        self.assertEqual(team.team_number, 2403)
        self.assertIsNone(team.nickname)  # TBA has no record for B teams; nothing is invented

    def test_repair_fixes_stored_zeros_only(self):
        self.db.add_all([models.Team(team_key="frc100B", team_number=0), models.Team(team_key="frc7587B", team_number=0),
                         models.Team(team_key="frc254", team_number=254), models.Team(team_key="frc9999", team_number=0)])
        self.db.commit()
        _repair(self.db)
        numbers = {t.team_key: t.team_number for t in self.db.query(models.Team)}
        self.assertEqual((numbers["frc100B"], numbers["frc7587B"], numbers["frc254"]), (100, 7587, 254))
        self.assertEqual(numbers["frc9999"], 0)  # not a B team: left for its own fix, never guessed here
        _repair(self.db)
        self.assertEqual({t.team_key: t.team_number for t in self.db.query(models.Team)}, numbers)
