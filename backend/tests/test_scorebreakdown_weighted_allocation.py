from __future__ import annotations

import unittest

from app.api.routes_events import _parse_2026_alliance_truth, _truth_context


class ScoreBreakdownWeightedAllocationTests(unittest.TestCase):
    def test_2026_parser_uses_team_weights_for_teleop_shares(self):
        rows = _parse_2026_alliance_truth(
            team_keys=["frc1", "frc2", "frc3"],
            alliance="red",
            breakdown={
                "hubScore": {
                    "teleopCount": 60,
                    "teleopPoints": 120,
                },
                "totalTeleopPoints": 120,
            },
            context=_truth_context(),
            team_weight_by_key={"frc1": 4.0, "frc2": 1.0, "frc3": 1.0},
        )
        by_team = {str(row["team_key"]): row for row in rows}

        self.assertGreater(
            float(by_team["frc1"]["teleop_score_count"] or 0.0),
            float(by_team["frc2"]["teleop_score_count"] or 0.0),
        )
        self.assertGreater(
            float(by_team["frc1"]["teleop_score_count"] or 0.0),
            float(by_team["frc3"]["teleop_score_count"] or 0.0),
        )
        total_count = sum(float(row.get("teleop_score_count") or 0.0) for row in rows)
        self.assertAlmostEqual(total_count, 60.0, places=3)

    def test_2026_parser_caps_count_to_points_when_count_exceeds_active_points(self):
        rows = _parse_2026_alliance_truth(
            team_keys=["frc1", "frc2", "frc3"],
            alliance="red",
            breakdown={
                "hubScore": {
                    "teleopCount": 90,
                    "teleopPoints": 60,
                },
                "totalTeleopPoints": 60,
            },
            context=_truth_context(),
            team_weight_by_key=None,
        )
        total_count = sum(float(row.get("teleop_score_count") or 0.0) for row in rows)
        self.assertAlmostEqual(total_count, 60.0, places=3)
        self.assertTrue(
            all(bool(((row.get("status") or {}).get("teleop_count_capped_to_points"))) for row in rows)
        )


if __name__ == "__main__":
    unittest.main()
