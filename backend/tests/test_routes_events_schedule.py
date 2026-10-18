import math
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_events
from app.db import models
from app.db.base import Base

def _seed_minimal_schedule(db: Session) -> None:
    event_key = "2026txhou"
    match_key = "2026txhou_qm1"
    db.add(models.Event(event_key=event_key, name="Houston", year=2026))
    db.add(models.Match(match_key=match_key, event_key=event_key, comp_level="qm", set_number=1, match_number=1, time=1700000100))
    teams = [
        ("frc118", 118, "Robonauts", "red", "r1"),
        ("frc148", 148, "Robowranglers", "red", "r2"),
        ("frc2468", 2468, "Team Appreciate", "red", "r3"),
        ("frc3847", 3847, "Spectrum", "blue", "b1"),
        ("frc624", 624, "CRyptonite", "blue", "b2"),
        ("frc1477", 1477, "Texas Torque", "blue", "b3"),
    ]
    for team_key, team_number, nickname, alliance, station in teams:
        db.add(models.Team(team_key=team_key, team_number=team_number, nickname=nickname))
        db.add(models.MatchTeam(match_key=match_key, team_key=team_key, event_key=event_key, alliance=alliance, station=station))
    db.commit()

def _seed_ratings(db: Session, *, event_key: str = "2026txhou") -> None:
    # Give red the stronger alliance so deterministic prob skews red-favored.
    rating_data = [
        ("frc118", 75.0, 0.8),
        ("frc148", 72.0, 0.8),
        ("frc2468", 70.0, 0.7),
        ("frc3847", 55.0, 0.6),
        ("frc624", 52.0, 0.6),
        ("frc1477", 50.0, 0.5),
    ]
    for team_key, rating, confidence in rating_data:
        db.add(
            models.EventTeamRating(
                event_key=event_key,
                team_key=team_key,
                rating_0_100=rating,
                confidence_0_1=confidence,
            )
        )
    db.commit()

class DeterministicBaselineTests(unittest.TestCase):
    def test_sigmoid_weights_calibrated(self) -> None:
        # +10 rating margin alone should yield ~0.68
        prob_10 = routes_events._deterministic_red_win_prob(10.0)
        self.assertAlmostEqual(prob_10, 1.0 / (1.0 + math.exp(-0.75)), places=4)
        self.assertGreater(prob_10, 0.67)
        self.assertLess(prob_10, 0.70)

        # +20 rating margin alone should yield ~0.82
        prob_20 = routes_events._deterministic_red_win_prob(20.0)
        self.assertGreater(prob_20, 0.80)
        self.assertLess(prob_20, 0.84)

        # symmetric around 0
        self.assertAlmostEqual(
            routes_events._deterministic_red_win_prob(0.0), 0.5, places=6
        )

        # mirrored margin → mirrored prob
        self.assertAlmostEqual(
            routes_events._deterministic_red_win_prob(-15.0)
            + routes_events._deterministic_red_win_prob(15.0),
            1.0,
            places=6,
        )

    def test_extreme_inputs_clamped(self) -> None:
        self.assertEqual(routes_events._deterministic_red_win_prob(1_000.0), 1.0)
        self.assertEqual(routes_events._deterministic_red_win_prob(-1_000.0), 0.0)

class SchedulePredictionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(self.engine)
        self.db = self.SessionLocal()
        _seed_minimal_schedule(self.db)
        _seed_ratings(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _call(self):
        return routes_events.get_event_schedule_with_synergy(
            event_key="2026txhou",
            db=self.db,
        )

    def test_schedule_prediction_comes_from_the_team_model(self) -> None:
        payload = self._call()

        self.assertEqual(int(payload.get("deterministic_prediction_count") or 0), 1)
        row = payload["matches"][0]
        prediction = row["prediction"]
        self.assertTrue(prediction["available"])
        self.assertEqual(prediction["source_label"], "deterministic_team_model_v3")
        self.assertAlmostEqual(
            prediction["red_win_prob"],
            prediction["red_win_prob_deterministic"],
            places=6,
        )
        # No official results for anyone yet: six typical robots, a coin flip (the old
        # rating rows seeded here no longer decide it).
        self.assertEqual(prediction["red_win_prob"], 0.5)
        self.assertIsNone(prediction["favored_alliance"])
