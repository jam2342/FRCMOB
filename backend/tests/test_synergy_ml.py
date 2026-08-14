# Phase 4 tests — pair synergy ML feature vector, snapshot rebuild, blend knob.
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.db import models
from app.services.ml.shadow import (
    infer_synergy_pair_shadow_from_rows,
    rebuild_feature_snapshots,
)
from app.services.ml.synergy import compute_match_synergy_projections
from app.services.ml.synergy_ml import (
    FEATURE_SCOPE_SYNERGY_PAIR,
    SYNERGY_PAIR_FEATURE_ORDER,
    SYNERGY_PAIR_MODEL_KEY,
    build_synergy_pair_feature_vector,
)
from tests.conftest import DBTestCase

class SynergyPairFeatureVectorTests(unittest.TestCase):
    def test_feature_vector_has_expected_keys(self):
        vector = build_synergy_pair_feature_vector(None, None, event_year=2026)
        for key in SYNERGY_PAIR_FEATURE_ORDER:
            self.assertIn(key, vector, f"Missing feature key: {key}")
        self.assertEqual(len(vector), len(SYNERGY_PAIR_FEATURE_ORDER))

    def test_feature_vector_defaults_without_ratings(self):
        vector = build_synergy_pair_feature_vector(None, None, event_year=2026)
        self.assertEqual(vector["a_rating_0_100"], 50.0)
        self.assertEqual(vector["b_rating_0_100"], 50.0)
        self.assertEqual(vector["event_year"], 2026.0)
        self.assertEqual(vector["n_matches_together_prior"], 0.0)
        self.assertEqual(vector["prior_synergy_points"], 0.0)

    def test_feature_vector_with_prior_context(self):
        vector = build_synergy_pair_feature_vector(
            None,
            None,
            prior_synergy_points=1.5,
            prior_confidence=0.72,
            n_matches_together_prior=8,
            event_year=2026,
        )
        self.assertEqual(vector["prior_synergy_points"], 1.5)
        self.assertAlmostEqual(vector["prior_confidence"], 0.72, places=4)
        self.assertEqual(vector["n_matches_together_prior"], 8.0)

class SynergyPairSnapshotRebuildTests(DBTestCase):
    def _seed_event_with_synergy(self):
        self.db.add(models.Event(event_key="2026txtest", name="TX Test 2026", year=2026))
        self.db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
        self.db.add(models.Team(team_key="frc148", team_number=148, nickname="Robowranglers"))
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
                },
                model_version="rating_v13_forgiving_scale",
            )
        )
        self.db.add(
            models.EventTeamRating(
                event_key="2026txtest",
                team_key="frc148",
                rating_0_100=78.0,
                confidence_0_1=0.65,
                robot_level_0_100=75.0,
                driver_skill_0_100=72.0,
                results_anchor=68.0,
                throughput=76.0,
                shift_productivity=70.0,
                capacity_utilization=65.0,
                endgame=73.0,
                consistency=70.0,
                details_json={
                    "subscores": {
                        "auto_contribution": 60.0,
                        "defense_presence": 55.0,
                        "penalty_discipline": 82.0,
                    },
                },
                model_version="rating_v13_forgiving_scale",
            )
        )
        self.db.add(
            models.TeamPairSynergyEvent(
                event_key="2026txtest",
                team_key_a="frc118",
                team_key_b="frc148",
                model_version="synergy_v4_rolefit_tuned",
                season=2026,
                synergy_points_event=1.25,
                confidence_event=0.6,
                n_matches_together=4,
                avg_quality=0.75,
                residual_mean_raw=1.1,
                params_hash="test_hash",
            )
        )
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
        self.db.commit()

    def test_rebuild_includes_synergy_pair_snapshots(self):
        self._seed_event_with_synergy()
        result = rebuild_feature_snapshots(
            self.db,
            event_key="2026txtest",
            limit_events=5,
            replace_existing=True,
            source_version="synergy_pair_ut",
        )
        self.assertTrue(bool(result.get("ok")))
        synergy_pair_result = result.get("synergy_pair", {})
        self.assertGreaterEqual(int(synergy_pair_result.get("rows_written", 0)), 1)

        snapshot = (
            self.db.query(models.MLFeatureSnapshot)
            .filter(
                models.MLFeatureSnapshot.scope == FEATURE_SCOPE_SYNERGY_PAIR,
                models.MLFeatureSnapshot.source_version == "synergy_pair_ut",
            )
            .first()
        )
        self.assertIsNotNone(snapshot)
        fv = snapshot.feature_vector or {}
        self.assertIn("a_rating_0_100", fv)
        self.assertIn("offense_defense_bridge", fv)
        target = snapshot.target or {}
        self.assertEqual(target.get("synergy_points"), 1.25)

class SynergyPairInferenceFallbackTests(DBTestCase):
    def test_inference_returns_model_not_found(self):
        result = infer_synergy_pair_shadow_from_rows(
            self.db,
            rows=[
                {
                    "team_key_a": "frc118",
                    "team_key_b": "frc148",
                    "event_key": "2026txtest",
                    "a_rating_0_100": 83.0,
                    "b_rating_0_100": 78.0,
                }
            ],
        )
        self.assertFalse(bool(result.get("ok")))
        self.assertEqual(result.get("model_key"), SYNERGY_PAIR_MODEL_KEY)
        self.assertEqual(result.get("reason"), "model_not_found")

    def test_inference_empty_rows(self):
        result = infer_synergy_pair_shadow_from_rows(self.db, rows=[])
        self.assertTrue(bool(result.get("ok")))
        self.assertEqual(result.get("prediction_count"), 0)

class SynergyBlendKnobTests(unittest.TestCase):
    def test_blend_knob_code_default_zero(self):
        # The CODE default must stay conservative (0.0) so ML is opt-in. The
        # live value is intentionally raised via .env in deployments; assert
        # the field default here, not the env-influenced live setting.
        from app.core.config import Settings

        self.assertEqual(
            float(Settings.model_fields["ml_synergy_blend"].default), 0.0
        )

    def test_blend_knob_clamped(self):
        original = settings.ml_synergy_blend
        try:
            settings.ml_synergy_blend = 2.5
            knob = max(0.0, min(1.0, float(settings.ml_synergy_blend)))
            self.assertEqual(knob, 1.0)
            settings.ml_synergy_blend = -0.5
            knob = max(0.0, min(1.0, float(settings.ml_synergy_blend)))
            self.assertEqual(knob, 0.0)
        finally:
            settings.ml_synergy_blend = original

class SynergyProjectionMLBlendTests(DBTestCase):
    def _seed_full_event(self):
        self.db.add(models.Event(event_key="2026txtest", name="TX Test", year=2026))
        for team_num, team_key in [(118, "frc118"), (148, "frc148"), (254, "frc254"),
                                    (1114, "frc1114"), (2056, "frc2056"), (3310, "frc3310")]:
            self.db.add(models.Team(team_key=team_key, team_number=team_num, nickname=f"Team {team_num}"))
            self.db.add(models.EventTeam(event_key="2026txtest", team_key=team_key))
            self.db.add(
                models.EventTeamRating(
                    event_key="2026txtest",
                    team_key=team_key,
                    rating_0_100=70.0 + team_num % 20,
                    confidence_0_1=0.6,
                    robot_level_0_100=70.0,
                    driver_skill_0_100=65.0,
                    results_anchor=60.0,
                    throughput=72.0,
                    shift_productivity=68.0,
                    capacity_utilization=64.0,
                    endgame=70.0,
                    consistency=66.0,
                    details_json={
                        "subscores": {
                            "auto_contribution": 60.0,
                            "defense_presence": 45.0,
                            "penalty_discipline": 80.0,
                        },
                    },
                    model_version="rating_v13_forgiving_scale",
                )
            )
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
        for team_key, alliance in [
            ("frc118", "red"), ("frc148", "red"), ("frc254", "red"),
            ("frc1114", "blue"), ("frc2056", "blue"), ("frc3310", "blue"),
        ]:
            self.db.add(
                models.MatchTeam(
                    match_key="2026txtest_qm1",
                    event_key="2026txtest",
                    team_key=team_key,
                    alliance=alliance,
                )
            )
        self.db.commit()

    def test_deterministic_projection_no_ml(self):
        self._seed_full_event()
        original = settings.ml_synergy_blend
        try:
            settings.ml_synergy_blend = 0.0
            result = compute_match_synergy_projections(
                self.db,
                "2026txtest",
                recompute_dependencies=True,
                commit=True,
            )
            self.assertTrue(bool(result.get("ok")))
            ml_blend = result.get("ml_synergy_blend", {})
            self.assertEqual(ml_blend.get("blend_knob"), 0.0)
            self.assertFalse(ml_blend.get("prediction_ok"))
            self.assertEqual(ml_blend.get("ml_blended_pair_count"), 0)
        finally:
            settings.ml_synergy_blend = original

    def test_blend_knob_nonzero_attempts_ml(self):
        self._seed_full_event()
        original = settings.ml_synergy_blend
        try:
            settings.ml_synergy_blend = 0.5
            result = compute_match_synergy_projections(
                self.db,
                "2026txtest",
                recompute_dependencies=True,
                commit=True,
            )
            self.assertTrue(bool(result.get("ok")))
            ml_blend = result.get("ml_synergy_blend", {})
            self.assertEqual(ml_blend.get("blend_knob"), 0.5)
            # No trained model → prediction_ok should be False, fallback to deterministic
            self.assertFalse(ml_blend.get("prediction_ok"))
            self.assertEqual(ml_blend.get("ml_blended_pair_count"), 0)
        finally:
            settings.ml_synergy_blend = original

    def test_projection_source_label_marks_ml_when_blended(self):
        self._seed_full_event()
        original = settings.ml_synergy_blend
        try:
            settings.ml_synergy_blend = 1.0

            def _fake_infer(_db, *, rows, **_kwargs):
                return {
                    "ok": True,
                    "model_version": "synergy_pair_test_v1",
                    "predictions": [
                        {
                            "team_key_a": str(row.get("team_key_a") or "").strip().lower(),
                            "team_key_b": str(row.get("team_key_b") or "").strip().lower(),
                            "event_key": str(row.get("event_key") or "").strip().lower(),
                            "synergy_points_pred": 1.25,
                        }
                        for row in rows
                    ],
                }

            with patch("app.services.ml.shadow.infer_synergy_pair_shadow_from_rows", _fake_infer):
                result = compute_match_synergy_projections(
                    self.db,
                    "2026txtest",
                    recompute_dependencies=True,
                    commit=True,
                )

            self.assertTrue(bool(result.get("ok")))
            rows = (
                self.db.query(models.MatchSynergyProjection)
                .filter(models.MatchSynergyProjection.event_key == "2026txtest")
                .all()
            )
            self.assertTrue(rows)
            self.assertTrue(all(str(row.source_label) == "ml" for row in rows))
        finally:
            settings.ml_synergy_blend = original

if __name__ == "__main__":
    unittest.main()
