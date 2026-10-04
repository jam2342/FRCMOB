from __future__ import annotations

import time
from unittest.mock import patch

from app.db import models
from app.services.events.match_times import refresh_match_times
from app.services.push import match_alerts
from tests.conftest import DBTestCase

EVENT = "2026txhou"


class MatchTimesAndAlertsTests(DBTestCase):
    def _seed(self, *, scheduled: int, predicted: int | None = None, actual: int | None = None) -> None:
        self.db.add(models.Event(event_key=EVENT, name="Houston", year=2026))
        self.db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
        self.db.add(models.Match(
            match_key=f"{EVENT}_qm1", event_key=EVENT, comp_level="qm", set_number=1, match_number=1,
            time=scheduled, predicted_time=predicted, actual_time=actual,
        ))
        self.db.add(models.MatchTeam(
            match_key=f"{EVENT}_qm1", team_key="frc118", event_key=EVENT, alliance="red", station="r1",
        ))
        self.db.add(models.PushSubscription(
            endpoint="https://push.example/1", keys={}, event_key=EVENT, team_keys=["frc118"],
            prefs={"match_lead_minutes": 10}, enabled=True, notified={},
        ))
        self.db.commit()

    def _tick(self, tba_matches: list[dict]) -> list[dict]:
        sent: list[dict] = []

        class _TBA:
            def event_matches(self, _event_key):
                return tba_matches

        with (
            patch.object(match_alerts, "push_configured", return_value=True),
            patch.object(match_alerts, "TBAClient", _TBA),
            patch.object(match_alerts, "send_web_push", side_effect=lambda _db, _sub, body: sent.append(body) or True),
        ):
            match_alerts.run_match_alert_tick(self.db)
        return sent

    def test_refresh_writes_only_changed_times_and_leaves_the_schedule(self):
        now = int(time.time())
        self._seed(scheduled=now + 3600)
        changed = refresh_match_times(
            self.db,
            event_key=EVENT,
            tba_matches=[{"key": f"{EVENT}_qm1", "time": 1, "predicted_time": now + 4000, "actual_time": None}],
        )
        match = self.db.get(models.Match, f"{EVENT}_qm1")
        self.assertEqual(changed, 1)
        self.assertEqual(match.time, now + 3600)
        self.assertEqual(match.predicted_time, now + 4000)
        self.assertIsNone(match.actual_time)
        self.assertEqual(refresh_match_times(self.db, event_key=EVENT, tba_matches=[{"key": f"{EVENT}_qm1", "predicted_time": now + 4000}]), 0)

    def test_alert_follows_the_predicted_time_when_the_field_runs_late(self):
        now = int(time.time())
        # Scheduled in 5 minutes, but the field is 40 minutes behind: no alert yet.
        self._seed(scheduled=now + 300)
        sent = self._tick([{"key": f"{EVENT}_qm1", "time": now + 300, "predicted_time": now + 2700}])
        self.assertEqual(sent, [])

    def test_alert_fires_on_the_predicted_time(self):
        now = int(time.time())
        # Scheduled an hour out, but the field is running early.
        self._seed(scheduled=now + 3600)
        sent = self._tick([{"key": f"{EVENT}_qm1", "time": now + 3600, "predicted_time": now + 420}])
        self.assertEqual(len(sent), 1)
        self.assertIn("Team 118 plays soon", sent[0]["title"])

    def test_no_alert_once_the_match_has_started(self):
        now = int(time.time())
        self._seed(scheduled=now + 300)
        sent = self._tick([{"key": f"{EVENT}_qm1", "time": now + 300, "actual_time": now - 30}])
        self.assertEqual(sent, [])
