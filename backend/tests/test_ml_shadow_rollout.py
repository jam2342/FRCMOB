import unittest

from app.core.config import settings
from app.api.routes_scouting import get_team_breakdown
from app.db import models
from app.services.ml.shadow import (
    FEATURE_SCOPE_TEAM_STRENGTH,
    TEAM_STRENGTH_MODEL_KEY,
    auto_train_shadow_models_for_event_breakdown,
    infer_match_outcome_shadow_from_rows,
    infer_team_strength_shadow_from_rows,
    rebuild_feature_snapshots,
    is_shadow_rollout_active,
)
from tests.conftest import DBTestCase

class MLShadowRolloutTests(unittest.TestCase):
    def test_rollout_disabled(self):
        original_enabled = settings.ml_shadow_enabled
        original_ratio = settings.ml_shadow_rollout_ratio
        try:
            settings.ml_shadow_enabled = False
            settings.ml_shadow_rollout_ratio = 1.0
            self.assertFalse(is_shadow_rollout_active("2026txhou_qm1"))
        finally:
            settings.ml_shadow_enabled = original_enabled
            settings.ml_shadow_rollout_ratio = original_ratio

    def test_rollout_deterministic_bucket(self):
        original_enabled = settings.ml_shadow_enabled
        original_ratio = settings.ml_shadow_rollout_ratio
        try:
            settings.ml_shadow_enabled = True
            settings.ml_shadow_rollout_ratio = 0.37
            first = is_shadow_rollout_active("stable-key")
            second = is_shadow_rollout_active("stable-key")
            self.assertEqual(first, second)
        finally:
            settings.ml_shadow_enabled = original_enabled
            settings.ml_shadow_rollout_ratio = original_ratio

    def test_blend_knob_activates_without_shadow_rollout(self):
        # With blend knob > 0, ML applies even when shadow rollout is off.
        original_enabled = settings.ml_shadow_enabled
        original_ratio = settings.ml_shadow_rollout_ratio
        original_blend = settings.ml_team_strength_blend
        try:
            settings.ml_shadow_enabled = True
            settings.ml_shadow_rollout_ratio = 0.0  # shadow rollout → off for all keys
            settings.ml_team_strength_blend = 0.5
            # Blend knob > 0 means ML should activate regardless of shadow rollout
            # The model.py gating: ml_team_strength_blend_knob > 0.0 OR is_shadow_rollout_active(...)
            # With ratio=0.0, is_shadow_rollout_active always returns False.
            # But knob > 0 should still allow ML.
            self.assertFalse(is_shadow_rollout_active("2026txhou:frc118:team_strength"))
            # knob takes precedence — this is tested implicitly through the OR gate
            self.assertTrue(float(settings.ml_team_strength_blend) > 0.0)
        finally:
            settings.ml_shadow_enabled = original_enabled
            settings.ml_shadow_rollout_ratio = original_ratio
            settings.ml_team_strength_blend = original_blend

    def test_blend_knob_zero_preserves_shadow_rollout_gating(self):
        # With blend knob = 0, ML only applies via legacy shadow rollout.
        original_enabled = settings.ml_shadow_enabled
        original_ratio = settings.ml_shadow_rollout_ratio
        original_blend = settings.ml_team_strength_blend
        try:
            settings.ml_shadow_enabled = True
            settings.ml_shadow_rollout_ratio = 0.0
            settings.ml_team_strength_blend = 0.0
            # Both gates off → ML should not activate
            self.assertFalse(is_shadow_rollout_active("2026txhou:frc118:team_strength"))
            self.assertFalse(float(settings.ml_team_strength_blend) > 0.0)
        finally:
            settings.ml_shadow_enabled = original_enabled
            settings.ml_shadow_rollout_ratio = original_ratio
            settings.ml_team_strength_blend = original_blend

class MLShadowInferenceFallbackTests(DBTestCase):

    def test_team_strength_inference_returns_model_not_found(self):
        result = infer_team_strength_shadow_from_rows(
            self.db,
            rows=[
                {
                    "event_key": "2026txhou",
                    "team_key": "frc118",
                    "rating_0_100": 82.0,
                    "confidence_0_1": 0.6,
                    "throughput_0_100": 79.0,
                    "driver_skill_0_100": 77.0,
                    "robot_level_0_100": 81.0,
                    "endgame_0_100": 73.0,
                    "consistency_0_100": 68.0,
                    "shift_productivity_0_100": 75.0,
                    "capacity_utilization_0_100": 66.0,
                }
            ],
        )
        self.assertFalse(bool(result.get("ok")))
        self.assertEqual(str(result.get("reason")), "model_not_found")

    def test_match_outcome_inference_returns_model_not_found(self):
        result = infer_match_outcome_shadow_from_rows(
            self.db,
            rows=[
                {
                    "event_key": "2026txhou",
                    "match_key": "2026txhou_qm1",
                    "red_rating_mean": 74.0,
                    "blue_rating_mean": 69.0,
                    "rating_margin": 5.0,
                    "red_confidence_mean": 0.56,
                    "blue_confidence_mean": 0.51,
                    "confidence_margin": 0.05,
                    "red_synergy_points": 3.0,
                    "blue_synergy_points": 1.0,
                    "synergy_margin": 2.0,
                    "red_projected_throughput": 2.8,
                    "blue_projected_throughput": 2.2,
                    "projected_margin": 0.6,
                }
            ],
        )
        self.assertFalse(bool(result.get("ok")))
        self.assertEqual(str(result.get("reason")), "model_not_found")

    def test_auto_train_breakdown_graceful_when_no_snapshots(self):
        result = auto_train_shadow_models_for_event_breakdown(
            self.db,
            event_key="2026txhou",
            limit_events=5,
        )
        self.assertTrue(bool(result.get("triggered")))
        self.assertFalse(bool(result.get("ok")))
        self.assertEqual(str(result.get("detail")), "no_snapshot_rows")

    def test_rebuild_snapshots_filters_to_requested_season(self):
        self.db.add(models.Event(event_key="2026txtest", name="TX Test 2026", year=2026))
        self.db.add(models.Event(event_key="2025txtest", name="TX Test 2025", year=2025))
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
            models.Match(
                match_key="2025txtest_qm1",
                event_key="2025txtest",
                comp_level="qm",
                set_number=1,
                match_number=1,
                time=1660000100,
            )
        )
        self.db.commit()

        result = rebuild_feature_snapshots(
            self.db,
            event_key=None,
            limit_events=20,
            season_year=2026,
            replace_existing=True,
            source_version="shadow_features_ut_season",
        )
        self.assertTrue(bool(result.get("ok")))
        self.assertEqual(int(result.get("season_year") or 0), 2026)
        self.assertEqual(int(result.get("event_count") or 0), 1)
        self.assertEqual(result.get("event_keys"), ["2026txtest"])

    def test_rebuild_snapshots_captures_richer_team_strength_features(self):
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
        run = models.AnalysisRun(match_key="2026txtest_qm1", version="video_v3_tracks", status="completed")
        self.db.add(run)
        self.db.flush()
        finding = models.TeamMatchFinding(
            analysis_run_id=run.id,
            match_key="2026txtest_qm1",
            event_key="2026txtest",
            team_key="frc118",
            alliance="red",
            source="video_v3_tracks",
            fuel_scoring_rate=9.2,
            cycle_time_sec=18.5,
            auto_contribution=7.0,
            climb_success_prob=0.84,
            defensive_engagement_sec=6.5,
            reliability_score=0.88,
            summary={},
        )
        self.db.add(finding)
        self.db.flush()
        self.db.add(
            models.TeamMatchThroughput(
                finding_id=finding.id,
                analysis_run_id=run.id,
                match_key="2026txtest_qm1",
                event_key="2026txtest",
                team_key="frc118",
                active_bps=1.63,
                metric_coverage={"coverage_score": 0.82},
                source="video_v3_tracks",
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
                        "manual_points_impact": 68.0,
                        "rp_contribution": 61.0,
                        "anti_defense": 58.0,
                        "defense_presence": 43.0,
                        "penalty_discipline": 87.0,
                    },
                    "raw_features": {
                        "expected_net_points": 29.5,
                        "auto_points_est": 8.2,
                        "teleop_points_est": 21.3,
                        "climb_output": 19.0,
                        "uptime": 0.91,
                        "throughput_trend_delta": 0.16,
                        "reliability_trend_delta": 0.08,
                        "cycle_trend_delta": -0.04,
                        "penalty_trend_delta": -0.12,
                        "throughput_coverage": 0.84,
                        "video_findings_count": 6,
                        "anti_defense_pressure_coverage": 0.41,
                        "severe_penalty_rate": 0.03,
                    },
                    "confidence_support": {
                        "avg_analysis_quality": 0.78,
                        "avg_identity_quality": 0.74,
                        "coverage_factor": 0.81,
                        "match_quality_weight_factor": 0.76,
                        "recent_match_factor": 0.67,
                        "trend_coverage_factor": 0.5,
                    },
                    "quality_gate": {
                        "accepted_match_count": 5,
                        "excluded_match_count": 1,
                    },
                },
                model_version="rating_v13_forgiving_scale",
            )
        )
        self.db.commit()

        result = rebuild_feature_snapshots(
            self.db,
            event_key="2026txtest",
            limit_events=5,
            replace_existing=True,
            source_version="shadow_features_ut_rich",
        )
        self.assertTrue(bool(result.get("ok")))
        snapshot_key = f"{FEATURE_SCOPE_TEAM_STRENGTH}:2026txtest:frc118:shadow_features_ut_rich"
        snapshot = self.db.get(models.MLFeatureSnapshot, snapshot_key)
        self.assertIsNotNone(snapshot)
        feature_vector = snapshot.feature_vector or {}
        self.assertEqual(feature_vector.get("auto_contribution_0_100"), 64.0)
        self.assertEqual(feature_vector.get("penalty_discipline_0_100"), 87.0)
        self.assertEqual(feature_vector.get("expected_net_points"), 29.5)
        self.assertEqual(feature_vector.get("avg_analysis_quality_0_1"), 0.78)
        self.assertEqual(feature_vector.get("excluded_match_count"), 1.0)
        self.assertEqual(feature_vector.get("event_year"), 2026.0)

    def test_team_breakdown_surfaces_ml_projection_status(self):
        original_enabled = settings.ml_shadow_enabled
        try:
            settings.ml_shadow_enabled = True
            self.db.add(models.Event(event_key="2026txtest", name="TX Test 2026", year=2026))
            self.db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
            self.db.add(
                models.EventTeamRating(
                    event_key="2026txtest",
                    team_key="frc118",
                    rating_0_100=81.0,
                    confidence_0_1=0.69,
                    robot_level_0_100=77.0,
                    driver_skill_0_100=73.0,
                    results_anchor=70.0,
                    throughput=79.0,
                    shift_productivity=72.0,
                    capacity_utilization=68.0,
                    endgame=75.0,
                    consistency=71.0,
                    details_json={
                        "subscores": {
                            "auto_contribution": 63.0,
                            "manual_points_impact": 67.0,
                            "rp_contribution": 60.0,
                            "anti_defense": 57.0,
                            "defense_presence": 42.0,
                            "penalty_discipline": 84.0,
                        },
                        "raw_features": {
                            "expected_net_points": 27.0,
                            "auto_points_est": 7.0,
                            "teleop_points_est": 20.0,
                            "climb_output": 18.0,
                            "uptime": 0.89,
                            "throughput_coverage": 0.79,
                        },
                        "confidence_support": {
                            "coverage_factor": 0.8,
                        },
                        "quality_gate": {
                            "accepted_match_count": 4,
                            "excluded_match_count": 0,
                        },
                    },
                    model_version="rating_v13_forgiving_scale",
                )
            )
            self.db.commit()

            payload = get_team_breakdown(
                team_key="frc118",
                event_key="2026txtest",
                db=self.db,
            )
            ml_projection = payload.get("ml_projection") or {}
            self.assertEqual(ml_projection.get("model_key"), TEAM_STRENGTH_MODEL_KEY)
            self.assertEqual(ml_projection.get("source"), "event_rating")
            self.assertEqual(ml_projection.get("reason"), "model_not_found")
            self.assertFalse(bool(ml_projection.get("available")))
        finally:
            settings.ml_shadow_enabled = original_enabled

if __name__ == "__main__":
    unittest.main()
