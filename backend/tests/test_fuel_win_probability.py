from __future__ import annotations

import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_events
from app.db import models
from app.db.base import Base
from app.services.events import fuel_win_probability as fuel
from app.services.ml.synergy import SYNERGY_MODEL_VERSION

RED = ["frc1", "frc2", "frc3"]
BLUE = ["frc4", "frc5", "frc6"]


class FuelWinProbabilityTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()
        for n in range(1, 7):
            self.db.add(models.Team(team_key=f"frc{n}", team_number=n))
        for event_key, start in (("2026prior", 1_000), ("2026here", 10_000)):
            self.db.add(models.Event(event_key=event_key, name=event_key, year=2026))
            for number in (1, 2):
                match_key = f"{event_key}_qm{number}"
                self.db.add(models.Match(match_key=match_key, event_key=event_key, comp_level="qm", set_number=1, match_number=number, time=start + number))
                for alliance, teams in (("red", RED), ("blue", BLUE)):
                    for team in teams:
                        self.db.add(models.MatchTeam(match_key=match_key, team_key=team, event_key=event_key, alliance=alliance, station=None))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _rate(self, match_key, team_key, rate):
        run = models.AnalysisRun(match_key=match_key, run_kind="official_truth", status="completed")
        self.db.add(run)
        self.db.flush()
        self.db.add(models.TeamMatchFinding(
            analysis_run_id=run.id, match_key=match_key, event_key=match_key.split("_")[0], team_key=team_key,
            alliance="red" if team_key in RED else "blue", source="tba_score_breakdown", fuel_scoring_rate=rate, summary={},
        ))
        self.db.commit()

    def _margins(self):
        order = [("2026here_qm1", RED, BLUE), ("2026here_qm2", RED, BLUE)]
        return fuel.fuel_margins_by_match(self.db, "2026here", order, event_start=10_001)

    def test_a_match_never_informs_its_own_prediction(self):
        for team in RED:
            self._rate("2026here_qm1", team, 60.0)
        for team in BLUE:
            self._rate("2026here_qm1", team, 20.0)
        margins = self._margins()
        self.assertNotIn("2026here_qm1", margins)  # nothing known before the first match
        self.assertAlmostEqual(margins["2026here_qm2"], 3 * 60.0 - 3 * 20.0)

    def test_previous_event_rate_is_used_and_shrunk_toward(self):
        for team in RED + BLUE:
            self._rate("2026prior_qm1", team, 40.0)
        self._rate("2026here_qm1", "frc1", 80.0)
        margins = self._margins()
        self.assertAlmostEqual(margins["2026here_qm1"], 0.0)
        # frc1: one match at 80 against a prior of 40, weight 1/(1+3).
        self.assertAlmostEqual(margins["2026here_qm2"], (1 * 80.0 + 3 * 40.0) / 4 - 40.0)

    def test_one_known_alliance_is_left_to_the_rating_formula(self):
        self._rate("2026prior_qm1", "frc1", 50.0)  # only a red team has any history
        margins = self._margins()
        self.assertNotIn("2026here_qm1", margins)

    def test_confidence_grows_with_matches_seen_here(self):
        early, few, settled = (fuel.fuel_red_win_prob(60.0, matches_seen=n) for n in (0, 1, 5))
        self.assertLess(early, few)
        self.assertLess(few, settled)
        self.assertAlmostEqual(settled, fuel.fuel_red_win_prob(60.0))

    def test_probability_is_symmetric(self):
        self.assertAlmostEqual(fuel.fuel_red_win_prob(0.0), 0.5)
        self.assertAlmostEqual(fuel.fuel_red_win_prob(25.0) + fuel.fuel_red_win_prob(-25.0), 1.0)
        self.assertGreater(fuel.fuel_red_win_prob(25.0), 0.8)

    def test_schedule_uses_fuel_and_keeps_the_rating_number(self):
        for team in RED:
            self._rate("2026here_qm1", team, 60.0)
        for team in BLUE:
            self._rate("2026here_qm1", team, 20.0)
        for team in RED + BLUE:
            self.db.add(models.EventTeamRating(event_key="2026here", team_key=team, rating_0_100=50.0, confidence_0_1=0.5))
        self.db.commit()
        payload = routes_events.get_event_schedule_with_synergy(
            event_key="2026here", request=SimpleNamespace(), synergy_model_version=SYNERGY_MODEL_VERSION,
            include_pair_breakdown=False, include_ml_predictions=True, refresh=False, db=self.db,
        )
        by_key = {row["match_key"]: row["prediction"] for row in payload["matches"]}
        self.assertEqual(by_key["2026here_qm1"]["source_label"], "deterministic_rating_synergy_v1")
        second = by_key["2026here_qm2"]
        self.assertEqual(second["source_label"], "deterministic_fuel_v1")
        # Each team has played once here, so the tempered early scale applies.
        self.assertAlmostEqual(second["red_win_prob"], round(fuel.fuel_red_win_prob(120.0, matches_seen=1), 4))
        self.assertEqual(second["red_win_prob_rating"], 0.5)
        self.assertEqual(payload["fuel_prediction_count"], 1)


if __name__ == "__main__":
    unittest.main()
