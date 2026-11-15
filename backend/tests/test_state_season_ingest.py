from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.db import models
from app.services.events.ingest import ingest_event
from app.services.ratings import component_spec
from app.services.season_config import UnsupportedSeasonRulesError
from tests.conftest import DBTestCase
from tests.test_alliance_results import _FakeTBA, _match


class _Next(_FakeTBA):
    def event(self, key):
        return {"key": key, "name": "Kickoff rehearsal", "year": 2027}


def _matches():
    out = []
    for number in (1, 2):
        match = copy.deepcopy(_match(number))
        match["key"] = f"2027test_qm{number}"
        out.append(match)
    return out


class StateSeasonIngestTests(DBTestCase):
    def _declare_2027(self, tmp_path):
        raw = json.loads(Path(component_spec.REGISTRY).read_text())
        entry = dict(next(e for e in raw["seasons"] if e["season"] == 2026), season=2027)
        raw["seasons"].append(entry)
        path = Path(tmp_path)/"registry.json"
        path.write_text(json.dumps(raw))
        return path

    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path, monkeypatch):
        self.tmp_path, self.monkeypatch = tmp_path, monkeypatch

    def test_a_state_served_season_stores_alliance_totals_without_rebuilt_truth(self):
        self.monkeypatch.setattr(component_spec, "REGISTRY", self._declare_2027(self.tmp_path))
        self.monkeypatch.setattr(settings, "rating_state_serving_enabled", True)
        out = ingest_event("2027test", _Next(_matches()), self.db, run_post_compute=False)
        self.assertEqual(out["score_breakdown_truth"], {"skipped": "no_rules_adapter"})
        rows = self.db.query(models.MatchAllianceResult).filter_by(event_key="2027test").order_by(
            models.MatchAllianceResult.match_key, models.MatchAllianceResult.alliance).all()
        self.assertEqual([(r.alliance, r.total_score, r.score_without_fouls, r.components) for r in rows[:2]],
                         [("blue", 513, 513, {}), ("red", 831, 826, {})])
        # No REBUILT per-robot truth on another game's breakdown.
        self.assertEqual(self.db.query(models.TeamMatchFinding).filter(models.TeamMatchFinding.event_key == "2027test").count(), 0)

    def test_any_other_unknown_season_still_fails_loudly(self):
        self.monkeypatch.setattr(settings, "rating_state_serving_enabled", False)
        with self.assertRaises(UnsupportedSeasonRulesError):
            ingest_event("2027test", _Next(_matches()), self.db, run_post_compute=False)
        self.db.rollback()
        self.monkeypatch.setattr(settings, "rating_state_serving_enabled", True)  # switch on, but 2027 undeclared
        with self.assertRaises(UnsupportedSeasonRulesError):
            ingest_event("2027test", _Next(_matches()), self.db, run_post_compute=False)
