from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import unittest
import uuid
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import routes_tracks
from app.api.routes_tracks import router as tracks_router
from app.db import models
from app.db.session import get_db


@unittest.skipUnless(
    os.environ.get("RUN_POSTGRES_INTEGRATION_E2E") == "1",
    "Set RUN_POSTGRES_INTEGRATION_E2E=1 to run PostgreSQL concurrency coverage.",
)
class OnDeviceSyncPostgresE2ETests(unittest.TestCase):
    def test_concurrent_retry_creates_one_session_and_one_track_set(self):
        database_url = str(os.environ["DATABASE_URL"])
        engine = create_engine(database_url, pool_pre_ping=True)
        session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        suffix = uuid.uuid4().hex[:10]
        event_key = f"2026e{suffix}"
        match_key = f"{event_key}_qm1"
        team_key = f"frc9{int(suffix[:5], 16) % 100000:05d}"

        with session_local() as db:
            db.add(models.Event(event_key=event_key, name="Concurrency", year=2026))
            db.add(models.Team(
                team_key=team_key,
                team_number=int(team_key[3:]),
                nickname="Concurrency",
            ))
            db.add(models.Match(
                match_key=match_key,
                event_key=event_key,
                comp_level="qm",
                set_number=1,
                match_number=1,
                time=1700000000,
            ))
            db.add(models.MatchTeam(
                match_key=match_key,
                team_key=team_key,
                event_key=event_key,
                alliance="red",
                station="r1",
            ))
            db.commit()

        app = FastAPI()
        app.include_router(tracks_router)

        def _override_get_db():
            db = session_local()
            try:
                yield db
            finally:
                db.rollback()
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        body = {
            "id": f"concurrent-{suffix}",
            "matchKey": match_key,
            "payload": {
                "points_by_team": {
                    team_key: [
                        {"timeSec": 31.0, "fieldX": 12.0, "fieldY": 4.0}
                    ]
                },
                "shift1_active_alliance": "red",
            },
        }

        try:
            with (
                TestClient(app) as client,
                mock.patch.object(
                    routes_tracks,
                    "on_device_sync_identity",
                    return_value=(True, "postgres-e2e"),
                ),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                responses = list(
                    executor.map(
                        lambda _index: client.post(
                            "/tracks/on-device-session", json=body
                        ),
                        range(2),
                    )
                )
            self.assertEqual([response.status_code for response in responses], [200, 200])
            run_ids = {response.json()["run_id"] for response in responses}
            self.assertEqual(len(run_ids), 1)
            with session_local() as db:
                sessions = db.query(models.OnDeviceSession).filter(
                    models.OnDeviceSession.match_key == match_key
                ).all()
                tracks = db.query(models.RobotTrack).filter(
                    models.RobotTrack.match_key == match_key
                ).all()
                self.assertEqual(len(sessions), 1)
                self.assertEqual(len(tracks), 1)
        finally:
            with session_local() as db:
                run_ids = [
                    run_id
                    for (run_id,) in db.query(models.AnalysisRun.id).filter(
                        models.AnalysisRun.match_key == match_key
                    ).all()
                ]
                if run_ids:
                    db.query(models.RobotTrack).filter(
                        models.RobotTrack.analysis_run_id.in_(run_ids)
                    ).delete(synchronize_session=False)
                    db.query(models.OnDeviceSession).filter(
                        models.OnDeviceSession.analysis_run_id.in_(run_ids)
                    ).delete(synchronize_session=False)
                    db.query(models.AnalysisRunContext).filter(
                        models.AnalysisRunContext.run_id.in_(run_ids)
                    ).delete(synchronize_session=False)
                    db.query(models.AnalysisRun).filter(
                        models.AnalysisRun.id.in_(run_ids)
                    ).delete(synchronize_session=False)
                db.query(models.MatchTeam).filter(
                    models.MatchTeam.match_key == match_key
                ).delete(synchronize_session=False)
                db.query(models.Match).filter(
                    models.Match.match_key == match_key
                ).delete(synchronize_session=False)
                db.query(models.Team).filter(
                    models.Team.team_key == team_key
                ).delete(synchronize_session=False)
                db.query(models.Event).filter(
                    models.Event.event_key == event_key
                ).delete(synchronize_session=False)
                db.commit()
            engine.dispose()
