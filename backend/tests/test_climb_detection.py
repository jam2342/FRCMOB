import unittest

from app.services.scoring.truth import _parse_2026_alliance_truth, _truth_context


class ClimbDetectionTests(unittest.TestCase):
    def test_2026_truth_parser_falls_back_to_endgame_robot_keys(self):
        rows = _parse_2026_alliance_truth(
            team_keys=["frc1", "frc2", "frc3"],
            alliance="red",
            breakdown={
                "totalAutoPoints": 12,
                "autoTowerPoints": 0,
                "totalTeleopPoints": 30,
                "endGameTowerPoints": 12,
                "endGameRobot1": "Level3",
                "endGameRobot2": "None",
                "endGameRobot3": "None",
            },
            context=_truth_context(),
        )

        self.assertEqual(len(rows), 3)
        by_team = {str(row["team_key"]): row for row in rows}

        self.assertTrue(by_team["frc1"]["climb_success"])
        self.assertGreater(float(by_team["frc1"]["climb_points"]), 0.0)
        self.assertEqual(by_team["frc1"]["status"]["endgame_tower"], "Level3")

        self.assertFalse(by_team["frc3"]["climb_success"])
        self.assertEqual(float(by_team["frc3"]["climb_points"]), 0.0)

    def test_2026_tba_robot_index_mapping_and_none_semantics(self):
        rows = _parse_2026_alliance_truth(
            team_keys=["frc111", "frc222", "frc333"],
            alliance="blue",
            breakdown={
                "totalAutoPoints": 6,
                "autoTowerPoints": 0,
                "totalTeleopPoints": 18,
                "endGameTowerPoints": 60,
                "endGameTowerRobot1": "Level3",
                "endGameTowerRobot2": "None",
                "endGameTowerRobot3": "Level1",
            },
            context=_truth_context(),
        )
        self.assertEqual(len(rows), 3)
        by_team = {str(row["team_key"]): row for row in rows}

        # Robot1/2/3 aligns with team_keys order in alliance payload.
        self.assertEqual(by_team["frc111"]["status"]["endgame_tower"], "Level3")
        self.assertEqual(by_team["frc222"]["status"]["endgame_tower"], "None")
        self.assertEqual(by_team["frc333"]["status"]["endgame_tower"], "Level1")

        # Per TBA 2026 semantics, any non-"None" status is a successful climb result.
        self.assertTrue(by_team["frc111"]["climb_success"])
        self.assertFalse(by_team["frc222"]["climb_success"])
        self.assertTrue(by_team["frc333"]["climb_success"])


if __name__ == "__main__":
    unittest.main()
