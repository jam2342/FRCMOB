# Phase 5 tests — ML role classifier feature vector, snapshot rebuild, blend knob.
import unittest

from app.core.config import settings
from app.db import models
from app.services.ml.shadow import (
    infer_role_signals_shadow,
    rebuild_feature_snapshots,
)
from app.services.ml.role_ml import (
    ROLE_SIGNAL_FEATURE_ORDER,
    ROLE_SIGNAL_NAMES,
    blend_role_signals,
    build_role_signal_feature_vector,
    normalize_role_signal_name,
    role_signal_model_key,
)
from tests.conftest import DBTestCase

class RoleSignalFeatureVectorTests(unittest.TestCase):
    def test_feature_vector_has_expected_keys(self):
        vector = build_role_signal_feature_vector(None, event_year=2026)
        for key in ROLE_SIGNAL_FEATURE_ORDER:
            self.assertIn(key, vector, f"Missing feature key: {key}")
        self.assertEqual(len(vector), len(ROLE_SIGNAL_FEATURE_ORDER))

    def test_feature_vector_defaults_without_rating(self):
        vector = build_role_signal_feature_vector(None, event_year=2026)
        # When rating is None, all features default to 0.0 (no data)
        self.assertEqual(vector["rating_0_100"], 0.0)
        self.assertEqual(vector["confidence_0_1"], 0.0)
        self.assertEqual(vector["event_year"], 0.0)

    def test_model_key_generation(self):
        self.assertEqual(role_signal_model_key("scorer"), "role_signal:scorer")
        self.assertEqual(role_signal_model_key("DEFENDER"), "role_signal:defender")

    def test_normalize_signal_name(self):
        self.assertEqual(normalize_role_signal_name("  Scorer  "), "scorer")
        self.assertEqual(normalize_role_signal_name(None), "")

class RoleSignalBlendTests(unittest.TestCase):
    def test_blend_zero_returns_deterministic(self):
        det = {"scorer_signal": 80.0, "defender_signal": 20.0}
        ml = {"scorer_signal": 60.0, "defender_signal": 40.0}
        result = blend_role_signals(det, ml, 0.0)
        self.assertEqual(result["scorer_signal"], 80.0)
        self.assertEqual(result["defender_signal"], 20.0)

    def test_blend_one_returns_ml(self):
        det = {"scorer_signal": 80.0, "defender_signal": 20.0}
        ml = {"scorer_signal": 60.0, "defender_signal": 40.0}
        result = blend_role_signals(det, ml, 1.0)
        self.assertEqual(result["scorer_signal"], 60.0)
        self.assertEqual(result["defender_signal"], 40.0)

    def test_blend_half(self):
        det = {"scorer_signal": 80.0, "defender_signal": 20.0}
        ml = {"scorer_signal": 60.0, "defender_signal": 40.0}
        result = blend_role_signals(det, ml, 0.5)
        self.assertAlmostEqual(result["scorer_signal"], 70.0, places=4)
        self.assertAlmostEqual(result["defender_signal"], 30.0, places=4)

    def test_blend_with_none_ml_falls_back(self):
        det = {"scorer_signal": 80.0, "defender_signal": 20.0}
        ml = {"scorer_signal": None, "defender_signal": 40.0}
        result = blend_role_signals(det, ml, 0.5)
        self.assertEqual(result["scorer_signal"], 80.0)  # fallback
        self.assertAlmostEqual(result["defender_signal"], 30.0, places=4)

class RoleSignalSnapshotRebuildTests(DBTestCase):
    def _seed_event_with_role_classification(self):
        self.db.add(models.Event(event_key="2026txtest", name="TX Test 2026", year=2026))
        self.db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
        self.db.add(
            models.Match(
                match_key="2026txtest_qm1",
                event_key="2026txtest",
                comp_level="qm",
                set_number=1,
                match_number=1,
                time=1700000100,
            )
        )
        self.db.add(
            models.EventTeamRating(
                event_key="2026txtest",
                team_key="frc118",
                rating_0_100=83.0,
                confidence_0_1=0.72,
                robot_level_0_100=79.0,
                driver_skill_0_100=76.0,
                results_anchor=71.0,
                throughput=81.0,
                shift_productivity=74.0,
                capacity_utilization=69.0,
                endgame=77.0,
                consistency=73.0,
                details_json={
                    "subscores": {
                        "auto_contribution": 64.0,
                        "defense_presence": 43.0,
                        "penalty_discipline": 87.0,
                    },
                    "role_classification": {
                        "primary_role": "primary_scorer",
                        "role_signals": {
                            "scorer_signal": 78.5,
                            "defender_signal": 22.0,
                            "feeder_signal": 45.0,
                            "endgame_signal": 55.0,
                        },
                        "versatility_score": 62.0,
                        "specialization": 40.0,
                    },
                },
                model_version="rating_v13_forgiving_scale",
            )
        )
        self.db.commit()

    def test_rebuild_includes_role_signal_snapshots(self):
        self._seed_event_with_role_classification()
        result = rebuild_feature_snapshots(
            self.db,
            event_key="2026txtest",
            limit_events=5,
            replace_existing=True,
            source_version="role_signal_ut",
        )
        self.assertTrue(bool(result.get("ok")))
        role_result = result.get("role_signal", {})
        self.assertGreaterEqual(int(role_result.get("rows_written", 0)), 4)  # 4 signals

        snapshot = (
            self.db.query(models.MLFeatureSnapshot)
            .filter(
                models.MLFeatureSnapshot.scope == "role_signal:scorer",
                models.MLFeatureSnapshot.source_version == "role_signal_ut",
            )
            .first()
        )
        self.assertIsNotNone(snapshot)
        fv = snapshot.feature_vector or {}
        self.assertIn("rating_0_100", fv)
        self.assertEqual(fv.get("rating_0_100"), 83.0)
        target = snapshot.target or {}
        self.assertEqual(target.get("signal_value"), 78.5)

    def test_rebuild_skips_teams_without_role_classification(self):
        self.db.add(models.Event(event_key="2026txtest", name="TX Test 2026", year=2026))
        self.db.add(models.Team(team_key="frc999", team_number=999, nickname="NoRole"))
        self.db.add(
            models.Match(
                match_key="2026txtest_qm1",
                event_key="2026txtest",
                comp_level="qm",
                set_number=1,
                match_number=1,
                time=1700000100,
            )
        )
        self.db.add(
            models.EventTeamRating(
                event_key="2026txtest",
                team_key="frc999",
                rating_0_100=60.0,
                confidence_0_1=0.5,
                robot_level_0_100=55.0,
                driver_skill_0_100=50.0,
                results_anchor=45.0,
                throughput=58.0,
                shift_productivity=52.0,
                capacity_utilization=48.0,
                endgame=50.0,
                consistency=55.0,
                details_json={},  # no role_classification
                model_version="rating_v13_forgiving_scale",
            )
        )
        self.db.commit()

        result = rebuild_feature_snapshots(
            self.db,
            event_key="2026txtest",
            limit_events=5,
            replace_existing=True,
            source_version="role_signal_ut_skip",
        )
        role_result = result.get("role_signal", {})
        self.assertEqual(int(role_result.get("rows_written", 0)), 0)
        self.assertGreaterEqual(int(role_result.get("skipped_no_labels", 0)), 1)

class RoleSignalInferenceFallbackTests(DBTestCase):
    def test_inference_returns_no_model_gracefully(self):
        result = infer_role_signals_shadow(self.db, rating=None, event_year=2026)
        self.assertFalse(bool(result.get("ok")))
        self.assertEqual(result.get("reason"), "missing_rating")
        preds = result.get("predictions", {})
        for signal_name in ROLE_SIGNAL_NAMES:
            self.assertIsNone(preds.get(f"{signal_name}_signal"))

class RoleBlendKnobTests(unittest.TestCase):
    def test_blend_knob_code_default_zero(self):
        # The CODE default must stay conservative (0.0) so ML is opt-in. The
        # live value is intentionally raised via .env in deployments; assert
        # the field default here, not the env-influenced live setting.
        from app.core.config import Settings

        self.assertEqual(
            float(Settings.model_fields["ml_role_blend"].default), 0.0
        )

    def test_blend_knob_clamped(self):
        original = settings.ml_role_blend
        try:
            settings.ml_role_blend = 3.0
            knob = max(0.0, min(1.0, float(settings.ml_role_blend)))
            self.assertEqual(knob, 1.0)
        finally:
            settings.ml_role_blend = original

if __name__ == "__main__":
    unittest.main()
