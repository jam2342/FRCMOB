import unittest

from app.services import jobs


class RebuiltHubActivityWindowsTests(unittest.TestCase):
    def test_infers_shift1_active_alliance_from_auto_fuel_count(self):
        payload = {
            "score_breakdown": {
                "red": {"hubScore": {"autoCount": 14}},
                "blue": {"hubScore": {"autoCount": 9}},
            }
        }
        shift1_active_alliance, source = jobs._infer_rebuilt_shift1_active_alliance(payload)
        self.assertEqual(shift1_active_alliance, "blue")
        self.assertEqual(source, "auto_fuel_count")

    def test_rebuilt_windows_include_post_deactivate_grace(self):
        windows = jobs._rebuilt_hub_activity_windows_by_alliance(
            auto_end_sec=20.0,
            duration_sec=160.0,
            shift1_active_alliance="blue",
        )
        red_windows = windows["windows_attributed_by_alliance"]["red"]
        blue_windows = windows["windows_attributed_by_alliance"]["blue"]
        self.assertEqual(red_windows, [(20.0, 33.0), (55.0, 83.0), (105.0, 163.0)])
        self.assertEqual(blue_windows, [(20.0, 58.0), (80.0, 108.0), (130.0, 163.0)])
        self.assertAlmostEqual(float(windows["attributed_duration_by_alliance"]["red"]), 99.0, places=3)
        self.assertAlmostEqual(float(windows["attributed_duration_by_alliance"]["blue"]), 99.0, places=3)

    def test_expected_active_hub_duration_is_manual_aligned(self):
        duration = jobs._rebuilt_expected_active_hub_duration_sec(
            auto_end_sec=20.0,
            duration_sec=160.0,
            include_post_deactivate_grace=True,
        )
        self.assertAlmostEqual(float(duration), 99.0, places=3)

    def test_cycle_pairing_stays_within_active_windows(self):
        observations = [
            {
                "track_id": 1,
                "frame_index": 0,
                "time_sec": 20.0,
                "field_x": 1.0,
                "field_y": 1.0,
                "zone_key": "neutral_transition_zone",
                "zone_kind": "neutral",
                "speed_mps": 1.0,
                "confidence": 0.9,
            },
            {
                "track_id": 1,
                "frame_index": 1,
                "time_sec": 25.0,
                "field_x": 1.2,
                "field_y": 1.0,
                "zone_key": "red_loading_depot_zone",
                "zone_kind": "loading",
                "speed_mps": 1.2,
                "confidence": 0.9,
            },
            {
                "track_id": 1,
                "frame_index": 2,
                "time_sec": 40.0,
                "field_x": 1.5,
                "field_y": 1.1,
                "zone_key": "red_alliance_scoring_zone",
                "zone_kind": "scoring",
                "speed_mps": 1.1,
                "confidence": 0.9,
            },
            {
                "track_id": 1,
                "frame_index": 3,
                "time_sec": 50.0,
                "field_x": 1.3,
                "field_y": 1.2,
                "zone_key": "neutral_transition_zone",
                "zone_kind": "neutral",
                "speed_mps": 1.0,
                "confidence": 0.9,
            },
            {
                "track_id": 1,
                "frame_index": 4,
                "time_sec": 60.0,
                "field_x": 1.1,
                "field_y": 1.1,
                "zone_key": "red_loading_depot_zone",
                "zone_kind": "loading",
                "speed_mps": 1.1,
                "confidence": 0.9,
            },
            {
                "track_id": 1,
                "frame_index": 5,
                "time_sec": 70.0,
                "field_x": 1.6,
                "field_y": 1.1,
                "zone_key": "red_alliance_scoring_zone",
                "zone_kind": "scoring",
                "speed_mps": 1.1,
                "confidence": 0.9,
            },
            {
                "track_id": 1,
                "frame_index": 6,
                "time_sec": 78.0,
                "field_x": 1.4,
                "field_y": 1.0,
                "zone_key": "neutral_transition_zone",
                "zone_kind": "neutral",
                "speed_mps": 1.0,
                "confidence": 0.9,
            },
        ]
        active_windows = [(20.0, 33.0), (55.0, 83.0), (105.0, 163.0)]
        metrics, _events, _meta = jobs._compute_team_metrics_and_events(
            team_key="frc1",
            observations=observations,
            duration_sec=160.0,
            auto_window_sec=20.0,
            endgame_window_sec=30.0,
            sample_interval_sec=2.0,
            event_model=None,
            active_scoring_windows_sec=active_windows,
            active_scoring_duration_sec=99.0,
        )
        self.assertAlmostEqual(float(metrics["cycle_time_sec"] or 0.0), 10.0, places=3)


if __name__ == "__main__":
    unittest.main()
