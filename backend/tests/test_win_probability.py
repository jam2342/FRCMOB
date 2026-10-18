from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_events
from app.db import models
from app.db.base import Base
from app.services.events import win_probability as wp

RED = ["frc1", "frc2", "frc3"]
BLUE = ["frc4", "frc5", "frc6"]


def add_result(db, match_key, event_key, red_teleop, blue_teleop, red=RED, blue=BLUE):
    # An alliance result as ingest stores it (scoring/alliance_results.py).
    for alliance, teams, teleop, other in (("red", red, red_teleop, blue_teleop), ("blue", blue, blue_teleop, red_teleop)):
        db.add(models.MatchAllianceResult(
            match_key=match_key, alliance=alliance, event_key=event_key, season_year=2026,
            team_keys=list(teams), surrogate_team_keys=[], dq_team_keys=[],
            total_score=int(teleop), score_without_fouls=int(teleop),
            result="win" if teleop > other else "loss" if teleop < other else "tie",
            components={
                "teleop_hub_points": teleop, "auto_hub_points": 0,
                "robot_auto_tower_points": [0.0, 0.0, 0.0], "robot_endgame_tower_points": [0.0, 0.0, 0.0],
            },
            raw_breakdown={},
        ))
    db.commit()


class TeamModelWinProbabilityTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()
        for n in range(1, 7):
            self.db.add(models.Team(team_key=f"frc{n}", team_number=n))
        for event_key, start in (("2026prior", 1_000), ("2026here", 10_000)):
            self.db.add(models.Event(event_key=event_key, name=event_key, year=2026))
            for number in (1, 2, 3):
                match_key = f"{event_key}_qm{number}"
                self.db.add(models.Match(match_key=match_key, event_key=event_key, comp_level="qm", set_number=1, match_number=number, time=start + number))
                for alliance, teams in (("red", RED), ("blue", BLUE)):
                    for team in teams:
                        self.db.add(models.MatchTeam(match_key=match_key, team_key=team, event_key=event_key, alliance=alliance, station=None))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _predictions(self):
        order = [(f"2026here_qm{n}", RED, BLUE) for n in (1, 2, 3)]
        return wp.match_predictions(self.db, "2026here", order)

    def test_a_played_match_keeps_the_prediction_made_before_it(self):
        add_result(self.db, "2026here_qm1", "2026here", 300, 100)
        predictions = self._predictions()
        margin, sd, seen, _prediction, _features = predictions["2026here_qm1"]
        # Nothing known before the first match: six season priors, an even match.
        self.assertAlmostEqual(margin, 0.0)
        self.assertEqual(seen, 0)
        # The next match (not played yet) is forecast from what qm1 showed.
        margin2, sd2, seen2, _prediction2, _features2 = predictions["2026here_qm2"]
        self.assertGreater(margin2, 0.0)
        self.assertEqual(seen2, 1)
        self.assertLess(sd2, sd)

    def test_earlier_events_carry_into_this_one(self):
        add_result(self.db, "2026prior_qm1", "2026prior", 320, 90)
        add_result(self.db, "2026prior_qm2", "2026prior", 310, 100)
        margin, _sd, seen, _prediction, _features = self._predictions()["2026here_qm1"]
        self.assertGreater(margin, 100.0)
        self.assertEqual(seen, 0)

    def test_a_later_match_never_informs_an_earlier_one(self):
        # qm3 was played before qm2 (postponed qm2): qm2's result can't move qm3's prediction.
        self.db.get(models.Match, "2026here_qm3").actual_time = 10_001
        self.db.get(models.Match, "2026here_qm1").actual_time = 10_000
        self.db.get(models.Match, "2026here_qm2").actual_time = 10_050
        self.db.commit()
        add_result(self.db, "2026here_qm1", "2026here", 200, 200)
        add_result(self.db, "2026here_qm3", "2026here", 200, 200)
        before = self._predictions()["2026here_qm3"]
        add_result(self.db, "2026here_qm2", "2026here", 400, 50)
        after = self._predictions()["2026here_qm3"]
        self.assertEqual(before, after)

    def test_probability_from_margin_and_spread(self):
        self.assertAlmostEqual(wp.red_win_prob(0.0, 50.0), 0.5)
        self.assertAlmostEqual(wp.red_win_prob(40.0, 50.0) + wp.red_win_prob(-40.0, 50.0), 1.0)
        # The same margin is surer when the spread is smaller.
        self.assertGreater(wp.red_win_prob(40.0, 30.0), wp.red_win_prob(40.0, 90.0))
        early, settled = wp.red_win_prob(40.0, 50.0, matches_seen=0), wp.red_win_prob(40.0, 50.0, matches_seen=5)
        self.assertLessEqual(early, settled)

    def test_schedule_uses_the_team_model_and_keeps_the_rating_number(self):
        add_result(self.db, "2026here_qm1", "2026here", 300, 100)
        for team in RED + BLUE:
            self.db.add(models.EventTeamRating(event_key="2026here", team_key=team, rating_0_100=50.0, confidence_0_1=0.5))
        self.db.commit()
        payload = routes_events.get_event_schedule_with_synergy(
            event_key="2026here", db=self.db,
        )
        by_key = {row["match_key"]: row["prediction"] for row in payload["matches"]}
        self.assertEqual(by_key["2026here_qm1"]["source_label"], "deterministic_team_model_v3")
        self.assertAlmostEqual(by_key["2026here_qm1"]["red_win_prob"], 0.5)
        second = by_key["2026here_qm2"]
        margin, sd, seen, _prediction, _features = self._predictions()["2026here_qm2"]
        self.assertAlmostEqual(second["red_win_prob"], round(wp.red_win_prob(margin, sd, matches_seen=seen), 4))
        self.assertGreater(second["red_win_prob"], 0.5)
        self.assertEqual(second["red_win_prob_rating"], 0.5)
        self.assertEqual(payload["points_prediction_count"], 3)


if __name__ == "__main__":
    unittest.main()


class WinCorrectionTests(unittest.TestCase):
    def _row(self, z: float) -> list[float]:
        # 14 features: a red edge of z standard deviations, everything else neutral.
        return [z, z * 0.6, z * 0.6, 1.7 * z, z * 0.3, z * 0.2, 0.0, 0.0, 0.0, 0.5, 0.5, 0.5, 0.0, 0.5]

    def test_the_network_moves_with_the_margin_and_stays_a_probability(self):
        from unittest.mock import patch

        from app.services.ratings import win_correction

        with patch.object(win_correction, "CORRECTION_BLEND", 1.0):
            self.assertTrue(win_correction.active(2026))
            low, even, high = (win_correction.corrected(2026, 0.5, self._row(z)) for z in (-2.0, 0.0, 2.0))
            self.assertTrue(0.0 < low < even < high < 1.0)
            self.assertGreater(high, 0.8)
            # No weights for a season: the deterministic answer, untouched.
            self.assertFalse(win_correction.active(2031))
            self.assertEqual(win_correction.corrected(2031, 0.42, self._row(1.0)), 0.42)
        with patch.object(win_correction, "CORRECTION_BLEND", 0.0):
            self.assertEqual(win_correction.corrected(2026, 0.42, self._row(1.0)), 0.42)

    def test_shipped_weights_match_the_feature_count(self):
        import json
        from pathlib import Path

        import app.services.ratings.win_correction as module

        data = json.loads((Path(module.__file__).parent / "data" / "win_correction_2026.json").read_text())
        self.assertEqual(data["features"], len(self._row(0.0)))
        self.assertLess(data["report"]["holdout"]["corrected_brier"], data["report"]["holdout"]["team_model_brier"])
