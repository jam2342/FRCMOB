from __future__ import annotations

import unittest

from app.services.intel.snapshots import _merge_missing_fields_only


class IntelSnapshotBackfillTests(unittest.TestCase):
    def test_merge_missing_fields_only_does_not_overwrite_existing_values(self):
        existing = {
            "team": {"team_key": "frc118", "nickname": "Robonauts"},
            "rating": {"available": True, "rating_0_100": 62.1},
            "analysis": {"averages": {"fuel_scoring_rate": None}},
        }
        fresh = {
            "team": {"team_key": "frc118", "nickname": "Different Nickname"},
            "rating": {"available": True, "rating_0_100": 68.9},
            "analysis": {"averages": {"fuel_scoring_rate": 0.92}},
        }
        merged, filled = _merge_missing_fields_only(existing, fresh)
        self.assertEqual(merged["team"]["nickname"], "Robonauts")
        self.assertEqual(merged["rating"]["rating_0_100"], 62.1)
        self.assertEqual(merged["analysis"]["averages"]["fuel_scoring_rate"], 0.92)
        self.assertEqual(filled, 1)


if __name__ == "__main__":
    unittest.main()
