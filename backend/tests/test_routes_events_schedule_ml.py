import math
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_events
from app.core.config import settings
from app.db import models
from app.db.base import Base
from app.services.ml.synergy import SYNERGY_MODEL_VERSION

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

class EventScheduleWithSynergyMLTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_schedule_includes_prediction_payload_when_ml_enabled(self):
        original_ml_shadow_enabled = settings.ml_shadow_enabled
        try:
            settings.ml_shadow_enabled = True
            payload = routes_events.get_event_schedule_with_synergy(
                event_key="2026txhou",
                request=SimpleNamespace(),
                synergy_model_version=SYNERGY_MODEL_VERSION,
                include_pair_breakdown=False,
                include_ml_predictions=True,
                refresh=False,
                db=self.db,
            )
        finally:
            settings.ml_shadow_enabled = original_ml_shadow_enabled

        self.assertTrue(payload.get("ok"))
        self.assertEqual(int(payload.get("count") or 0), 1)
        self.assertIn("matches", payload)
        row = payload["matches"][0]
        prediction = row.get("prediction") if isinstance(row, dict) else None
        self.assertIsInstance(prediction, dict)
        assert isinstance(prediction, dict)
        self.assertIn("available", prediction)
        self.assertEqual(str(prediction.get("model_key")), "match_outcome")

class DeterministicBaselineTests(unittest.TestCase):
    def test_sigmoid_weights_calibrated(self) -> None:
        # +10 rating margin alone should yield ~0.68
        prob_10 = routes_events._deterministic_red_win_prob(10.0, 0.0, 0.0)
        self.assertAlmostEqual(prob_10, 1.0 / (1.0 + math.exp(-0.75)), places=4)
        self.assertGreater(prob_10, 0.67)
        self.assertLess(prob_10, 0.70)

        # +20 rating margin alone should yield ~0.82
        prob_20 = routes_events._deterministic_red_win_prob(20.0, 0.0, 0.0)
        self.assertGreater(prob_20, 0.80)
        self.assertLess(prob_20, 0.84)

        # symmetric around 0
        self.assertAlmostEqual(
            routes_events._deterministic_red_win_prob(0.0, 0.0, 0.0), 0.5, places=6
        )

        # mirrored margin → mirrored prob
        self.assertAlmostEqual(
            routes_events._deterministic_red_win_prob(-15.0, -10.0, -5.0)
            + routes_events._deterministic_red_win_prob(15.0, 10.0, 5.0),
            1.0,
            places=6,
        )

    def test_extreme_inputs_clamped(self) -> None:
        self.assertEqual(routes_events._deterministic_red_win_prob(1_000.0, 0.0, 0.0), 1.0)
        self.assertEqual(routes_events._deterministic_red_win_prob(-1_000.0, 0.0, 0.0), 0.0)

class BlendedPredictionTests(unittest.TestCase):
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
            request=SimpleNamespace(),
            synergy_model_version=SYNERGY_MODEL_VERSION,
            include_pair_breakdown=False,
            include_ml_predictions=True,
            refresh=False,
            db=self.db,
        )

    def test_deterministic_populated_when_ml_disabled(self) -> None:
        original_enabled = settings.ml_shadow_enabled
        original_blend = settings.ml_match_outcome_blend
        try:
            settings.ml_shadow_enabled = False
            settings.ml_match_outcome_blend = 0.0
            payload = self._call()
        finally:
            settings.ml_shadow_enabled = original_enabled
            settings.ml_match_outcome_blend = original_blend

        self.assertEqual(int(payload.get("deterministic_prediction_count") or 0), 1)
        self.assertEqual(int(payload.get("ml_prediction_count") or 0), 0)
        self.assertEqual(int(payload.get("ml_blended_prediction_count") or 0), 0)

        row = payload["matches"][0]
        prediction = row["prediction"]
        self.assertTrue(prediction["available"])
        self.assertEqual(prediction["source_label"], "deterministic_rating_synergy_v1")
        self.assertIsNone(prediction["model_version"])
        self.assertIsNone(prediction["red_win_prob_ml"])
        self.assertIsNotNone(prediction["red_win_prob_deterministic"])
        self.assertAlmostEqual(
            prediction["red_win_prob"],
            prediction["red_win_prob_deterministic"],
            places=6,
        )
        self.assertEqual(prediction["favored_alliance"], "red")
        self.assertEqual(prediction["prediction_blend"], 0.0)

    def test_blend_zero_keeps_deterministic_even_when_ml_available(self) -> None:
        def _fake_infer(_db, *, rows, **_kwargs):
            return {
                "ok": True,
                "model_version": "fake_match_outcome_v1",
                "predictions": [
                    {"event_key": r["event_key"], "match_key": r["match_key"], "red_win_prob": 0.9}
                    for r in rows
                ],
            }

        original_enabled = settings.ml_shadow_enabled
        original_blend = settings.ml_match_outcome_blend
        original_infer = routes_events.infer_match_outcome_shadow_from_rows
        try:
            settings.ml_shadow_enabled = True
            settings.ml_match_outcome_blend = 0.0
            routes_events.infer_match_outcome_shadow_from_rows = _fake_infer  # type: ignore[assignment]
            payload = self._call()
        finally:
            settings.ml_shadow_enabled = original_enabled
            settings.ml_match_outcome_blend = original_blend
            routes_events.infer_match_outcome_shadow_from_rows = original_infer  # type: ignore[assignment]

        self.assertEqual(int(payload.get("ml_blended_prediction_count") or 0), 0)
        prediction = payload["matches"][0]["prediction"]
        self.assertEqual(prediction["source_label"], "deterministic_rating_synergy_v1")
        self.assertEqual(prediction["prediction_blend"], 0.0)
        self.assertAlmostEqual(
            prediction["red_win_prob"],
            prediction["red_win_prob_deterministic"],
            places=6,
        )
        self.assertEqual(prediction["red_win_prob_ml"], 0.9)

    def test_blend_half_averages_ml_and_deterministic(self) -> None:
        def _fake_infer(_db, *, rows, **_kwargs):
            return {
                "ok": True,
                "model_version": "fake_match_outcome_v1",
                "predictions": [
                    {"event_key": r["event_key"], "match_key": r["match_key"], "red_win_prob": 0.9}
                    for r in rows
                ],
            }

        original_enabled = settings.ml_shadow_enabled
        original_blend = settings.ml_match_outcome_blend
        original_infer = routes_events.infer_match_outcome_shadow_from_rows
        try:
            settings.ml_shadow_enabled = True
            settings.ml_match_outcome_blend = 0.5
            routes_events.infer_match_outcome_shadow_from_rows = _fake_infer  # type: ignore[assignment]
            payload = self._call()
        finally:
            settings.ml_shadow_enabled = original_enabled
            settings.ml_match_outcome_blend = original_blend
            routes_events.infer_match_outcome_shadow_from_rows = original_infer  # type: ignore[assignment]

        self.assertEqual(int(payload.get("ml_blended_prediction_count") or 0), 1)
        prediction = payload["matches"][0]["prediction"]
        self.assertEqual(prediction["source_label"], "blended_det_ml_v1")
        self.assertEqual(prediction["prediction_blend"], 0.5)
        self.assertEqual(prediction["red_win_prob_ml"], 0.9)

        det_prob = prediction["red_win_prob_deterministic"]
        self.assertIsNotNone(det_prob)
        expected = 0.5 * float(det_prob) + 0.5 * 0.9
        self.assertAlmostEqual(prediction["red_win_prob"], round(expected, 4), places=4)

    def test_blend_one_uses_ml_only(self) -> None:
        def _fake_infer(_db, *, rows, **_kwargs):
            return {
                "ok": True,
                "model_version": "fake_match_outcome_v1",
                "predictions": [
                    {"event_key": r["event_key"], "match_key": r["match_key"], "red_win_prob": 0.9}
                    for r in rows
                ],
            }

        original_enabled = settings.ml_shadow_enabled
        original_blend = settings.ml_match_outcome_blend
        original_infer = routes_events.infer_match_outcome_shadow_from_rows
        try:
            settings.ml_shadow_enabled = True
            settings.ml_match_outcome_blend = 1.0
            routes_events.infer_match_outcome_shadow_from_rows = _fake_infer  # type: ignore[assignment]
            payload = self._call()
        finally:
            settings.ml_shadow_enabled = original_enabled
            settings.ml_match_outcome_blend = original_blend
            routes_events.infer_match_outcome_shadow_from_rows = original_infer  # type: ignore[assignment]

        prediction = payload["matches"][0]["prediction"]
        self.assertEqual(prediction["source_label"], "ml_shadow_match_outcome_live")
        self.assertEqual(prediction["prediction_blend"], 1.0)
        self.assertEqual(prediction["red_win_prob"], 0.9)

    def test_ml_inference_failure_falls_back_to_deterministic(self) -> None:
        def _exploding_infer(_db, *, rows, **_kwargs):
            raise RuntimeError("model file missing")

        original_enabled = settings.ml_shadow_enabled
        original_blend = settings.ml_match_outcome_blend
        original_infer = routes_events.infer_match_outcome_shadow_from_rows
        try:
            settings.ml_shadow_enabled = True
            settings.ml_match_outcome_blend = 0.5
            routes_events.infer_match_outcome_shadow_from_rows = _exploding_infer  # type: ignore[assignment]
            payload = self._call()
        finally:
            settings.ml_shadow_enabled = original_enabled
            settings.ml_match_outcome_blend = original_blend
            routes_events.infer_match_outcome_shadow_from_rows = original_infer  # type: ignore[assignment]

        prediction = payload["matches"][0]["prediction"]
        self.assertTrue(prediction["available"])
        self.assertEqual(prediction["source_label"], "deterministic_rating_synergy_v1")
        self.assertIsNone(prediction["red_win_prob_ml"])

if __name__ == "__main__":
    unittest.main()
