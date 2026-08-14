from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_teams
from app.db import models
from app.db.base import Base
from app.services.ratings.model import recompute_event_ratings


def _seed_pipeline_rows(db: Session) -> None:
    event_key = "2026txhou"
    match_key = "2026txhou_qm1"
    team_key = "frc118"

    db.add(models.Event(event_key=event_key, name="Houston", year=2026))
    db.add(models.Team(team_key=team_key, team_number=118, nickname="Robonauts"))
    db.add(models.EventTeam(event_key=event_key, team_key=team_key))
    db.add(models.Match(match_key=match_key, event_key=event_key, comp_level="qm", set_number=1, match_number=1, time=1700000000))
    db.add(models.MatchTeam(match_key=match_key, team_key=team_key, event_key=event_key, alliance="red", station="r1"))

    run = models.AnalysisRun(match_key=match_key, version="video_v3_tracks", status="completed")
    db.add(run)
    db.flush()
    db.add(
        models.AnalysisRunContext(
            run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            analysis_version="video_v3_tracks",
            params_hash="params-smoke",
            calibration_id=1,
        )
    )
    finding = models.TeamMatchFinding(
        analysis_run_id=run.id,
        match_key=match_key,
        event_key=event_key,
        team_key=team_key,
        alliance="red",
        station="r1",
        source="video_v3_tracks",
        fuel_scoring_rate=0.88,
        cycle_time_sec=10.2,
        auto_contribution=5.4,
        climb_success_prob=0.61,
        defensive_engagement_sec=17.5,
        reliability_score=0.8,
        summary={
            "sampling": {"detections": 21},
            "throughput_metrics": {"coverage_score": 0.79},
            "analysis_context": {"tracking_backend": "yolo_bytetrack"},
        },
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
            balls_shot_total=11,
            shooting_time_total_seconds=20.0,
            bps_total=0.55,
            balls_shot_active=9,
            shooting_time_active_seconds=15.0,
            active_bps=0.6,
            metric_coverage={"coverage_score": 0.79, "observed_matches": 1},
            source="video_v3_tracks",
        )
    )
    db.add(
        models.AnalysisQuality(
            run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            calibration_quality_score=0.77,
            tracking_quality_score=0.8,
            identity_quality_score=0.78,
            overall_quality_score=0.79,
            details={},
        )
    )
    db.commit()


class PipelineSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(self.engine)
        self.db = self.SessionLocal()
        _seed_pipeline_rows(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_ingest_analysis_rating_to_intel_payload_smoke(self):
        rating_result = recompute_event_ratings(self.db, "2026txhou")
        self.assertTrue(rating_result.get("ok"))
        self.assertGreaterEqual(int(rating_result.get("count") or 0), 1)

        team_payload = asyncio.run(
            routes_teams._build_team_intel_payload(
                db=self.db,
                team_key="frc118",
                event_key="2026txhou",
                preferred_year=2026,
                fallback_year=2025,
                include_tba=False,
                include_statbotics=False,
                allow_season_fallback=True,
                auto_heal_ratings=False,
            )
        )
        self.assertTrue(team_payload.get("ok"))
        self.assertTrue((team_payload.get("rating") or {}).get("available"))
        self.assertGreaterEqual(float((team_payload.get("data_coverage") or {}).get("score_0_1") or 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
