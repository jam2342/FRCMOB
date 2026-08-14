from __future__ import annotations

import unittest

from app.services.auto_scout.shift_play import (
    TrackPoint,
    analyze_match_shift_play,
    analyze_robot_shift_play,
)
from app.services.game_config import load_game_config

# Field-coordinate anchors for each zone (within the 16.541 x 8.0693 m field).
ZONE_XY = {
    "red_alliance_scoring_zone": (14.0, 4.0),
    "red_loading_depot_zone": (15.5, 2.0),
    "blue_alliance_scoring_zone": (2.0, 4.0),
    "neutral_transition_zone": (8.0, 4.0),
}

STEP = 0.5


def series(specs):
    # specs: list of (start_sec, end_sec, zone, speed). Coords come from ZONE_XY.
    points = []
    for start, end, zone, speed in specs:
        x, y = ZONE_XY[zone]
        t = start
        while t < end:
            points.append(TrackPoint(time_sec=round(t, 2), field_x=x, field_y=y, zone_key=zone, speed_mps=speed))
            t += STEP
    return points


def attacker_specs():
    # Red robot: in its scoring zone during attack-eligible windows, dipping to the
    # depot periodically (cycles); idle in neutral during opponent shifts.
    specs = []
    attack_windows = [(0, 20), (20, 30), (30, 55), (80, 105), (130, 160)]  # both + red shifts
    for ws, we in attack_windows:
        t = ws
        while t < we:
            # 4s scoring, 1s depot -> repeated round trips
            specs.append((t, min(t + 4, we), "red_alliance_scoring_zone", 1.1))
            specs.append((min(t + 4, we), min(t + 5, we), "red_loading_depot_zone", 1.1))
            t += 5
    specs.append((55, 80, "neutral_transition_zone", 0.2))
    specs.append((105, 130, "neutral_transition_zone", 0.2))
    return specs


def defender_specs():
    # Red robot: parks in the opponent scoring zone during blue-active shifts; idles
    # in neutral otherwise (little offense).
    return [
        (0, 55, "neutral_transition_zone", 0.2),
        (55, 80, "blue_alliance_scoring_zone", 0.9),
        (80, 105, "neutral_transition_zone", 0.2),
        (105, 130, "blue_alliance_scoring_zone", 0.9),
        (130, 160, "neutral_transition_zone", 0.2),
    ]


class ShiftPlayEngineTests(unittest.TestCase):
    def setUp(self):
        self.schedule = load_game_config().shift_schedule

    def test_attacker_scores_high_offense_low_defense(self):
        points = {"frc_atk": series(attacker_specs())}
        alliances = {"frc_atk": "red"}
        result = analyze_robot_shift_play(
            team_key="frc_atk", alliance="red", points_by_team=points,
            alliance_by_team=alliances, schedule=self.schedule,
        )
        self.assertGreaterEqual(result.offense_level_1_5, 4)
        self.assertLessEqual(result.defense_level_1_5, 2)
        self.assertGreaterEqual(result.metrics["cycles"], 2)
        self.assertGreater(sum(sum(row) for row in result.attack_heatmap), 0)
        self.assertGreater(result.offense_confidence, 0.5)

    def test_defender_scores_high_defense(self):
        # A blue opponent sits in its scoring zone during blue shifts -> defender shadows it.
        points = {
            "frc_def": series(defender_specs()),
            "frc_blue": series([(55, 80, "blue_alliance_scoring_zone", 0.8), (105, 130, "blue_alliance_scoring_zone", 0.8)]),
        }
        alliances = {"frc_def": "red", "frc_blue": "blue"}
        result = analyze_robot_shift_play(
            team_key="frc_def", alliance="red", points_by_team=points,
            alliance_by_team=alliances, schedule=self.schedule,
        )
        self.assertGreaterEqual(result.defense_level_1_5, 3)
        self.assertTrue(result.defense_assessable)
        self.assertGreater(result.metrics["opponent_zone_sec"], 20)
        self.assertGreater(result.metrics["shadow_sec"], 20)
        self.assertGreater(sum(sum(row) for row in result.defense_heatmap), 0)

    def test_idle_robot_scores_low_both(self):
        points = {"frc_idle": series([(0, 160, "neutral_transition_zone", 0.0)])}
        alliances = {"frc_idle": "red"}
        result = analyze_robot_shift_play(
            team_key="frc_idle", alliance="red", points_by_team=points,
            alliance_by_team=alliances, schedule=self.schedule,
        )
        self.assertLessEqual(result.offense_level_1_5, 2)
        self.assertLessEqual(result.defense_level_1_5, 2)

    def test_disruption_signal_when_opponent_scores_only_unengaged(self):
        # Defender engaged (in opp scoring zone) for first half of shift_2; opponent
        # scores only in the second half when the defender has left.
        points = {
            "frc_def": series([
                (0, 55, "neutral_transition_zone", 0.2),
                (55, 67, "blue_alliance_scoring_zone", 0.9),   # engaged
                (67, 160, "neutral_transition_zone", 0.2),     # unengaged
            ]),
            "frc_blue": series([
                (55, 67, "neutral_transition_zone", 0.5),       # not scoring while engaged
                (67, 80, "blue_alliance_scoring_zone", 0.7),    # scoring while unengaged
            ]),
        }
        alliances = {"frc_def": "red", "frc_blue": "blue"}
        result = analyze_robot_shift_play(
            team_key="frc_def", alliance="red", points_by_team=points,
            alliance_by_team=alliances, schedule=self.schedule,
        )
        self.assertGreater(result.metrics["disruption_0_1"], 0.5)

    def test_defense_not_assessable_without_opponent_windows_data(self):
        # Robot only tracked during its own/both windows -> no opponent-active data.
        points = {"frc_atk": series([(0, 30, "red_alliance_scoring_zone", 1.0), (30, 55, "red_alliance_scoring_zone", 1.0)])}
        result = analyze_robot_shift_play(
            team_key="frc_atk", alliance="red", points_by_team=points,
            alliance_by_team={"frc_atk": "red"}, schedule=self.schedule,
        )
        self.assertFalse(result.defense_assessable)
        self.assertEqual(result.defense_confidence, 0.0)

    def test_match_level_returns_entry_per_team(self):
        points = {
            "frc_atk": series(attacker_specs()),
            "frc_def": series(defender_specs()),
            "frc_blue": series([(55, 80, "blue_alliance_scoring_zone", 0.8)]),
        }
        alliances = {"frc_atk": "red", "frc_def": "red", "frc_blue": "blue"}
        results = analyze_match_shift_play(points_by_team=points, alliance_by_team=alliances, schedule=self.schedule)
        self.assertEqual(set(results), {"frc_atk", "frc_def", "frc_blue"})
        self.assertIn("offense", results["frc_atk"])
        self.assertIn("heatmaps", results["frc_atk"])


if __name__ == "__main__":
    unittest.main()
