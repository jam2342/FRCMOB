from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.db import models
from app.services.auto_scout import scouting as auto_scouting
from tests.conftest import DBTestCase


def _seed_core_entities(db) -> tuple[str, str, str]:
    event_key = "2026txhou"
    match_key = "2026txhou_qm1"
    team_key = "frc118"
    db.add(models.Event(event_key=event_key, name="Houston", year=2026))
    db.add(models.Team(team_key=team_key, team_number=118, nickname="Robonauts"))
    db.add(models.EventTeam(event_key=event_key, team_key=team_key))
    db.add(models.Match(match_key=match_key, event_key=event_key, comp_level="qm", set_number=1, match_number=1, time=1700000000))
    db.add(models.MatchTeam(match_key=match_key, team_key=team_key, event_key=event_key, alliance="red", station="r1"))
    db.commit()
    return event_key, match_key, team_key


def _seed_analysis_rows(
    db,
    *,
    event_key: str,
    match_key: str,
    team_key: str,
    run_version: str = "video_v3_tracks",
    created_at: datetime | None = None,
) -> models.AnalysisRun:
    calibration = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key == match_key)
        .one_or_none()
    )
    if calibration is None:
        calibration = models.FieldCalibration(
            match_key=match_key,
            event_key=event_key,
            frame_time_sec=12.0,
            image_width=1280,
            image_height=720,
            image_points=[{"x": 0.0, "y": 0.0}, {"x": 1279.0, "y": 0.0}, {"x": 1279.0, "y": 719.0}, {"x": 0.0, "y": 719.0}],
            field_points=[{"x": 0.0, "y": 0.0}, {"x": 16.541, "y": 0.0}, {"x": 16.541, "y": 8.0693}, {"x": 0.0, "y": 8.0693}],
            homography=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        )
        db.add(calibration)
        db.flush()

    run = models.AnalysisRun(
        match_key=match_key,
        version=run_version,
        status="completed",
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    db.add(
        models.AnalysisRunContext(
            run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            analysis_version=run_version,
            params_hash=f"params-{run.id}",
            calibration_id=calibration.id,
            created_at=created_at or datetime.now(timezone.utc),
        )
    )
    finding = models.TeamMatchFinding(
        analysis_run_id=run.id,
        match_key=match_key,
        event_key=event_key,
        team_key=team_key,
        alliance="red",
        station="r1",
        source=run_version,
        fuel_scoring_rate=0.88,
        cycle_time_sec=10.0,
        auto_contribution=6.2,
        climb_success_prob=0.4,
        defensive_engagement_sec=11.5,
        reliability_score=0.91,
        summary={"sampling": {"detections": 60}},
    )
    db.add(finding)
    db.flush()
    db.add(
        models.TeamMatchThroughput(
            finding_id=finding.id,
            analysis_run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            team_key=team_key,
            balls_shot_total=12,
            shooting_time_total_seconds=26.0,
            bps_total=0.46,
            balls_shot_active=10,
            shooting_time_active_seconds=21.0,
            active_bps=0.48,
            metric_coverage={"coverage_score": 0.84, "has_cycle_time": True},
            source=run_version,
        )
    )
    db.add(
        models.AnalysisQuality(
            run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            calibration_quality_score=0.92,
            tracking_quality_score=0.9,
            identity_quality_score=0.88,
            overall_quality_score=0.9,
            details={"coverage_quality": {"avg_coverage_score_0_1": 0.84}},
        )
    )
    db.flush()

    track_rows = [
        (0.0, "red_loading_depot_zone", 1.2),
        (5.0, "neutral_transition_zone", 1.1),
        (10.0, "neutral_transition_zone", 1.0),
        (15.0, "red_alliance_scoring_zone", 0.8),
        (20.0, "red_alliance_scoring_zone", 0.6),
        (35.0, "neutral_transition_zone", 0.7),
        (40.0, "neutral_transition_zone", 0.05),
        (45.0, "neutral_transition_zone", 0.02),
        (50.0, "neutral_transition_zone", 0.04),
        (60.0, "red_loading_depot_zone", 0.9),
        (80.0, "neutral_transition_zone", 1.1),
        (100.0, "red_alliance_scoring_zone", 0.7),
        (120.0, "red_tower_endgame_zone", 0.4),
    ]
    for index, (time_sec, zone_key, speed_mps) in enumerate(track_rows, start=1):
        db.add(
            models.RobotTrack(
                analysis_run_id=run.id,
                match_key=match_key,
                event_key=event_key,
                team_key=team_key,
                track_id=1,
                frame_index=index,
                time_sec=time_sec,
                bbox_x1=10.0,
                bbox_y1=10.0,
                bbox_x2=80.0,
                bbox_y2=80.0,
                centroid_x=45.0,
                centroid_y=45.0,
                field_x=5.0 + index,
                field_y=2.0,
                zone_key=zone_key,
                speed_mps=speed_mps,
                confidence=0.95,
                source=run_version,
            )
        )

    db.add(
        models.MatchEvent(
            analysis_run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            team_key=team_key,
            track_id=1,
            frame_index=2,
            time_sec=3.5,
            event_type="auto_mobility",
            confidence=0.82,
            field_x=3.0,
            field_y=2.0,
            meta={"distance_m": 2.1},
        )
    )
    db.add(
        models.MatchEvent(
            analysis_run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            team_key=team_key,
            track_id=1,
            frame_index=8,
            time_sec=44.0,
            event_type="protected_zone_interference",
            confidence=0.71,
            field_x=7.0,
            field_y=4.0,
            meta={},
        )
    )
    db.commit()
    db.refresh(run)
    return run


class AutoScoutingServiceTests(DBTestCase):
    def test_generate_ready_auto_scout_draft(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)

        row, created = auto_scouting.generate_auto_scout_draft(
            self.db,
            event_key=event_key,
            match_key=match_key,
            team_key=team_key,
        )

        self.assertTrue(created)
        self.assertEqual(row.status, "ready")
        self.assertEqual(row.mapper_version, "2026_v2")
        form_patch = (row.draft_payload or {}).get("form_patch", {})
        self.assertTrue(form_patch.get("auto_mobility"))
        self.assertEqual(form_patch.get("auto_scored"), 0)
        self.assertEqual(form_patch.get("auto_missed"), 0)
        self.assertEqual(form_patch.get("teleop_scored"), 12)
        self.assertEqual(form_patch.get("teleop_cycles"), 14)
        self.assertIn("teleop_under_defense_scored", form_patch)
        self.assertIn("offense_level_1_5", form_patch)
        self.assertIn("defense_level_1_5", form_patch)
        self.assertIn("field_awareness_1_5", form_patch)
        self.assertIn("decision_quality_1_5", form_patch)
        self.assertIn("intake_failures", form_patch)
        self.assertEqual(form_patch.get("foul_count"), 1)
        self.assertEqual(form_patch.get("endgame_mode"), "parked")
        self.assertIn("disabled_period", (row.draft_payload or {}).get("derived_insights", {}))
        self.assertIn("cycle_pace_summary", (row.draft_payload or {}).get("derived_insights", {}))
        self.assertEqual((row.field_provenance or {}).get("auto_mobility"), "auto")
        self.assertEqual((row.field_provenance or {}).get("teleop_scored"), "auto")
        self.assertEqual((row.field_provenance or {}).get("offense_level_1_5"), "auto")
        self.assertEqual((row.field_provenance or {}).get("defense_level_1_5"), "auto")
        self.assertEqual((row.field_provenance or {}).get("endgame_mode"), "auto")
        self.assertGreater(float((row.field_confidence or {}).get("auto_mobility") or 0.0), 0.7)
        self.assertGreater(float((row.field_confidence or {}).get("teleop_scored") or 0.0), 0.7)
        self.assertEqual(row.missing_reasons or [], [])

    def test_generate_failed_when_analysis_cannot_queue(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        with patch("app.services.auto_scout.scouting._queue_analysis_if_possible", return_value=(False, "no_calibration")):
            row, _ = auto_scouting.generate_auto_scout_draft(
                self.db,
                event_key=event_key,
                match_key=match_key,
                team_key=team_key,
            )
        self.assertEqual(row.status, "failed")
        self.assertIn("no_calibration", row.missing_reasons or [])

    def test_approve_records_field_overrides(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)
        row, _ = auto_scouting.generate_auto_scout_draft(
            self.db,
            event_key=event_key,
            match_key=match_key,
            team_key=team_key,
        )

        approved_row, overrides = auto_scouting.approve_auto_scout_draft(
            self.db,
            draft_id=row.id,
            draft_version=row.draft_version,
            approved_by="Jamal",
            edited_payload={
                "form_patch": {
                    "auto_mobility": False,
                },
                "notes_seed": "",
                "derived_insights": (row.draft_payload or {}).get("derived_insights", {}),
            },
        )

        self.assertEqual(approved_row.status, "approved")
        self.assertEqual((approved_row.field_overrides or {}).get("auto_mobility", {}).get("to"), False)
        self.assertEqual(overrides.get("auto_mobility", {}).get("from"), True)

    def test_approve_rejects_stale_version(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)
        row, _ = auto_scouting.generate_auto_scout_draft(
            self.db,
            event_key=event_key,
            match_key=match_key,
            team_key=team_key,
        )

        with self.assertRaises(HTTPException) as context:
            auto_scouting.approve_auto_scout_draft(
                self.db,
                draft_id=row.id,
                draft_version=row.draft_version + 1,
                approved_by="Jamal",
                edited_payload={"form_patch": {"auto_mobility": True}},
            )
        self.assertEqual(context.exception.status_code, 409)

    def test_new_analysis_run_supersedes_old_unapproved_draft(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        first_run = _seed_analysis_rows(
            self.db,
            event_key=event_key,
            match_key=match_key,
            team_key=team_key,
            created_at=datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc),
        )
        first_row, _ = auto_scouting.generate_auto_scout_draft(
            self.db,
            event_key=event_key,
            match_key=match_key,
            team_key=team_key,
        )
        self.assertEqual(first_row.analysis_run_id, first_run.id)

        second_run = _seed_analysis_rows(
            self.db,
            event_key=event_key,
            match_key=match_key,
            team_key=team_key,
            created_at=datetime(2026, 4, 5, 12, 11, tzinfo=timezone.utc),
        )
        second_row, _ = auto_scouting.generate_auto_scout_draft(
            self.db,
            event_key=event_key,
            match_key=match_key,
            team_key=team_key,
            force_regenerate=True,
        )

        self.assertEqual(second_row.analysis_run_id, second_run.id)
        self.db.refresh(first_row)
        self.assertEqual(first_row.status, "superseded")
        self.assertIsNotNone(first_row.superseded_at)

    def test_approval_telemetry_reports_override_rate_and_time(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)
        row, _ = auto_scouting.generate_auto_scout_draft(
            self.db,
            event_key=event_key,
            match_key=match_key,
            team_key=team_key,
        )
        row.generated_at = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        auto_scouting.approve_auto_scout_draft(
            self.db,
            draft_id=row.id,
            draft_version=row.draft_version,
            approved_by="Jamal",
            edited_payload={
                "form_patch": {
                    **((row.draft_payload or {}).get("form_patch") or {}),
                    "auto_mobility": False,
                },
                "notes_seed": "",
                "derived_insights": (row.draft_payload or {}).get("derived_insights", {}),
            },
        )

        telemetry = auto_scouting.get_auto_scout_approval_telemetry(
            self.db,
            event_key=event_key,
            mapper_version="2026_v2",
            max_rows=200,
        )
        self.assertEqual(int(telemetry.get("approved_draft_count") or 0), 1)
        override_rate = telemetry.get("override_rate_by_field") or {}
        self.assertEqual(float(override_rate.get("auto_mobility") or 0.0), 1.0)
        tta = telemetry.get("time_to_approve_sec") or {}
        self.assertEqual(int(tta.get("count") or 0), 1)
        self.assertIsNotNone(tta.get("avg"))
        calibration = telemetry.get("confidence_calibration_by_field") or {}
        self.assertIn("auto_mobility", calibration)


_ROUND2_ML_FIELDS = (
    "teleop_under_defense_scored",
    "offense_level_1_5",
    "defense_level_1_5",
    "field_awareness_1_5",
    "decision_quality_1_5",
    "intake_failures",
)


def _seed_six_team_match(db) -> tuple[str, str, list[str]]:
    # A 6-team match where 5 teams have analysis data and the 6th has none, so the
    # 6th resolves to a team_not_resolved failed draft.
    event_key = "2026txhou"
    match_key = "2026txhou_qm1"
    resolved_teams = ["frc118", "frc148", "frc254", "frc1678", "frc973"]
    unresolved_team = "frc111"
    all_teams = resolved_teams + [unresolved_team]
    db.add(models.Event(event_key=event_key, name="Houston", year=2026))
    db.add(models.Match(match_key=match_key, event_key=event_key, comp_level="qm", set_number=1, match_number=1, time=1700000000))
    for index, team_key in enumerate(all_teams):
        alliance = "red" if index < 3 else "blue"
        station = f"{alliance[0]}{(index % 3) + 1}"
        db.add(models.Team(team_key=team_key, team_number=int(team_key[3:]), nickname=team_key))
        db.add(models.EventTeam(event_key=event_key, team_key=team_key))
        db.add(models.MatchTeam(match_key=match_key, team_key=team_key, event_key=event_key, alliance=alliance, station=station))
    db.commit()

    calibration = models.FieldCalibration(
        match_key=match_key,
        event_key=event_key,
        frame_time_sec=12.0,
        image_width=1280,
        image_height=720,
        image_points=[{"x": 0.0, "y": 0.0}, {"x": 1279.0, "y": 0.0}, {"x": 1279.0, "y": 719.0}, {"x": 0.0, "y": 719.0}],
        field_points=[{"x": 0.0, "y": 0.0}, {"x": 16.541, "y": 0.0}, {"x": 16.541, "y": 8.0693}, {"x": 0.0, "y": 8.0693}],
        homography=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    )
    db.add(calibration)
    db.flush()

    run = models.AnalysisRun(
        match_key=match_key,
        version="video_v3_tracks",
        status="completed",
        created_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    db.add(
        models.AnalysisRunContext(
            run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            analysis_version="video_v3_tracks",
            params_hash=f"params-{run.id}",
            calibration_id=calibration.id,
        )
    )
    db.add(
        models.AnalysisQuality(
            run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            calibration_quality_score=0.92,
            tracking_quality_score=0.9,
            identity_quality_score=0.88,
            overall_quality_score=0.9,
            details={"coverage_quality": {"avg_coverage_score_0_1": 0.84}},
        )
    )
    db.flush()

    for team_key in resolved_teams:
        finding = models.TeamMatchFinding(
            analysis_run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            team_key=team_key,
            alliance="red",
            station="r1",
            source="video_v3_tracks",
            fuel_scoring_rate=0.88,
            cycle_time_sec=10.0,
            auto_contribution=6.2,
            climb_success_prob=0.4,
            defensive_engagement_sec=11.5,
            reliability_score=0.91,
            summary={"sampling": {"detections": 60}},
        )
        db.add(finding)
        db.flush()
        db.add(
            models.TeamMatchThroughput(
                finding_id=finding.id,
                analysis_run_id=run.id,
                match_key=match_key,
                event_key=event_key,
                team_key=team_key,
                balls_shot_total=12,
                shooting_time_total_seconds=26.0,
                bps_total=0.46,
                balls_shot_active=10,
                shooting_time_active_seconds=21.0,
                active_bps=0.48,
                metric_coverage={"coverage_score": 0.84, "has_cycle_time": True},
                source="video_v3_tracks",
            )
        )
        for index, (time_sec, zone_key, speed_mps) in enumerate(
            [
                (0.0, "red_loading_depot_zone", 1.2),
                (5.0, "neutral_transition_zone", 1.1),
                (15.0, "red_alliance_scoring_zone", 0.8),
                (40.0, "neutral_transition_zone", 0.05),
                (60.0, "red_loading_depot_zone", 0.9),
                (100.0, "red_alliance_scoring_zone", 0.7),
                (120.0, "red_tower_endgame_zone", 0.4),
            ],
            start=1,
        ):
            db.add(
                models.RobotTrack(
                    analysis_run_id=run.id,
                    match_key=match_key,
                    event_key=event_key,
                    team_key=team_key,
                    track_id=1,
                    frame_index=index,
                    time_sec=time_sec,
                    bbox_x1=10.0,
                    bbox_y1=10.0,
                    bbox_x2=80.0,
                    bbox_y2=80.0,
                    centroid_x=45.0,
                    centroid_y=45.0,
                    field_x=5.0 + index,
                    field_y=2.0,
                    zone_key=zone_key,
                    speed_mps=speed_mps,
                    confidence=0.95,
                    source="video_v3_tracks",
                )
            )
        db.add(
            models.MatchEvent(
                analysis_run_id=run.id,
                match_key=match_key,
                event_key=event_key,
                team_key=team_key,
                track_id=1,
                frame_index=2,
                time_sec=3.5,
                event_type="auto_mobility",
                confidence=0.82,
                field_x=3.0,
                field_y=2.0,
                meta={"distance_m": 2.1},
            )
        )
    db.commit()
    return event_key, match_key, all_teams


class AutoScoutBatchGenerationTests(DBTestCase):
    def test_batch_creates_draft_for_every_team(self):
        event_key, match_key, all_teams = _seed_six_team_match(self.db)
        summary = auto_scouting.generate_auto_scout_drafts_for_match(
            self.db,
            event_key=event_key,
            match_key=match_key,
        )
        self.assertEqual(summary["team_count"], 6)
        self.assertEqual(summary["created_count"], 6)
        self.assertEqual(len(summary["draft_ids_by_team"]), 6)
        self.assertEqual(summary["errors"], [])
        # 5 resolved teams + 1 team_not_resolved failure, none blocking the others.
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["ready_count"] + summary["low_confidence_count"], 5)

    def test_one_failed_team_does_not_block_others(self):
        event_key, match_key, all_teams = _seed_six_team_match(self.db)
        auto_scouting.generate_auto_scout_drafts_for_match(
            self.db,
            event_key=event_key,
            match_key=match_key,
        )
        failed_team = "frc111"
        failed_draft = auto_scouting.get_auto_scout_draft(
            self.db, event_key=event_key, match_key=match_key, team_key=failed_team
        )
        self.assertEqual(failed_draft.status, "failed")
        self.assertIn("team_not_resolved", failed_draft.missing_reasons or [])
        for team_key in [t for t in all_teams if t != failed_team]:
            row = auto_scouting.get_auto_scout_draft(
                self.db, event_key=event_key, match_key=match_key, team_key=team_key
            )
            self.assertIn(row.status, {"ready", "low_confidence"})

    def test_batch_is_idempotent(self):
        event_key, match_key, _ = _seed_six_team_match(self.db)
        auto_scouting.generate_auto_scout_drafts_for_match(self.db, event_key=event_key, match_key=match_key)
        second = auto_scouting.generate_auto_scout_drafts_for_match(self.db, event_key=event_key, match_key=match_key)
        self.assertEqual(second["created_count"], 0)
        self.assertEqual(second["skipped_existing_count"], 6)

    def test_existing_ready_draft_skipped_unless_force(self):
        event_key, match_key, _ = _seed_six_team_match(self.db)
        team_key = "frc254"
        first = auto_scouting.generate_auto_scout_drafts_for_match(self.db, event_key=event_key, match_key=match_key)
        first_id = first["draft_ids_by_team"][team_key]

        skipped = auto_scouting.generate_auto_scout_drafts_for_match(self.db, event_key=event_key, match_key=match_key)
        self.assertEqual(skipped["draft_ids_by_team"][team_key], first_id)

        forced = auto_scouting.generate_auto_scout_drafts_for_match(
            self.db, event_key=event_key, match_key=match_key, force_regenerate=True
        )
        # Force bumps draft_version on the same row; the slot is still regenerated.
        self.assertGreaterEqual(forced["ready_count"] + forced["low_confidence_count"], 5)

    def test_approved_draft_is_not_replaced(self):
        event_key, match_key, _ = _seed_six_team_match(self.db)
        team_key = "frc148"
        first = auto_scouting.generate_auto_scout_drafts_for_match(self.db, event_key=event_key, match_key=match_key)
        approved_id = first["draft_ids_by_team"][team_key]
        approved = self.db.get(models.AutoScoutDraft, approved_id)
        auto_scouting.approve_auto_scout_draft(
            self.db,
            draft_id=approved.id,
            draft_version=approved.draft_version,
            approved_by="Jamal",
            edited_payload=approved.draft_payload or {"form_patch": {}, "notes_seed": "", "derived_insights": {}},
        )

        forced = auto_scouting.generate_auto_scout_drafts_for_match(
            self.db, event_key=event_key, match_key=match_key, force_regenerate=True
        )
        self.assertEqual(forced["draft_ids_by_team"][team_key], approved_id)
        self.db.refresh(approved)
        self.assertEqual(approved.status, "approved")

    def test_per_team_failure_is_isolated_into_errors(self):
        event_key, match_key, _ = _seed_six_team_match(self.db)
        real = auto_scouting.generate_auto_scout_draft
        calls = {"n": 0}

        def _flaky(db, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real(db, **kwargs)

        with patch.object(auto_scouting, "generate_auto_scout_draft", side_effect=_flaky):
            summary = auto_scouting.generate_auto_scout_drafts_for_match(
                self.db, event_key=event_key, match_key=match_key
            )
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["created_count"], 5)


class AutoScoutBackfillTests(DBTestCase):
    def test_backfill_generates_missing_drafts(self):
        event_key, match_key, _ = _seed_six_team_match(self.db)
        from app.services.auto_scout.backfill import backfill_missing_auto_scout_drafts

        result = backfill_missing_auto_scout_drafts(self.db, max_runs=10, max_drafts=50)
        self.assertEqual(result["matches_with_missing"], 1)
        self.assertGreaterEqual(result["drafts_created"], 5)

    def test_backfill_skips_when_drafts_present(self):
        event_key, match_key, _ = _seed_six_team_match(self.db)
        from app.services.auto_scout.backfill import backfill_missing_auto_scout_drafts

        auto_scouting.generate_auto_scout_drafts_for_match(self.db, event_key=event_key, match_key=match_key)
        result = backfill_missing_auto_scout_drafts(self.db, max_runs=10, max_drafts=50)
        self.assertEqual(result["matches_with_missing"], 0)
        self.assertEqual(result["drafts_created"], 0)


class AutoScoutPredictorCacheTests(DBTestCase):
    def test_feature_vector_built_once_per_draft(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)
        from app.services.auto_scout.predictors import round2_ml

        real_builder = round2_ml.build_auto_scout_field_feature_vector
        calls = {"n": 0}

        def _counting(*args, **kwargs):
            calls["n"] += 1
            return real_builder(*args, **kwargs)

        with patch.object(round2_ml, "build_auto_scout_field_feature_vector", side_effect=_counting):
            auto_scouting.generate_auto_scout_draft(
                self.db, event_key=event_key, match_key=match_key, team_key=team_key
            )
        # Six round-2 fields used to each build the vector twice (12 builds); now one.
        self.assertEqual(calls["n"], 1)

    def test_ml_high_confidence_prediction_wins(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)
        from app.services.auto_scout.predictors import round2_ml

        def _fake_infer(db, *, field_name, rows, model_version=None):
            return {
                "ok": True,
                "model_key": f"auto_scout_field:{field_name}",
                "model_version": "v9",
                "predictions": [{"field_value_pred": 4.0, "confidence_0_1": 0.93}],
            }

        with patch.object(round2_ml, "infer_auto_scout_field_shadow_from_rows", side_effect=_fake_infer):
            row, _ = auto_scouting.generate_auto_scout_draft(
                self.db, event_key=event_key, match_key=match_key, team_key=team_key
            )
        refs = (row.field_evidence_refs or {}).get("offense_level_1_5") or []
        self.assertTrue(any(str(ref.get("ref_id", "")).startswith("ml_model:") for ref in refs))

    def test_ml_missing_falls_back_to_deterministic(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)
        from app.services.auto_scout.predictors import round2_ml

        def _no_model(db, *, field_name, rows, model_version=None):
            return {"ok": False, "reason": "model_not_found", "predictions": []}

        with patch.object(round2_ml, "infer_auto_scout_field_shadow_from_rows", side_effect=_no_model):
            row, _ = auto_scouting.generate_auto_scout_draft(
                self.db, event_key=event_key, match_key=match_key, team_key=team_key
            )
        refs = (row.field_evidence_refs or {}).get("offense_level_1_5") or []
        self.assertTrue(refs)
        self.assertTrue(all(str(ref.get("ref_id", "")).startswith("auto_scout_rule:") for ref in refs))
        self.assertEqual((row.field_provenance or {}).get("offense_level_1_5"), "auto")


class AutoScoutTeamProfileTests(DBTestCase):
    def _seed_second_match(self, event_key, team_key, match_key):
        self.db.add(models.Match(match_key=match_key, event_key=event_key, comp_level="qm", set_number=1, match_number=2, time=1700000100))
        self.db.add(models.MatchTeam(match_key=match_key, team_key=team_key, event_key=event_key, alliance="red", station="r1"))
        self.db.commit()
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)

    def test_profile_aggregates_team_fields_across_matches(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)
        auto_scouting.generate_auto_scout_draft(self.db, event_key=event_key, match_key=match_key, team_key=team_key)

        second_match = f"{event_key}_qm2"
        self._seed_second_match(event_key, team_key, second_match)
        auto_scouting.generate_auto_scout_draft(self.db, event_key=event_key, match_key=second_match, team_key=team_key)

        profile = auto_scouting.summarize_team_auto_scout_profile(
            self.db, team_key=team_key, event_key=event_key, season_year=2026
        )
        self.assertTrue(profile["available"])
        self.assertEqual(profile["sample_matches"], 2)
        self.assertFalse(profile["is_last_season"])

        offense = profile["fields"].get("offense_level_1_5")
        self.assertIsNotNone(offense)
        self.assertEqual(offense["type"], "numeric")
        self.assertEqual(offense["samples"], 2)
        self.assertEqual(len(offense["trend"]), 2)
        self.assertIsNotNone(offense["avg_confidence_0_1"])

        mobility = profile["fields"].get("auto_mobility")
        self.assertIsNotNone(mobility)
        self.assertEqual(mobility["type"], "rate")
        self.assertEqual(mobility["samples"], 2)

        endgame = profile["fields"].get("endgame_mode")
        if endgame is not None:
            self.assertEqual(endgame["type"], "categorical")
            self.assertIn("mode", endgame)

    def test_profile_unavailable_when_no_drafts(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        profile = auto_scouting.summarize_team_auto_scout_profile(
            self.db, team_key=team_key, event_key=event_key, season_year=2026
        )
        self.assertFalse(profile["available"])
        self.assertEqual(profile["sample_matches"], 0)
        self.assertEqual(profile["fields"], {})

    def test_profile_widens_from_event_to_season(self):
        event_key, match_key, team_key = _seed_core_entities(self.db)
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)
        auto_scouting.generate_auto_scout_draft(self.db, event_key=event_key, match_key=match_key, team_key=team_key)

        # Ask for an event with no drafts; should widen to the season and still resolve.
        profile = auto_scouting.summarize_team_auto_scout_profile(
            self.db, team_key=team_key, event_key="2026other", season_year=2026
        )
        self.assertTrue(profile["available"])
        self.assertIsNone(profile["scope"]["event_key"])
        self.assertEqual(profile["scope"]["season_year"], 2026)


if __name__ == "__main__":
    unittest.main()
