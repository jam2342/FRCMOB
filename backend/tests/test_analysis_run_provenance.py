from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models
from app.services.analysis.runs import (
    RUN_KIND_OFFICIAL_TRUTH,
    RUN_KIND_ON_DEVICE,
    RUN_KIND_VIDEO,
    latest_completed_run,
)
from app.services.ratings.data_loader import load_event_rating_data
from tests.conftest import DBTestCase


class AnalysisRunProvenanceTests(DBTestCase):
    def _seed(self) -> tuple[str, str]:
        event_key = "2026txhou"
        match_key = "2026txhou_qm1"
        self.db.add(models.Event(event_key=event_key, name="Houston", year=2026))
        self.db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
        self.db.add(
            models.Match(
                match_key=match_key,
                event_key=event_key,
                comp_level="qm",
                set_number=1,
                match_number=1,
                time=1700000000,
            )
        )
        self.db.commit()
        return event_key, match_key

    def _run(
        self,
        *,
        event_key: str,
        match_key: str,
        version: str,
        run_kind: str,
        created_at: datetime,
    ) -> models.AnalysisRun:
        run = models.AnalysisRun(
            match_key=match_key,
            version=version,
            run_kind=run_kind,
            status="completed",
            created_at=created_at,
        )
        self.db.add(run)
        self.db.flush()
        self.db.add(
            models.AnalysisRunContext(
                run_id=run.id,
                match_key=match_key,
                event_key=event_key,
                analysis_version=version,
                params_hash=f"params-{run.id}",
            )
        )
        return run

    def _track(self, run: models.AnalysisRun, *, event_key: str, source: str) -> None:
        self.db.add(
            models.RobotTrack(
                analysis_run_id=run.id,
                match_key=run.match_key,
                event_key=event_key,
                team_key="frc118",
                track_id=1,
                frame_index=1,
                time_sec=1.0,
                bbox_x1=0.0,
                bbox_y1=0.0,
                bbox_x2=1.0,
                bbox_y2=1.0,
                centroid_x=0.5,
                centroid_y=0.5,
                field_x=1.0,
                field_y=1.0,
                source=source,
            )
        )

    def test_newer_non_video_run_cannot_shadow_canonical_video(self):
        event_key, match_key = self._seed()
        now = datetime.now(timezone.utc)
        video = self._run(
            event_key=event_key,
            match_key=match_key,
            version="video_v3_tracks",
            run_kind=RUN_KIND_VIDEO,
            created_at=now - timedelta(minutes=3),
        )
        self._run(
            event_key=event_key,
            match_key=match_key,
            version="tba_score_breakdown_v1",
            run_kind=RUN_KIND_OFFICIAL_TRUTH,
            created_at=now - timedelta(minutes=2),
        )
        self._run(
            event_key=event_key,
            match_key=match_key,
            version="on_device_pwa_v1",
            run_kind=RUN_KIND_ON_DEVICE,
            created_at=now - timedelta(minutes=1),
        )
        self.db.commit()

        selected = latest_completed_run(
            self.db,
            match_key=match_key,
            run_kind=RUN_KIND_VIDEO,
            analysis_version="video_v3_tracks",
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected[0].id, video.id)

    def test_ratings_loader_quarantines_on_device_findings(self):
        event_key, match_key = self._seed()
        self.db.add(models.EventTeam(event_key=event_key, team_key="frc118"))
        now = datetime.now(timezone.utc)
        video = self._run(
            event_key=event_key,
            match_key=match_key,
            version="video_v3_tracks",
            run_kind=RUN_KIND_VIDEO,
            created_at=now,
        )
        on_device = self._run(
            event_key=event_key,
            match_key=match_key,
            version="on_device_pwa_v1",
            run_kind=RUN_KIND_ON_DEVICE,
            created_at=now + timedelta(seconds=1),
        )
        for run, source in (
            (video, "video_v3_tracks"),
            (on_device, "on_device_pwa_v1"),
        ):
            self.db.add(
                models.TeamMatchFinding(
                    analysis_run_id=run.id,
                    match_key=match_key,
                    event_key=event_key,
                    team_key="frc118",
                    alliance="red",
                    source=source,
                    summary={"quality_gate": {"accepted": True}},
                )
            )
        self.db.commit()

        data = load_event_rating_data(self.db, event_key, {"season_year": 2026})

        self.assertEqual(len(data.raw_finding_rows), 1)
        self.assertEqual(data.raw_finding_rows[0].analysis_run_id, video.id)
        self.assertNotIn(on_device.id, data.accepted_run_ids)
