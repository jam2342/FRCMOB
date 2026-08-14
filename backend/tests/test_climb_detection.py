import unittest

from app.api.routes_events import _parse_2026_alliance_truth, _truth_context
from app.services.events.classifier import _zone_flags
from app.services.jobs import _compute_team_metrics_and_events


class ClimbDetectionTests(unittest.TestCase):
    def test_zone_flags_treat_cage_like_endgame(self):
        flags = _zone_flags("red_deep_cage_zone", "custom")
        self.assertEqual(flags["endgame"], 1.0)
        self.assertEqual(flags["tower"], 1.0)

    def test_metrics_detect_climb_from_cage_dwell(self):
        observations = []
        for frame_index, time_sec in enumerate([124.0, 128.0, 132.0, 136.0, 140.0, 145.0, 150.0, 155.0, 159.0]):
            observations.append(
                {
                    "track_id": 1,
                    "frame_index": frame_index,
                    "time_sec": time_sec,
                    "field_x": 1.0,
                    "field_y": 1.0,
                    "zone_key": "red_deep_cage_zone",
                    "zone_kind": "custom",
                    "speed_mps": 0.1,
                    "speed_px": 4.5,
                    "confidence": 0.9,
                }
            )

        metrics, events, _meta = _compute_team_metrics_and_events(
            team_key="frc1",
            observations=observations,
            duration_sec=160.0,
            auto_window_sec=20.0,
            endgame_window_sec=30.0,
            sample_interval_sec=2.0,
            event_model=None,
        )
        event_types = {str(row.get("event_type")) for row in events}
        self.assertEqual(metrics["climb_success_prob"], 1.0)
        self.assertIn("climb_attempt", event_types)
        self.assertIn("climb_success", event_types)

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
