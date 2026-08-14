from __future__ import annotations

import unittest

from app.api import routes_scouting
from app.db import models


class FindingDedupePriorityTests(unittest.TestCase):
    def test_prefers_video_source_over_official_for_same_match(self):
        official = models.TeamMatchFinding(
            id=10,
            match_key="2026tx_match1",
            team_key="frc1",
            source="tba_score_breakdown",
        )
        video = models.TeamMatchFinding(
            id=9,
            match_key="2026tx_match1",
            team_key="frc1",
            source="video_v3_tracks",
        )
        rows = [
            (official, 1000),
            (video, 1000),
        ]

        deduped = routes_scouting._dedupe_finding_rows_by_match(rows)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0][0].source, "video_v3_tracks")

    def test_prefers_newer_id_when_source_priority_equal(self):
        older = models.TeamMatchFinding(
            id=11,
            match_key="2026tx_match2",
            team_key="frc1",
            source="video_v3_tracks",
        )
        newer = models.TeamMatchFinding(
            id=12,
            match_key="2026tx_match2",
            team_key="frc1",
            source="video_v3_tracks",
        )
        rows = [
            (older, 1001),
            (newer, 1001),
        ]

        deduped = routes_scouting._dedupe_finding_rows_by_match(rows)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(int(deduped[0][0].id or 0), 12)


if __name__ == "__main__":
    unittest.main()
