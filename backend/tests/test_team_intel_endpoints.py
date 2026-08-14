from __future__ import annotations

import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes_teams import router as teams_router
from app.db import models
from app.db.base import Base
from app.db.session import get_db


def _seed_minimal_intel_rows(db: Session) -> None:
    event_key = "2026txhou"
    match_key = "2026txhou_qm1"
    team_key = "frc118"

    db.add(models.Event(event_key=event_key, name="Houston", year=2026))
    db.add(models.Team(team_key=team_key, team_number=118, nickname="Robonauts"))
    db.add(models.TeamProfile(team_key=team_key, state_prov="TX", country="USA"))
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
            params_hash="params-1",
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
        fuel_scoring_rate=0.9,
        cycle_time_sec=9.8,
        auto_contribution=5.2,
        climb_success_prob=0.65,
        defensive_engagement_sec=19.0,
        reliability_score=0.82,
        summary={
            "sampling": {"detections": 24},
            "throughput_metrics": {"coverage_score": 0.82},
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
            balls_shot_total=10,
            shooting_time_total_seconds=18.0,
            bps_total=0.55,
            balls_shot_active=8,
            shooting_time_active_seconds=14.0,
            active_bps=0.57,
            metric_coverage={"coverage_score": 0.82, "observed_matches": 1},
            source="video_v3_tracks",
        )
    )
    db.add(
        models.AnalysisQuality(
            run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            calibration_quality_score=0.8,
            tracking_quality_score=0.78,
            identity_quality_score=0.76,
            overall_quality_score=0.78,
            details={},
        )
    )
    db.add(
        models.EventTeamRating(
            event_key=event_key,
            team_key=team_key,
            rating_0_100=62.0,
            confidence_0_1=0.58,
            robot_level_0_100=60.0,
            driver_skill_0_100=58.0,
            results_anchor=55.0,
            throughput=63.0,
            shift_productivity=61.0,
            capacity_utilization=60.0,
            endgame=57.0,
            consistency=59.0,
            pros_json=[{"label": "Strong autonomous impact", "percentile": 74.0}],
            cons_json=[{"label": "Penalty pressure worsening", "percentile": 33.0}],
            details_json={
                "subscores": {
                    "auto_contribution": 68.0,
                    "anti_defense": 59.0,
                    "manual_points_impact": 61.0,
                    "rp_contribution": 58.0,
                    "defense_presence": 56.0,
                    "penalty_discipline": 64.0,
                }
            },
            model_version="rating_v11_calibrated_scale",
        )
    )
    db.commit()


class TeamIntelEndpointIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(self.engine)
        with self.SessionLocal() as db:
            _seed_minimal_intel_rows(db)

        app = FastAPI()
        app.include_router(teams_router)

        def _override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.rollback()
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_get_team_intel_includes_data_coverage_payload(self):
        response = self.client.get(
            "/teams/frc118/intel",
            params={
                "event_key": "2026txhou",
                "include_tba": "false",
                "include_statbotics": "false",
                "auto_heal_ratings": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        coverage = payload.get("data_coverage") or {}
        self.assertIn("score_0_1", coverage)
        self.assertIn("missing_reasons", coverage)
        self.assertIn("analysis", payload)
        self.assertIn("data_coverage", payload["analysis"])

    def test_get_event_teams_intel_includes_team_coverage_and_aggregate(self):
        response = self.client.get(
            "/teams/event/2026txhou/intel",
            params={
                "include_tba": "false",
                "include_statbotics": "false",
                "auto_heal_ratings": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("average_data_coverage_score_0_1", payload)
        teams = payload.get("teams") or []
        self.assertTrue(teams)
        self.assertIn("data_coverage", teams[0])

    def test_event_intel_compact_rating_mode_omits_details_and_signals(self):
        response = self.client.get(
            "/teams/event/2026txhou/intel",
            params={
                "include_tba": "false",
                "include_statbotics": "false",
                "include_rating_details": "false",
                "include_rating_signals": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        team_rows = payload.get("teams") or []
        self.assertTrue(team_rows)
        rating = (team_rows[0] or {}).get("rating") or {}
        self.assertEqual(rating.get("pros"), [])
        self.assertEqual(rating.get("cons"), [])
        self.assertEqual(rating.get("details"), {})

    def test_event_intel_full_rating_mode_includes_details_and_signals(self):
        response = self.client.get(
            "/teams/event/2026txhou/intel",
            params={
                "include_tba": "false",
                "include_statbotics": "false",
                "include_rating_details": "true",
                "include_rating_signals": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        team_rows = payload.get("teams") or []
        self.assertTrue(team_rows)
        rating = (team_rows[0] or {}).get("rating") or {}
        self.assertTrue(isinstance(rating.get("pros"), list) and len(rating.get("pros")) > 0)
        self.assertTrue(isinstance(rating.get("cons"), list) and len(rating.get("cons")) > 0)
        self.assertTrue(isinstance(rating.get("details"), dict) and len(rating.get("details")) > 0)

    def test_event_intel_compact_payload_is_smaller_than_full_payload(self):
        compact = self.client.get(
            "/teams/event/2026txhou/intel",
            params={
                "include_tba": "false",
                "include_statbotics": "false",
                "include_rating_details": "false",
                "include_rating_signals": "false",
            },
        )
        full = self.client.get(
            "/teams/event/2026txhou/intel",
            params={
                "include_tba": "false",
                "include_statbotics": "false",
                "include_rating_details": "true",
                "include_rating_signals": "true",
            },
        )
        self.assertEqual(compact.status_code, 200)
        self.assertEqual(full.status_code, 200)
        compact_bytes = len(json.dumps(compact.json()))
        full_bytes = len(json.dumps(full.json()))
        self.assertLess(compact_bytes, full_bytes)


if __name__ == "__main__":
    unittest.main()
