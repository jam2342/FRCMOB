from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.game_config.schema import GameConfig, ShiftSchedule
from app.services.game_config import reload_game_config

_ROLE_ZONES = {
    "attack": {"red": "red_a", "blue": "blue_a"},
    "gather": {"red": "red_g", "blue": "blue_g"},
    "defense": {"red": "blue_a", "blue": "red_a"},
    "climb": {"red": "red_c", "blue": "blue_c"},
}


def _schedule(windows):
    return ShiftSchedule(countdown_based=True, windows=windows, role_zones=_ROLE_ZONES)


class ShiftScheduleModelTests(unittest.TestCase):
    def test_valid_contiguous_schedule(self):
        schedule = _schedule(
            [
                {"key": "auto", "start_sec": 0, "end_sec": 20, "active": "both"},
                {"key": "shift_1", "start_sec": 20, "end_sec": 45, "active": "red"},
            ]
        )
        self.assertEqual(len(schedule.windows), 2)

    def test_rejects_gap_between_windows(self):
        with self.assertRaises(ValidationError):
            _schedule(
                [
                    {"key": "auto", "start_sec": 0, "end_sec": 20, "active": "both"},
                    {"key": "shift_1", "start_sec": 30, "end_sec": 55, "active": "red"},
                ]
            )

    def test_rejects_schedule_not_starting_at_zero(self):
        with self.assertRaises(ValidationError):
            _schedule([{"key": "shift_1", "start_sec": 10, "end_sec": 30, "active": "red"}])

    def test_rejects_duplicate_window_keys(self):
        with self.assertRaises(ValidationError):
            _schedule(
                [
                    {"key": "dup", "start_sec": 0, "end_sec": 20, "active": "both"},
                    {"key": "dup", "start_sec": 20, "end_sec": 40, "active": "red"},
                ]
            )

    def test_rejects_window_with_end_le_start(self):
        with self.assertRaises(ValidationError):
            _schedule([{"key": "bad", "start_sec": 0, "end_sec": 0, "active": "both"}])


class ShiftScheduleConfigTests(unittest.TestCase):
    def test_real_2026_config_has_valid_shift_schedule(self):
        cfg = reload_game_config()
        schedule = cfg.shift_schedule
        self.assertIsNotNone(schedule)
        ordered = sorted(schedule.windows, key=lambda window: window.start_sec)
        self.assertEqual(ordered[0].start_sec, 0)
        self.assertEqual(ordered[-1].end_sec, cfg.phases.total_sec)
        # AUTO / transition / endgame are "both" (attack-only, never defense).
        by_key = {window.key: window for window in schedule.windows}
        self.assertEqual(by_key["auto"].active, "both")
        self.assertEqual(by_key["endgame"].active, "both")
        # role zones must reference real zone keys.
        zone_keys = {zone.key for zone in cfg.zones}
        self.assertIn(schedule.role_zones.defense.red, zone_keys)
        self.assertIn(schedule.role_zones.attack.blue, zone_keys)

    def test_config_rejects_role_zone_unknown_zone(self):
        raw = reload_game_config().model_dump()
        raw["shift_schedule"]["role_zones"]["defense"]["red"] = "nonexistent_zone"
        with self.assertRaises(ValidationError):
            GameConfig.model_validate(raw)

    def test_config_rejects_final_window_not_matching_total_sec(self):
        raw = reload_game_config().model_dump()
        raw["shift_schedule"]["windows"][-1]["end_sec"] = 999
        with self.assertRaises(ValidationError):
            GameConfig.model_validate(raw)

    def test_shift_schedule_is_optional(self):
        raw = reload_game_config().model_dump()
        raw["shift_schedule"] = None
        cfg = GameConfig.model_validate(raw)
        self.assertIsNone(cfg.shift_schedule)


if __name__ == "__main__":
    unittest.main()
