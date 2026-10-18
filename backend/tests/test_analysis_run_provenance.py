from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models
from app.services.analysis.runs import (
    RUN_KIND_ON_DEVICE,
    RUN_KIND_VIDEO,
)
from app.services.ratings.model import recompute_event_ratings
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

    def test_ratings_read_only_official_findings(self):
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

        # Video and phone findings can be wrong or unreviewed: the rating rests on
        # official results only, so this team is rated as the season's typical robot.
        recompute_event_ratings(self.db, event_key)
        row = self.db.get(models.EventTeamRating, (event_key, "frc118"))
        self.assertEqual(row.details_json["basis"], "season_typical")
        self.assertEqual(row.details_json["matches_here"], 0)
