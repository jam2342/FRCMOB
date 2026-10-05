from __future__ import annotations

import unittest

from app.services.intel.snapshots import _merge_missing_fields_only


class IntelSnapshotBackfillTests(unittest.TestCase):
    def test_fresh_values_win_and_the_previous_snapshot_fills_gaps(self):
        existing = {
            "team": {"team_key": "frc118", "nickname": "Robonauts"},
            "rating": {"available": True, "rating_0_100": 62.1},
            "tba": {"rank": 4},
            "teams": ["frc118"],
        }
        fresh = {
            "team": {"team_key": "frc118", "nickname": "Robonauts 2"},
            "rating": {"available": True, "rating_0_100": 68.9},
            "tba": {"rank": None},
            "teams": ["frc118", "frc254"],
        }
        merged, filled = _merge_missing_fields_only(existing, fresh)
        self.assertEqual(merged["team"]["nickname"], "Robonauts 2")
        self.assertEqual(merged["rating"]["rating_0_100"], 68.9)
        self.assertEqual(merged["teams"], ["frc118", "frc254"])
        # TBA didn't answer this time: keep the last known rank.
        self.assertEqual(merged["tba"]["rank"], 4)
        self.assertEqual(filled, 1)


if __name__ == "__main__":
    unittest.main()
