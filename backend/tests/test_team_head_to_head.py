from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.teams.head_to_head import get_team_head_to_head
from app.db import models
from tests.conftest import DBTestCase


class HeadToHeadTests(DBTestCase):
    def setUp(self):
        super().setUp()
        for event_key, year in (("2025mnmi", 2025), ("2026mndu", 2026)):
            self.db.add(models.Event(event_key=event_key, name=f"Event {year}", year=year))
        for n in range(1, 9):
            self.db.add(models.Team(team_key=f"frc{n}", team_number=n))
        # (match, event, time, red, blue, red score, blue score)
        played = [
            ("2025mnmi_qm1", "2025mnmi", 100, (1, 2, 3), (4, 5, 6), 50, 40),  # 1+2 together, win
            ("2025mnmi_qm2", "2025mnmi", 200, (1, 4, 5), (2, 6, 7), 30, 60),  # 1 vs 2, 1 loses
            ("2026mndu_qm1", "2026mndu", 300, (2, 7, 8), (1, 3, 4), 70, 70),  # 1 vs 2, tie
            ("2026mndu_qm2", "2026mndu", 400, (1, 2, 8), (3, 4, 5), None, None),  # together, not played yet
            ("2026mndu_qm3", "2026mndu", 500, (3, 4, 5), (6, 7, 8), 10, 20),  # neither: left out
        ]
        for key, event_key, time, red, blue, red_score, blue_score in played:
            self.db.add(models.Match(match_key=key, event_key=event_key, comp_level="qm", set_number=1, match_number=int(key[-1]), time=time))
            for alliance, side in (("red", red), ("blue", blue)):
                for station, n in enumerate(side, start=1):
                    self.db.add(models.MatchTeam(match_key=key, team_key=f"frc{n}", event_key=event_key, alliance=alliance, station=f"{alliance[0]}{station}"))
            if red_score is None:
                continue
            for alliance, side, score, other in (("red", red, red_score, blue_score), ("blue", blue, blue_score, red_score)):
                self.db.add(models.MatchAllianceResult(
                    match_key=key, alliance=alliance, event_key=event_key, season_year=int(event_key[:4]),
                    team_keys=[f"frc{n}" for n in side], total_score=score,
                    result="win" if score > other else "loss" if score < other else "tie",
                ))
        self.db.commit()

    def test_counts_matches_together_and_against_from_the_first_teams_side(self):
        body = get_team_head_to_head("frc1", other="2", db=self.db)
        self.assertEqual(body["other_key"], "frc2")
        self.assertEqual(body["together"], {"played": 1, "wins": 1, "losses": 0, "ties": 0})
        self.assertEqual(body["against"], {"played": 2, "wins": 0, "losses": 1, "ties": 1})
        self.assertEqual(body["seasons"], [2026, 2025])
        self.assertEqual(body["match_count"], 4)
        # Newest first; the unplayed match is listed without a result.
        self.assertEqual([m["match_key"] for m in body["matches"]], ["2026mndu_qm2", "2026mndu_qm1", "2025mnmi_qm2", "2025mnmi_qm1"])
        self.assertIsNone(body["matches"][0]["result"])
        self.assertEqual(body["matches"][2], {
            "match_key": "2025mnmi_qm2", "event_key": "2025mnmi", "event_name": "Event 2025", "season": 2025,
            "comp_level": "qm", "relation": "against", "alliance": "red", "score": 30, "opponent_score": 60,
            "result": "loss", "time": 200,
        })

    def test_from_the_other_side_the_record_flips(self):
        body = get_team_head_to_head("frc2", other="frc1", db=self.db)
        self.assertEqual(body["against"], {"played": 2, "wins": 1, "losses": 0, "ties": 1})

    def test_teams_that_never_met(self):
        body = get_team_head_to_head("frc1", other="frc99", db=self.db)
        self.assertEqual((body["match_count"], body["matches"]), (0, []))

    def test_a_team_against_itself_is_refused(self):
        with pytest.raises(HTTPException) as caught:
            get_team_head_to_head("frc1", other="1", db=self.db)
        self.assertEqual(caught.value.status_code, 400)
