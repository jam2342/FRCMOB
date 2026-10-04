from __future__ import annotations

from datetime import datetime, timezone

from app.db import models
from app.services.auto_scout.shift_play import summarize_team_shift_play
from tests.conftest import DBTestCase


class TeamShiftPlaySummaryTests(DBTestCase):
    def _seed_attacker(self, status: str = "accepted"):
        db = self.db
        db.add(models.Event(event_key="2026txhou", name="Houston", year=2026))
        db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
        db.add(models.Match(match_key="2026txhou_qm1", event_key="2026txhou", comp_level="qm", set_number=1, match_number=1, time=1700000000))
        db.add(models.MatchTeam(match_key="2026txhou_qm1", team_key="frc118", event_key="2026txhou", alliance="red", station="r1"))
        run = models.AnalysisRun(
            match_key="2026txhou_qm1",
            version="on_device_pwa_v1",
            run_kind="on_device",
            status="completed",
            created_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        db.add(models.AnalysisRunContext(
            run_id=run.id,
            match_key="2026txhou_qm1",
            event_key="2026txhou",
            analysis_version="on_device_pwa_v1",
            params_hash="team-shift-test",
        ))
        db.add(models.OnDeviceSession(
            analysis_run_id=run.id,
            match_key="2026txhou_qm1",
            event_key="2026txhou",
            principal_hash="principal",
            client_session_id_hash="client",
            shift1_active_alliance="red",
            quality_score=0.9,
            status=status,
        ))

        def add(t, zone, x, spd):
            db.add(models.RobotTrack(
                analysis_run_id=run.id, match_key="2026txhou_qm1", event_key="2026txhou", team_key="frc118",
                track_id=1, frame_index=int(t * 2), time_sec=t, bbox_x1=0.0, bbox_y1=0.0, bbox_x2=1.0, bbox_y2=1.0,
                centroid_x=0.0, centroid_y=0.0, field_x=x, field_y=4.0, zone_key=zone, speed_mps=spd,
                confidence=None, source="on_device_pwa_v1",
            ))

        # Attacker: in own scoring zone during attack-eligible windows.
        for ws, we in [(0, 20), (30, 55), (80, 105), (130, 160)]:
            t = ws
            while t < we:
                add(t, "red_alliance_scoring_zone", 14.0, 1.1)
                t += 2
        db.commit()
        return run

    def test_unreviewed_recording_is_not_summarised(self):
        self._seed_attacker(status="provisional")
        res = summarize_team_shift_play(self.db, team_key="frc118", event_key="2026txhou")
        self.assertFalse(res["available"])

    def test_summary_available_for_attacker(self):
        self._seed_attacker()
        res = summarize_team_shift_play(self.db, team_key="frc118", event_key="2026txhou")
        self.assertTrue(res["available"])
        self.assertEqual(res["sample_matches"], 1)
        self.assertGreaterEqual(res["offense"]["level_1_5"], 3)
        self.assertIsNotNone(res["attack_heatmap"])
        self.assertEqual(res["attack_heatmap"]["grid_rows"], 6)
        self.assertEqual(res["attack_heatmap"]["grid_cols"], 12)
        self.assertGreater(res["attack_heatmap"]["total_points"], 0)
        # No opponent-active tracking seeded -> defense not assessable.
        self.assertFalse(res["defense"]["assessable"])

    def test_summary_unavailable_when_no_runs(self):
        db = self.db
        db.add(models.Event(event_key="2026txhou", name="Houston", year=2026))
        db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
        db.add(models.Match(match_key="2026txhou_qm1", event_key="2026txhou", comp_level="qm", set_number=1, match_number=1, time=1700000000))
        db.add(models.MatchTeam(match_key="2026txhou_qm1", team_key="frc118", event_key="2026txhou", alliance="red", station="r1"))
        db.commit()
        res = summarize_team_shift_play(self.db, team_key="frc118", event_key="2026txhou")
        self.assertFalse(res["available"])
        self.assertEqual(res["sample_matches"], 0)
        self.assertIsNone(res["attack_heatmap"])


if __name__ == "__main__":
    import unittest

    unittest.main()
