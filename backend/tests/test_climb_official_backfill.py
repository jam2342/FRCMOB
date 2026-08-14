from __future__ import annotations

import unittest

from app.db import models
from app.services.climb.official_backfill import (
    backfill_official_climb_for_event,
    climb_signal_coverage,
)
from tests.conftest import DBTestCase


class _FakeTBA:
    def __init__(self, matches: list[dict]):
        self._matches = matches

    def event_matches(self, event_key: str):  # noqa: D401
        return list(self._matches)


def _seed_event_context(db):
    db.add(models.Event(event_key="2025txhou", name="Houston", year=2025))
    db.add(models.Match(match_key="2025txhou_qm1", event_key="2025txhou", comp_level="qm", set_number=1, match_number=1, time=1700000000))
    for number in (1, 2, 3, 4, 5, 6):
        team_key = f"frc{number}"
        db.add(models.Team(team_key=team_key, team_number=number, nickname=f"Team {number}"))
        if number <= 3:
            db.add(models.EventTeam(event_key="2025txhou", team_key=team_key))


class ClimbOfficialBackfillTests(DBTestCase):

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

    def test_backfill_updates_existing_findings_with_official_climb_fields(self):
        with self.SessionLocal() as db:
            _seed_event_context(db)
            run = models.AnalysisRun(match_key="2025txhou_qm1", version="video_v3_tracks", status="completed")
            db.add(run)
            db.flush()
            db.add(
                models.AnalysisRunContext(
                    run_id=run.id,
                    match_key="2025txhou_qm1",
                    event_key="2025txhou",
                    analysis_version="video_v3_tracks",
                    params_hash="test",
                    calibration_id=None,
                )
            )
            existing = models.TeamMatchFinding(
                analysis_run_id=run.id,
                match_key="2025txhou_qm1",
                event_key="2025txhou",
                team_key="frc1",
                alliance="red",
                station="r1",
                source="video_v3_tracks",
                climb_success_prob=0.1,
                summary={},
            )
            db.add(existing)
            db.commit()

            fake_match_payload = {
                "key": "2025txhou_qm1",
                "alliances": {
                    "red": {"team_keys": ["frc1", "frc2", "frc3"]},
                    "blue": {"team_keys": ["frc4", "frc5", "frc6"]},
                },
                "score_breakdown": {
                    "red": {
                        "autoPoints": 0,
                        "teleopPoints": 0,
                        "endGameRobot1": "DeepCage",
                        "endGameRobot2": "None",
                        "endGameRobot3": "None",
                    },
                    "blue": {
                        "autoPoints": 0,
                        "teleopPoints": 0,
                        "endGameRobot1": "None",
                        "endGameRobot2": "None",
                        "endGameRobot3": "None",
                    },
                },
            }

            result = backfill_official_climb_for_event(
                db,
                event_key="2025txhou",
                tba=_FakeTBA([fake_match_payload]),
            )
            db.commit()

            self.assertTrue(bool(result.get("ok")))
            self.assertGreaterEqual(int(result.get("updated_findings") or 0), 1)

            updated = (
                db.query(models.TeamMatchFinding)
                .filter(
                    models.TeamMatchFinding.event_key == "2025txhou",
                    models.TeamMatchFinding.match_key == "2025txhou_qm1",
                    models.TeamMatchFinding.team_key == "frc1",
                )
                .order_by(models.TeamMatchFinding.id.desc())
                .first()
            )
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertTrue(isinstance(updated.summary, dict))
            self.assertTrue(bool((updated.summary or {}).get("official_score_breakdown")))
            self.assertGreaterEqual(float(updated.climb_success_prob or 0.0), 1.0)


if __name__ == "__main__":
    unittest.main()
