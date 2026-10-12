from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes_scouting import router as scouting_router
from app.db import models
from app.db.base import Base
from app.db.session import get_db


class RatingsSummaryViewTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Session = sessionmaker(bind=engine, autoflush=False)
        Base.metadata.create_all(engine)
        with Session() as db:
            db.add(models.Event(event_key="2026arc", name="Archimedes", year=2026))
            db.add(models.Team(team_key="frc254", team_number=254, nickname="The Cheesy Poofs"))
            db.add(models.EventTeamRating(
                event_key="2026arc", team_key="frc254", rating_0_100=91.0,
                pros_json=[{"label": "Fast"}], cons_json=[{"label": "Rarely climbs"}],
                evidence_json=[{"kind": "match", "ref": "2026arc_qm1"}],
                details_json={"raw_features": {"fuel": 350.6}, "weighting": {"a": 1}},
            ))
            db.commit()
        app = FastAPI()
        app.include_router(scouting_router)

        def _db():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _db
        self.client = TestClient(app)

    def test_summary_drops_details_and_evidence_but_keeps_what_lists_show(self):
        full = self.client.get("/scouting/event/2026arc/ratings").json()["ratings"][0]
        summary = self.client.get("/scouting/event/2026arc/ratings", params={"view": "summary"}).json()["ratings"][0]
        self.assertTrue(full["details"])
        self.assertTrue(full["evidence"])
        self.assertEqual(summary["details"], {})
        self.assertEqual(summary["evidence"], [])
        for key in ("rating_0_100", "confidence_0_1", "subscores", "pros", "cons", "nickname", "model_version"):
            self.assertEqual(summary[key], full[key], key)

    def test_unknown_view_is_refused(self):
        self.assertEqual(self.client.get("/scouting/event/2026arc/ratings", params={"view": "all"}).status_code, 422)
