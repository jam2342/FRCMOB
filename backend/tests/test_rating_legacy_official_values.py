from __future__ import annotations

import unittest

from app.db.models import TeamMatchFinding
from app.services.ratings.feature_extraction import (
    _legacy_2026_official_allocation,
    _usable_batch_cycle_time,
)


class LegacyOfficialValueTests(unittest.TestCase):
    def _finding(self, *, source: str, allocation: str | None = None) -> TeamMatchFinding:
        return TeamMatchFinding(
            match_key="2026test_qm1",
            event_key="2026test",
            team_key="frc1",
            alliance="red",
            source=source,
            cycle_time_sec=1.5,
            auto_contribution=30.0,
            summary={"season_year": 2026, "status": {"auto_allocation": allocation}},
        )

    def test_legacy_official_values_are_not_robot_cycle_or_auto_evidence(self):
        finding = self._finding(source="tba_score_breakdown")
        self.assertFalse(_usable_batch_cycle_time(finding))
        self.assertTrue(_legacy_2026_official_allocation(finding))

    def test_new_copr_auto_is_usable_but_official_cycle_stays_unknown(self):
        finding = self._finding(source="tba_score_breakdown", allocation="event_auto_fuel_copr")
        self.assertFalse(_usable_batch_cycle_time(finding))
        self.assertFalse(_legacy_2026_official_allocation(finding))

    def test_measured_video_cycle_is_usable(self):
        finding = self._finding(source="video_v3_tracks")
        self.assertTrue(_usable_batch_cycle_time(finding))
        self.assertFalse(_legacy_2026_official_allocation(finding))
