import unittest

try:
    from app.services.jobs import (
        _score_breakdown_cycle_fallbacks_from_match_payload,
        _teleop_count_and_points_from_breakdown,
    )

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - optional dependency guarded import
    _IMPORT_ERROR = exc


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional deps unavailable: {_IMPORT_ERROR}")
class CycleTimeFallbackTests(unittest.TestCase):
    def test_extracts_2026_hub_count_and_points(self):
        count, points, source = _teleop_count_and_points_from_breakdown(
            {
                "hubScore": {
                    "teleopCount": 24,
                    "teleopPoints": 72,
                },
                "totalTeleopPoints": 90,
                "endGameTowerPoints": 18,
            }
        )
        self.assertEqual(count, 24.0)
        self.assertEqual(points, 72.0)
        self.assertEqual(source, "official_count")

    def test_caps_count_to_points_when_count_likely_includes_inactive_hub_scores(self):
        count, points, source = _teleop_count_and_points_from_breakdown(
            {
                "hubScore": {
                    "teleopCount": 30,
                    "teleopPoints": 18,
                },
            }
        )
        self.assertEqual(count, 18.0)
        self.assertEqual(points, 18.0)
        self.assertEqual(source, "official_count_capped_to_points")

    def test_builds_team_fallbacks_from_official_count(self):
        payload = {
            "alliances": {
                "red": {"team_keys": ["frc1", "frc2", "frc3"]},
                "blue": {"team_keys": ["frc4", "frc5", "frc6"]},
            },
            "score_breakdown": {
                "red": {"hubScore": {"teleopCount": 18, "teleopPoints": 54}},
                "blue": {"hubScore": {"teleopCount": 9, "teleopPoints": 27}},
            },
        }
        fallbacks = _score_breakdown_cycle_fallbacks_from_match_payload(
            match_payload=payload,
            teleop_duration_sec=110.0,
        )
        self.assertIn("frc1", fallbacks)
        red_team = fallbacks["frc1"]
        self.assertAlmostEqual(float(red_team["score_count_estimate"]), 6.0, places=3)
        self.assertAlmostEqual(float(red_team["cycle_time_sec"]), 110.0 / 6.0, places=3)
        self.assertEqual(red_team["count_source"], "official_count")
        self.assertEqual(red_team["source"], "tba_score_breakdown_api")

    def test_uses_points_proxy_when_counts_missing(self):
        payload = {
            "alliances": {
                "red": {"team_keys": ["frc11", "frc22", "frc33"]},
            },
            "score_breakdown": {
                "red": {
                    "teleopPoints": 60,
                    "endGameTowerPoints": 0,
                },
            },
        }
        fallbacks = _score_breakdown_cycle_fallbacks_from_match_payload(
            match_payload=payload,
            teleop_duration_sec=120.0,
        )
        self.assertIn("frc11", fallbacks)
        team_payload = fallbacks["frc11"]
        # 60 alliance teleop points / 3 teams = 20 pts per team, 1 pt per score (REBUILT fuel) => 20 cycles.
        self.assertAlmostEqual(float(team_payload["score_count_estimate"]), 20.0, places=3)
        self.assertAlmostEqual(float(team_payload["cycle_time_sec"]), 6.0, places=3)
        self.assertEqual(team_payload["count_source"], "official_points_proxy")

    def test_weighted_allocation_prefers_higher_prior_team(self):
        payload = {
            "alliances": {
                "red": {"team_keys": ["frc1", "frc2", "frc3"]},
            },
            "score_breakdown": {
                "red": {
                    "hubScore": {"teleopCount": 30, "teleopPoints": 60},
                },
            },
        }
        fallbacks = _score_breakdown_cycle_fallbacks_from_match_payload(
            match_payload=payload,
            teleop_duration_sec=120.0,
            team_weight_by_key={
                "frc1": 4.0,
                "frc2": 1.0,
                "frc3": 1.0,
            },
        )
        self.assertIn("frc1", fallbacks)
        self.assertIn("frc2", fallbacks)
        self.assertIn("frc3", fallbacks)
        score_frc1 = float(fallbacks["frc1"]["score_count_estimate"])
        score_frc2 = float(fallbacks["frc2"]["score_count_estimate"])
        score_frc3 = float(fallbacks["frc3"]["score_count_estimate"])
        self.assertGreater(score_frc1, score_frc2)
        self.assertGreater(score_frc1, score_frc3)
        self.assertAlmostEqual(score_frc1 + score_frc2 + score_frc3, 30.0, places=2)
        self.assertEqual(fallbacks["frc1"]["allocation_method"], "weighted_team_prior")


if __name__ == "__main__":
    unittest.main()
