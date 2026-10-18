from __future__ import annotations

import unittest

from app.db import models
from app.services.climb.coverage import climb_signal_coverage
from tests.conftest import DBTestCase


def _seed_event_context(db):
    db.add(models.Event(event_key="2025txhou", name="Houston", year=2025))
    db.add(models.Match(match_key="2025txhou_qm1", event_key="2025txhou", comp_level="qm", set_number=1, match_number=1, time=1700000000))
    for number in (1, 2, 3, 4, 5, 6):
        team_key = f"frc{number}"
        db.add(models.Team(team_key=team_key, team_number=number, nickname=f"Team {number}"))
        if number <= 3:
            db.add(models.EventTeam(event_key="2025txhou", team_key=team_key))


class ClimbSignalCoverageTests(DBTestCase):

    def test_climb_signal_coverage_counts_official_marker_from_summary(self):
        with self.SessionLocal() as db:
            _seed_event_context(db)
            run = models.AnalysisRun(match_key="2025txhou_qm1", version="video_v3_tracks", status="completed")
            db.add(run)
            db.flush()
            db.add(
                models.TeamMatchFinding(
                    analysis_run_id=run.id,
                    match_key="2025txhou_qm1",
                    event_key="2025txhou",
                    team_key="frc1",
                    alliance="red",
                    station="r1",
                    source="video_v3_tracks",
                    climb_success_prob=1.0,
                    summary={"official_score_breakdown": True, "status": {"endgame": "Level2"}},
                )
            )
            db.add(
                models.TeamMatchFinding(
                    analysis_run_id=run.id,
                    match_key="2025txhou_qm1",
                    event_key="2025txhou",
                    team_key="frc2",
                    alliance="red",
                    station="r2",
                    source="video_v3_tracks",
                    climb_success_prob=0.55,
                    summary={"analysis_context": {"tracking_backend": "yolo_bytetrack"}},
                )
            )
            db.commit()

            payload = climb_signal_coverage(db, event_key="2025txhou")
            summary = payload.get("summary") or {}
            self.assertEqual(int(summary.get("teams_total") or 0), 3)
            self.assertEqual(int(summary.get("teams_with_any_signal") or 0), 2)
            self.assertEqual(int(summary.get("teams_with_official_signal") or 0), 1)
            self.assertEqual(int(summary.get("teams_with_video_signal") or 0), 1)

if __name__ == "__main__":
    unittest.main()
