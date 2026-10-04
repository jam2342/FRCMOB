from __future__ import annotations

import unittest

from app.services.events.ingest import extract_2026_auto_fuel_coprs
from scripts.refresh_2026_official_truth import eligible_match_payloads


class RefreshOfficialTruthTests(unittest.TestCase):
    def test_only_stored_matches_with_complete_coprs_are_ready(self):
        matches = [
            {
                "key": "2026test_qm1",
                "alliances": {"red": {"team_keys": ["frc1", "frc2", "frc3"]}},
                "score_breakdown": {"red": {"hubScore": {"autoPoints": 30}}},
            },
            {"key": "2026test_qm2", "alliances": {}, "score_breakdown": {}},
        ]
        coprs = extract_2026_auto_fuel_coprs({
            "Hub Auto Fuel Count": {"frc1": 20.0, "frc2": -2.0, "frc3": float("nan")},
        })
        eligible, missing = eligible_match_payloads(
            matches,
            stored_match_keys={"2026test_qm1"},
            auto_fuel_coprs=coprs,
        )
        self.assertEqual([match["key"] for match in eligible], ["2026test_qm1"])
        self.assertEqual(missing, {"frc3"})

    def test_all_zero_coprs_do_not_overwrite_positive_auto_match(self):
        matches = [{
            "key": "2026test_qm1",
            "alliances": {"red": {"team_keys": ["frc1", "frc2", "frc3"]}},
            "score_breakdown": {"red": {"hubScore": {"autoPoints": 30}}},
        }]
        _, missing = eligible_match_payloads(
            matches,
            stored_match_keys={"2026test_qm1"},
            auto_fuel_coprs={"frc1": 0.0, "frc2": -1.0, "frc3": 0.0},
        )
        self.assertEqual(missing, {"frc1", "frc2", "frc3"})
