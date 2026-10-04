from __future__ import annotations

from app.db import models
from app.services.events.ingest import upsert_score_breakdown_truth
from tests.conftest import DBTestCase


class ScoreBreakdownIngestSemanticsTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        assert self.db is not None
        self.db.add(models.Event(event_key="2026test", name="Test", year=2026))
        self.db.add(models.Match(match_key="2026test_qm1", event_key="2026test", comp_level="qm", set_number=1, match_number=1))
        for number in range(1, 7):
            self.db.add(models.Team(team_key=f"frc{number}", team_number=number, nickname=f"Team {number}"))
        self.db.flush()

    def _match(self) -> dict:
        return {
            "key": "2026test_qm1",
            "alliances": {
                "red": {"team_keys": ["frc1", "frc2", "frc3"], "score": 301},
                "blue": {"team_keys": ["frc4", "frc5", "frc6"], "score": 250},
            },
            "score_breakdown": {
                "red": {"hubScore": {"autoPoints": 90, "teleopPoints": 150, "teleopCount": 150}},
                "blue": {"hubScore": {"autoPoints": 0, "teleopPoints": 90, "teleopCount": 90}},
            },
        }

    def test_copr_estimates_auto_and_does_not_invent_batch_cycle_time(self):
        assert self.db is not None
        old_run = models.AnalysisRun(
            match_key="2026test_qm1",
            version="tba_score_breakdown_v1",
            run_kind="official_truth",
            status="completed",
        )
        self.db.add(old_run)
        self.db.flush()
        self.db.add(models.TeamMatchFinding(
            analysis_run_id=old_run.id,
            match_key="2026test_qm1",
            event_key="2026test",
            team_key="frc1",
            alliance="red",
            source="tba_score_breakdown",
            cycle_time_sec=1.5,
            auto_contribution=30.0,
        ))
        self.db.flush()
        self.db.expunge(old_run)
        result = upsert_score_breakdown_truth(
            self.db,
            event_key="2026test",
            season_year=2026,
            matches=[self._match()],
            auto_fuel_copr_by_team={
                "frc1": 60, "frc2": 30, "frc3": 0,
                "frc4": 10, "frc5": 10, "frc6": 10,
            },
        )
        self.db.flush()
        self.assertEqual(result["cleared_runs"], 1)
        self.assertEqual(result["inserted_findings"], 6)
        rows = {row.team_key: row for row in self.db.query(models.TeamMatchFinding).all()}
        self.assertEqual([rows[key].auto_contribution for key in ("frc1", "frc2", "frc3")], [60.0, 30.0, 0.0])
        self.assertTrue(all(row.cycle_time_sec is None for row in rows.values()))
        self.assertEqual(rows["frc1"].summary["status"]["auto_allocation"], "event_auto_fuel_copr")
        self.assertIsNotNone(rows["frc1"].fuel_scoring_rate)
        auto_event = self.db.query(models.MatchEvent).filter_by(team_key="frc1", event_type="auto_points_scored").one()
        self.assertEqual(auto_event.meta["estimated_points"], 60.0)
        self.assertNotIn("official_points", auto_event.meta)

    def test_official_final_scores_are_persisted_for_outcome_labels(self):
        assert self.db is not None
        upsert_score_breakdown_truth(
            self.db,
            event_key="2026test",
            season_year=2026,
            matches=[self._match()],
        )
        self.db.flush()
        finding = self.db.query(models.TeamMatchFinding).first()
        self.assertEqual(finding.summary["red_score"], 301)
        self.assertEqual(finding.summary["blue_score"], 250)

    def test_missing_copr_leaves_auto_unknown(self):
        assert self.db is not None
        upsert_score_breakdown_truth(
            self.db,
            event_key="2026test",
            season_year=2026,
            matches=[self._match()],
        )
        self.db.flush()
        rows = {row.team_key: row for row in self.db.query(models.TeamMatchFinding).all()}
        self.assertIsNone(rows["frc1"].auto_contribution)
        self.assertEqual(rows["frc1"].summary["status"]["auto_allocation"], "unknown")
        self.assertEqual(rows["frc4"].auto_contribution, 0.0)

    def test_zero_output_robot_still_counts_as_an_official_match(self):
        assert self.db is not None
        match = self._match()
        match["score_breakdown"]["blue"]["hubScore"] = {"autoPoints": 0, "teleopPoints": 0, "teleopCount": 0}
        result = upsert_score_breakdown_truth(
            self.db,
            event_key="2026test",
            season_year=2026,
            matches=[match],
        )
        self.db.flush()
        self.assertEqual(result["inserted_findings"], 6)
        blue = self.db.query(models.TeamMatchFinding).filter_by(team_key="frc4").one()
        self.assertEqual(blue.climb_success_prob, 0.0)
        self.assertIsNone(blue.cycle_time_sec)
