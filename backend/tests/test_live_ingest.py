from __future__ import annotations

from unittest import mock

from app.core.config import settings
from app.db import models
from app.services import scheduler
from app.services.events import live_ingest
from app.services.simulation.event_sim import QualMatch, Standing, simulate
from tests.conftest import DBTestCase

NOW = 1_792_360_000  # 2026-10-18 22:13 UTC
TODAY = [
    {"key": "2026busy", "start_date": "2026-10-17", "end_date": "2026-10-18"},
    {"key": "2026done", "start_date": "2026-10-18", "end_date": "2026-10-18"},
    {"key": "2026fresh", "start_date": "2026-10-18", "end_date": "2026-10-19"},
    {"key": "2026later", "start_date": "2026-11-07", "end_date": "2026-11-08"},
]


class LiveIngestTests(DBTestCase):
    def setUp(self):
        super().setUp()
        for key in ("2026busy", "2026done", "2026fresh", "2026later"):
            self.db.add(models.Event(event_key=key, name=key, year=2026))
        self.db.add(models.Match(match_key="2026busy_qm9", event_key="2026busy", comp_level="qm", set_number=1, match_number=9, predicted_time=NOW - 600))
        self.db.add(models.Match(match_key="2026done_qm1", event_key="2026done", comp_level="qm", set_number=1, match_number=1, actual_time=NOW - 5 * 3600))
        self.db.add(models.Match(match_key="2026later_qm1", event_key="2026later", comp_level="qm", set_number=1, match_number=1, time=NOW + 20 * 86400))
        self.db.commit()
        self.tba = mock.Mock()
        self.tba.events.return_value = TODAY

    def test_events_in_play(self):
        # Busy has a match 10 min ago; fresh runs today but was never ingested; done ended
        # hours ago; later isn't on today.
        candidates = live_ingest._events_covering(self.tba, live_ingest.datetime.fromtimestamp(NOW, live_ingest.timezone.utc).date())
        self.assertEqual(candidates, ["2026busy", "2026done", "2026fresh"])
        self.assertEqual(live_ingest.events_in_play(self.db, candidates, NOW), ["2026busy", "2026fresh"])

    def test_only_events_that_gained_results_are_rebuilt(self):
        def fake_refresh(db, *, event_key, run_post_compute):
            if event_key == "2026busy":
                db.add(models.MatchAllianceResult(match_key="2026busy_qm9", alliance="red", event_key="2026busy", season_year=2026))
                db.commit()
            return {"status": "processed"}

        with mock.patch.object(live_ingest, "refresh_event", side_effect=fake_refresh) as refresh, \
                mock.patch.object(live_ingest, "post_compute_event", return_value={"ratings": {"ok": True}}) as rebuild:
            result = live_ingest.ingest_events_in_play(self.db, self.tba, now=NOW)
        self.assertEqual([c.kwargs["event_key"] for c in refresh.call_args_list], ["2026busy", "2026fresh"])
        self.assertEqual([c.kwargs["event_key"] for c in rebuild.call_args_list], ["2026busy"])
        self.assertEqual((result["changed"], result["rebuilt"], result["errors"]), (["2026busy"], ["2026busy"], []))

    def test_a_failing_event_does_not_stop_the_others(self):
        statuses = {"2026busy": {"status": "ingest_failed"}, "2026fresh": {"status": "processed"}}
        with mock.patch.object(live_ingest, "refresh_event", side_effect=lambda db, *, event_key, run_post_compute: statuses[event_key]), \
                mock.patch.object(live_ingest, "post_compute_event") as rebuild:
            result = live_ingest.ingest_events_in_play(self.db, self.tba, now=NOW)
        self.assertEqual(result["errors"], ["2026busy"])
        rebuild.assert_not_called()

    def test_scheduler_registers_it_unless_switched_off(self):
        for enabled, expected in ((True, True), (False, False)):
            registered = []
            fake = mock.Mock(add_job=lambda *_a, **kw: registered.append(kw.get("id")), running=False)
            with mock.patch.object(scheduler, "get_scheduler", return_value=fake), \
                    mock.patch.object(settings, "live_event_ingest_enabled", enabled):
                scheduler.start_scheduler()
            self.assertEqual("live_event_ingest" in registered, expected)


def test_forecast_tolerates_a_lineup_robot_missing_from_the_team_list():
    # 2026catt on 2026-10-18: frc4414b was in lineups but not on the event's team list.
    teams = ["frc1", "frc2", "frc3", "frc4", "frc5", "frc6", "frc7", "frc8"]
    remaining = [QualMatch(("frc1", "frc2", "frc4414b"), ("frc4", "frc5", "frc6"), 0.6)]
    out = simulate(teams, {t: Standing() for t in teams}, remaining, {t: 1.0 for t in teams}, lambda a, b: 0.5, runs=20)
    assert set(out) == set(teams)
