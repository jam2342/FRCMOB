# Regressions found in the 2026-10-04 QA pass (official ingest, fuel predictions, phone
# recordings feeding drafts, heatmaps). Each failed before its fix.
from types import SimpleNamespace
from unittest.mock import patch

from app.api import routes_events, routes_tracks
from app.db import models
from app.services.events.ingest import upsert_score_breakdown_truth
from app.services.events.fuel_win_probability import fuel_margins_by_match
from app.services.ml.synergy import SYNERGY_MODEL_VERSION
from app.services.auto_scout.scouting import generate_auto_scout_draft, summarize_team_auto_scout_profile
from app.services.auto_scout.shift_play import analyze_run_shift_play
from app.services.analysis.evidence import evidence_rejection_reason
from tests.conftest import DBTestCase

RED = ["frc1", "frc2", "frc3"]
BLUE = ["frc4", "frc5", "frc6"]


class QAPipelineRegressionTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self.db.add(models.Event(event_key="2026qa", name="QA", year=2026))
        for number in range(1, 7):
            self.db.add(models.Team(team_key=f"frc{number}", team_number=number))
        for number, time in ((1, 10000), (2, 10030), (3, 10020)):
            match_key = f"2026qa_qm{number}"
            self.db.add(models.Match(match_key=match_key, event_key="2026qa", comp_level="qm", set_number=1, match_number=number, time=time))
            for alliance, teams in (("red", RED), ("blue", BLUE)):
                for station, team in enumerate(teams, 1):
                    self.db.add(models.MatchTeam(match_key=match_key, event_key="2026qa", team_key=team, alliance=alliance, station=f"{alliance[0]}{station}"))
        self.db.commit()

    def _official_match(self, number, red_fuel, blue_fuel):
        return {
            "key": f"2026qa_qm{number}",
            "alliances": {
                "red": {"team_keys": RED, "score": red_fuel},
                "blue": {"team_keys": BLUE, "score": blue_fuel},
            },
            "score_breakdown": {
                "red": {"hubScore": {"autoPoints": 0, "teleopPoints": red_fuel, "teleopCount": red_fuel}},
                "blue": {"hubScore": {"autoPoints": 0, "teleopPoints": blue_fuel, "teleopCount": blue_fuel}},
            },
        }

    def _ingest(self, matches):
        upsert_score_breakdown_truth(self.db, event_key="2026qa", season_year=2026, matches=matches)
        self.db.commit()

    def test_official_zero_fuel_is_a_known_zero_not_missing(self):
        self._ingest([self._official_match(1, 0, 90)])
        finding = self.db.query(models.TeamMatchFinding).filter_by(match_key="2026qa_qm1", team_key="frc1").one()
        self.assertEqual(finding.fuel_scoring_rate, 0.0)

    def test_zero_fuel_history_still_enables_fuel_prediction(self):
        self._ingest([self._official_match(1, 0, 90)])
        margins = fuel_margins_by_match(self.db, "2026qa", [("2026qa_qm1", RED, BLUE), ("2026qa_qm3", RED, BLUE)], event_start=10000)
        self.assertIn("2026qa_qm3", margins)
        self.assertLess(margins["2026qa_qm3"], 0.0)

    def test_later_postponed_match_does_not_inform_earlier_prediction(self):
        self._ingest([self._official_match(2, 180, 60)])
        payload = routes_events.get_event_schedule_with_synergy(
            "2026qa", SimpleNamespace(), synergy_model_version=SYNERGY_MODEL_VERSION,
            include_pair_breakdown=False, include_ml_predictions=True, refresh=False, db=self.db,
        )
        match = next(row for row in payload["matches"] if row["match_key"] == "2026qa_qm3")
        self.assertEqual(match["prediction"]["source_label"], "deterministic_rating_synergy_v1")

    def test_rejected_nonofficial_finding_cannot_suppress_official_ingest(self):
        self.assertIsNotNone(evidence_rejection_reason({"video_evidence": {"ratings_eligible": False}}))
        run = models.AnalysisRun(match_key="2026qa_qm1", run_kind="video", version="legacy", status="completed")
        self.db.add(run)
        self.db.flush()
        self.db.add(models.TeamMatchFinding(
            analysis_run_id=run.id, match_key="2026qa_qm1", event_key="2026qa", team_key="frc1",
            alliance="red", source="video_yolo", fuel_scoring_rate=999,
            summary={"video_evidence": {"ratings_eligible": False}},
        ))
        self.db.commit()
        self._ingest([self._official_match(1, 90, 60)])
        official = self.db.query(models.TeamMatchFinding).filter_by(match_key="2026qa_qm1", team_key="frc1", source="tba_score_breakdown").first()
        self.assertIsNotNone(official)

    def _phone_run(self, quality=0.9, x=2.0):
        run = models.AnalysisRun(match_key="2026qa_qm1", run_kind="on_device", version="on_device_pwa_v1", status="completed")
        self.db.add(run)
        self.db.flush()
        session = models.OnDeviceSession(
            analysis_run_id=run.id, match_key=run.match_key, event_key="2026qa",
            principal_hash=f"qa-{run.id}", client_session_id_hash=f"qa-{run.id}",
            quality_score=quality, status="accepted", shift1_active_alliance="red",
        )
        self.db.add(session)
        self.db.add(models.AnalysisRunContext(
            run_id=run.id, match_key=run.match_key, event_key="2026qa", analysis_version=run.version, params_hash=f"qa-{run.id}",
        ))
        for frame in range(480):
            self.db.add(models.RobotTrack(
                analysis_run_id=run.id, match_key=run.match_key, event_key="2026qa", team_key="frc1",
                track_id=1, frame_index=frame, time_sec=frame / 3, field_x=x, field_y=3,
                bbox_x1=0, bbox_y1=0, bbox_x2=0, bbox_y2=0, centroid_x=0, centroid_y=0,
                zone_key="red_alliance_scoring_zone", source="on_device_pwa_v1",
            ))
        self.db.commit()
        return run, session

    def test_position_only_recording_does_not_invent_zero_fuel_counts(self):
        self._phone_run()
        draft, _ = generate_auto_scout_draft(self.db, event_key="2026qa", match_key="2026qa_qm1", team_key="frc1")
        form = draft.draft_payload["form_patch"]
        self.assertNotIn("auto_scored", form, f"Position-only input fabricated scoring: {form}")
        self.assertNotIn("teleop_scored", form)

    def test_recorder_and_auto_draft_use_same_offense_assessment(self):
        run, _session = self._phone_run()
        shift = analyze_run_shift_play(self.db, run_id=run.id)["frc1"]
        draft, _ = generate_auto_scout_draft(self.db, event_key="2026qa", match_key="2026qa_qm1", team_key="frc1")
        self.assertEqual(draft.draft_payload["form_patch"]["offense_level_1_5"], shift["offense"]["level_1_5"])

    def test_untracked_opponents_do_not_produce_auto_filled_defense_level(self):
        run, _session = self._phone_run()
        shift = analyze_run_shift_play(self.db, run_id=run.id)["frc1"]
        self.assertFalse(shift["defense"]["assessable"])
        draft, _ = generate_auto_scout_draft(self.db, event_key="2026qa", match_key="2026qa_qm1", team_key="frc1")
        self.assertNotIn("defense_level_1_5", draft.draft_payload["form_patch"])

    def test_rejecting_recording_removes_machine_owned_robot_profile(self):
        _run, session = self._phone_run()
        generate_auto_scout_draft(self.db, event_key="2026qa", match_key="2026qa_qm1", team_key="frc1")
        def profile():
            return summarize_team_auto_scout_profile(self.db, team_key="frc1", event_key="2026qa", season_year=2026)

        self.assertTrue(profile()["available"])
        with patch.object(routes_tracks, "require_admin_access"), patch.object(routes_tracks, "require_write_access"):
            routes_tracks.review_on_device_session(session.id, routes_tracks.OnDeviceSessionReviewRequest(status="rejected"), SimpleNamespace(), self.db)
        self.assertEqual(session.status, "rejected")
        self.assertFalse(profile()["available"])

    def test_heatmap_uses_one_best_recording_per_match_like_shift_play(self):
        self._phone_run(quality=0.9, x=2)
        self._phone_run(quality=0.6, x=14)
        heatmap = routes_tracks.get_team_heatmap(
            "frc1", SimpleNamespace(), event_key="2026qa", match_key=None,
            grid_cols=48, grid_rows=24, sigma=0.6, include_unreviewed=False, db=self.db,
        )
        self.assertEqual(heatmap["match_count"], 1)
        self.assertEqual(heatmap["total_points"], 480)
