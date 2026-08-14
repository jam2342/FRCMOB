import unittest
from types import SimpleNamespace

try:
    from app.services.ratings.model import (
        _apply_sparse_rating_guard,
        _ensure_minimum_pros_cons_signals,
        _make_signal,
        _trend_delta_ratio,
        _weighted_median,
    )
    from app.services.ml.synergy import compute_pair_role_adjustment
    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - dependency-gated test import
    _IMPORT_ERROR = exc


def _dummy_rating(
    *,
    throughput: float,
    shift: float,
    auto: float,
    endgame: float,
    defense: float,
    consistency: float,
    discipline: float,
    confidence: float,
    epa_percentile: float | None = None,
):
    details_json = {
        "subscores": {
            "throughput": throughput,
            "shift_productivity": shift,
            "auto_contribution": auto,
            "endgame": endgame,
            "defense_presence": defense,
            "consistency": consistency,
            "penalty_discipline": discipline,
        }
    }
    if isinstance(epa_percentile, (int, float)):
        details_json["epa_context"] = {"percentile": float(epa_percentile)}

    return SimpleNamespace(
        throughput=throughput,
        shift_productivity=shift,
        endgame=endgame,
        consistency=consistency,
        confidence_0_1=confidence,
        details_json=details_json,
    )


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional scoring deps unavailable: {_IMPORT_ERROR}")
class RatingDepthHelperTests(unittest.TestCase):
    def test_weighted_median(self):
        value = _weighted_median([(1.0, 1.0), (2.0, 4.0), (9.0, 1.0)])
        self.assertEqual(value, 2.0)

    def test_trend_delta_ratio_positive_and_negative(self):
        improving = _trend_delta_ratio([8.0, 7.5, 7.0, 6.0, 5.5, 5.0])
        regressing = _trend_delta_ratio([5.0, 5.3, 5.8, 6.1, 6.5, 7.0])
        self.assertIsNotNone(improving)
        self.assertIsNotNone(regressing)
        self.assertGreater(improving, 0.0)
        self.assertLess(regressing, 0.0)

    def test_minimum_pros_cons_signals_are_backfilled(self):
        findings = [
            SimpleNamespace(
                match_key="m1",
                summary={},
                fuel_scoring_rate=1.2,
                auto_contribution=7.0,
                climb_success_prob=0.5,
                defensive_engagement_sec=18.0,
                reliability_score=0.82,
                cycle_time_sec=11.4,
            )
        ]
        pros, cons = _ensure_minimum_pros_cons_signals(
            pros=[],
            cons=[],
            confidence=0.52,
            findings=findings,
            use_fallback_model=False,
            throughput=66.0,
            auto_contribution_score=73.0,
            endgame=34.0,
            defense_presence_score=41.0,
            anti_defense_score=39.0,
            penalty_discipline=62.0,
        )
        self.assertGreaterEqual(len(pros), 2)
        self.assertGreaterEqual(len(cons), 2)
        self.assertTrue(all(isinstance(item.get("label"), str) and item.get("label") for item in pros))
        self.assertTrue(all(isinstance(item.get("label"), str) and item.get("label") for item in cons))

    def test_existing_signal_lists_are_preserved_when_already_deep(self):
        seed_pros = [
            {"label": "Seed Pro A", "percentile": 88.0, "evidence": []},
            {"label": "Seed Pro B", "percentile": 82.0, "evidence": []},
        ]
        seed_cons = [
            {"label": "Seed Con A", "percentile": 24.0, "evidence": []},
            {"label": "Seed Con B", "percentile": 28.0, "evidence": []},
        ]
        pros, cons = _ensure_minimum_pros_cons_signals(
            pros=list(seed_pros),
            cons=list(seed_cons),
            confidence=0.61,
            findings=[],
            use_fallback_model=True,
            throughput=55.0,
            auto_contribution_score=55.0,
            endgame=55.0,
            defense_presence_score=55.0,
            anti_defense_score=55.0,
            penalty_discipline=55.0,
        )
        self.assertEqual(len(pros), len(seed_pros))
        self.assertEqual(len(cons), len(seed_cons))
        self.assertEqual(pros[0]["label"], "Seed Pro A")
        self.assertEqual(cons[0]["label"], "Seed Con A")

    def test_make_signal_includes_rule_based_evidence_fields(self):
        signal = _make_signal(
            "Strong autonomous impact",
            7.4,
            78.0,
            [{"match_key": "m1", "metric": "auto_contribution", "value": 7.4}],
            signal_confidence=0.62,
        )
        self.assertEqual(signal["metric"], "auto_contribution")
        self.assertIn("delta", signal)
        self.assertIn("confidence_0_1", signal)
        self.assertIn("sample_size", signal)
        self.assertIn("rule_id", signal)

    def test_sparse_rating_guard_caps_sparse_data_ceiling(self):
        guarded, details = _apply_sparse_rating_guard(
            raw_final_rating=96.0,
            confidence=0.22,
            matches_observed=1,
            video_findings_count=0,
            use_fallback_model=True,
        )
        self.assertLessEqual(guarded, 80.0)
        self.assertTrue(details["applied"])
        self.assertEqual(details["video_findings_count"], 0)


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional synergy deps unavailable: {_IMPORT_ERROR}")
class SynergyRoleAdjustmentTests(unittest.TestCase):
    def test_returns_zero_when_ratings_missing(self):
        payload = compute_pair_role_adjustment({}, "frc1", "frc2")
        self.assertEqual(payload["source"], "no_ratings")
        self.assertEqual(payload["profile_coverage_0_1"], 0.0)
        self.assertEqual(payload["net_adjustment_points"], 0.0)

    def test_complementary_pair_beats_risky_overlap_pair(self):
        complementary_map = {
            "frc1": _dummy_rating(
                throughput=92.0,
                shift=90.0,
                auto=78.0,
                endgame=82.0,
                defense=26.0,
                consistency=83.0,
                discipline=88.0,
                confidence=0.82,
            ),
            "frc2": _dummy_rating(
                throughput=55.0,
                shift=58.0,
                auto=70.0,
                endgame=75.0,
                defense=93.0,
                consistency=79.0,
                discipline=85.0,
                confidence=0.79,
            ),
        }
        overlap_map = {
            "frc3": _dummy_rating(
                throughput=35.0,
                shift=32.0,
                auto=30.0,
                endgame=34.0,
                defense=90.0,
                consistency=28.0,
                discipline=24.0,
                confidence=0.76,
            ),
            "frc4": _dummy_rating(
                throughput=30.0,
                shift=31.0,
                auto=28.0,
                endgame=35.0,
                defense=92.0,
                consistency=27.0,
                discipline=22.0,
                confidence=0.73,
            ),
        }

        complementary = compute_pair_role_adjustment(complementary_map, "frc1", "frc2")
        overlap = compute_pair_role_adjustment(overlap_map, "frc3", "frc4")

        self.assertGreater(complementary["net_adjustment_points"], overlap["net_adjustment_points"])
        self.assertGreater(complementary["profile_coverage_0_1"], 0.0)

    def test_partial_coverage_when_one_rating_missing(self):
        rating_map = {
            "frc9": _dummy_rating(
                throughput=70.0,
                shift=68.0,
                auto=66.0,
                endgame=69.0,
                defense=55.0,
                consistency=74.0,
                discipline=72.0,
                confidence=0.7,
            )
        }
        payload = compute_pair_role_adjustment(rating_map, "frc9", "frc404")
        self.assertEqual(payload["profile_coverage_0_1"], 0.5)
        self.assertEqual(payload["source"], "event_ratings")

    def test_epa_context_boosts_pair_adjustment(self):
        high_epa_map = {
            "frc11": _dummy_rating(
                throughput=70.0,
                shift=68.0,
                auto=66.0,
                endgame=69.0,
                defense=55.0,
                consistency=74.0,
                discipline=72.0,
                confidence=0.7,
                epa_percentile=90.0,
            ),
            "frc12": _dummy_rating(
                throughput=70.0,
                shift=68.0,
                auto=66.0,
                endgame=69.0,
                defense=55.0,
                consistency=74.0,
                discipline=72.0,
                confidence=0.7,
                epa_percentile=88.0,
            ),
        }
        low_epa_map = {
            "frc21": _dummy_rating(
                throughput=70.0,
                shift=68.0,
                auto=66.0,
                endgame=69.0,
                defense=55.0,
                consistency=74.0,
                discipline=72.0,
                confidence=0.7,
                epa_percentile=10.0,
            ),
            "frc22": _dummy_rating(
                throughput=70.0,
                shift=68.0,
                auto=66.0,
                endgame=69.0,
                defense=55.0,
                consistency=74.0,
                discipline=72.0,
                confidence=0.7,
                epa_percentile=12.0,
            ),
        }

        high = compute_pair_role_adjustment(high_epa_map, "frc11", "frc12")
        low = compute_pair_role_adjustment(low_epa_map, "frc21", "frc22")
        self.assertGreater(high["net_adjustment_points"], low["net_adjustment_points"])


if __name__ == "__main__":
    unittest.main()
