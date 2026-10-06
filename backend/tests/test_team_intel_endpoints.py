from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_teams
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

    def test_event_intel_keeps_the_event_loop_free_while_tba_is_slow(self):
        # The builder is async but its TBA/DB work is blocking; on the loop it froze
        # every other request and the room websockets for the whole TBA round trip.
        import asyncio
        import time as _time

        class _SlowTba:
            def event_team_statuses(self, event_key):
                _time.sleep(0.3)
                return {}

        async def _run():
            ticks = 0
            done = False

            async def _ticker():
                nonlocal ticks
                while not done:
                    ticks += 1
                    await asyncio.sleep(0.01)

            ticker = asyncio.create_task(_ticker())
            db = self.SessionLocal()
            try:
                await routes_teams._build_event_teams_intel_payload(
                    db=db,
                    event_key="2026txhou",
                    include_tba=True,
                    include_statbotics=False,
                    auto_heal_ratings=False,
                    include_season_fallback=True,
                    include_rating_details=False,
                    include_rating_signals=False,
                )
            finally:
                done = True
                db.close()
                await ticker
            return ticks

        with patch.object(routes_teams, "TBAClient", _SlowTba), patch.object(routes_teams.settings, "tba_auth_key", "test"):
            ticks = asyncio.run(_run())
        self.assertGreater(ticks, 10)

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

    def _event_intel_with_statbotics(self, total_points):
        # Every other test in this file passes include_statbotics=false, which is
        # exactly why the scalar-EPA crash reached production unnoticed.
        row = {
            "team": 118,
            "epa": {
                "total_points": total_points,
                "unitless": 2149.0,
                "norm": 1885.0,
                "breakdown": {"auto_points": 73.13, "teleop_points": 140.52},
            },
        }

        async def _fake_get_team_events(**_kwargs):
            return [row]

        with patch.object(
            routes_teams.statbotics_client, "get_team_events", _fake_get_team_events
        ):
            return self.client.get(
                "/teams/event/2026txhou/intel",
                params={
                    "include_tba": "false",
                    "include_statbotics": "true",
                    "auto_heal_ratings": "false",
                },
            )

    def test_event_intel_survives_scalar_statbotics_total_points(self):
        # Statbotics returns epa.total_points as a bare number for 2026 events.
        # Calling .get("mean") on it raised AttributeError and 500'd the payload for
        # every team at the event, not just the one with the odd shape.
        response = self._event_intel_with_statbotics(287.57)
        self.assertEqual(response.status_code, 200)
        teams = response.json().get("teams") or []
        self.assertTrue(teams)
        statbotics = teams[0].get("statbotics") or {}
        self.assertAlmostEqual(statbotics.get("event_epa_points"), 287.57, places=2)

    def test_event_intel_still_reads_mean_from_object_statbotics_total_points(self):
        # The older {"mean": ...} shape must keep working.
        response = self._event_intel_with_statbotics({"mean": 301.4})
        self.assertEqual(response.status_code, 200)
        teams = response.json().get("teams") or []
        self.assertTrue(teams)
        statbotics = teams[0].get("statbotics") or {}
        self.assertAlmostEqual(statbotics.get("event_epa_points"), 301.4, places=2)

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
