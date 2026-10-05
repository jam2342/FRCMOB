from __future__ import annotations

import unittest

from app.services.scouting_rooms.helpers import rows_for_metric_coverage


class MetricCoverageGateTests(unittest.TestCase):
    def test_rejected_rows_are_not_shown_in_their_place(self):
        rows, coverage = rows_for_metric_coverage([("video",)], [], {"enabled": True, "raw_count": 1, "accepted_count": 0})
        self.assertEqual(rows, [])
        self.assertEqual(coverage["source"], "quality_gate_rejected_all")
        self.assertFalse(coverage["fallback_used"])

    def test_accepted_rows_and_disabled_gate(self):
        rows, coverage = rows_for_metric_coverage([("a",), ("b",)], [("a",)], {"enabled": True})
        self.assertEqual(rows, [("a",)])
        self.assertEqual(coverage["source"], "quality_gate_accepted")
        rows, coverage = rows_for_metric_coverage([("a",), ("b",)], [], {"enabled": False})
        self.assertEqual(rows, [("a",), ("b",)])
        self.assertEqual(coverage["source"], "quality_gate_disabled")


if __name__ == "__main__":
    unittest.main()
