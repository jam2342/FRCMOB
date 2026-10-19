from __future__ import annotations

from unittest import mock

from app.api import routes_matches
from app.db import models
from app.services.events.match_lineups import write_match_lineups
from tests.conftest import DBTestCase

EVENT = "2026catt"
MATCH = f"{EVENT}_sf8m1"


def _links(red, blue):
    return [(MATCH, f"frc{t}", "red", f"r{i}") for i, t in enumerate(red, 1)] + [
        (MATCH, f"frc{t}", "blue", f"b{i}") for i, t in enumerate(blue, 1)
    ]


class MatchLineupTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self.db.add(models.Event(event_key=EVENT, name="Tidal Tumble", year=2026))
        self.db.add(models.Match(match_key=MATCH, event_key=EVENT, comp_level="sf", set_number=8, match_number=1))
        for team in (5012, 7415, 4322, 973, 3925, 8005, 599, 1):
            self.db.add(models.Team(team_key=f"frc{team}", team_number=team))
        self.db.commit()

    def _lineup(self):
        rows = self.db.query(models.MatchTeam).filter(models.MatchTeam.match_key == MATCH).all()
        return {a: sorted(r.team_key for r in rows if r.alliance == a) for a in ("red", "blue")}

    def test_a_backup_robot_replaces_the_one_it_relieved(self):
        # Tidal Tumble SF8 on 2026-10-18: TBA listed 3925 until the result showed 599.
        write_match_lineups(self.db, EVENT, _links([5012, 7415, 4322], [973, 3925, 8005]))
        self.db.commit()
        write_match_lineups(self.db, EVENT, _links([5012, 7415, 4322], [973, 599, 8005]))
        self.db.commit()
        self.assertEqual(self._lineup()["blue"], ["frc599", "frc8005", "frc973"])

    def test_a_lineup_missing_an_alliance_only_adds(self):
        write_match_lineups(self.db, EVENT, _links([5012, 7415, 4322], [973, 3925, 8005]))
        self.db.commit()
        write_match_lineups(self.db, EVENT, _links([5012, 7415, 1], []))
        self.db.commit()
        self.assertEqual(len(self._lineup()["red"]), 4)
        self.assertEqual(len(self._lineup()["blue"]), 3)

    def test_a_robot_that_switched_sides_moves(self):
        write_match_lineups(self.db, EVENT, _links([5012, 7415, 4322], [973, 3925, 8005]))
        self.db.commit()
        write_match_lineups(self.db, EVENT, _links([5012, 7415, 973], [4322, 3925, 8005]))
        self.db.commit()
        self.assertEqual(self._lineup(), {"red": ["frc5012", "frc7415", "frc973"], "blue": ["frc3925", "frc4322", "frc8005"]})

    def test_a_placeholder_robot_on_both_alliances_saves_once(self):
        # 2026aroz QF1-2 on 2026-10-18: two-robot alliances, TBA filled both with frc9999.
        self.db.add(models.Team(team_key="frc9999", team_number=9999))
        self.db.commit()
        links = [(MATCH, "frc5012", "red", "r1"), (MATCH, "frc7415", "red", "r2"), (MATCH, "frc9999", "red", "r3"),
                 (MATCH, "frc973", "blue", "b1"), (MATCH, "frc8005", "blue", "b2"), (MATCH, "frc9999", "blue", "b3")]
        write_match_lineups(self.db, EVENT, links)
        self.db.commit()
        self.assertEqual(self._lineup(), {"red": ["frc5012", "frc7415", "frc9999"], "blue": ["frc8005", "frc973"]})

    def test_refreshing_the_schedule_from_tba_drops_replaced_robots(self):
        write_match_lineups(self.db, EVENT, _links([5012, 7415, 4322], [973, 3925, 8005]))
        self.db.commit()
        tba = mock.Mock()
        tba.event_teams.return_value = []
        tba.event_matches.return_value = [{
            "key": MATCH, "comp_level": "sf", "set_number": 8, "match_number": 1,
            "alliances": {"red": {"team_keys": ["frc5012", "frc7415", "frc4322"]},
                          "blue": {"team_keys": ["frc973", "frc599", "frc8005"]}},
        }]
        routes_matches._refresh_event_schedule_from_tba(self.db, tba, EVENT)
        self.assertEqual(self._lineup()["blue"], ["frc599", "frc8005", "frc973"])
