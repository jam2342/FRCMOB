from __future__ import annotations

from unittest import mock

from app.api import routes_matches
from app.core.config import settings
from app.db import models
from tests.test_schedule_times_endpoint import EVENT, ScheduleTimesEndpointTests


class _FakeCache:
    def __init__(self):
        self.values = {}

    def get(self, key):  # noqa: ANN001
        return self.values.get(key)

    def set(self, key, value, ttl=3600):  # noqa: ANN001
        self.values[key] = value
        return True


def _tba(key, comp, set_number, match_number):
    return {
        "key": key, "comp_level": comp, "set_number": set_number, "match_number": match_number,
        "alliances": {"red": {"team_keys": ["frc1", "frc2", "frc3"], "score": -1},
                      "blue": {"team_keys": ["frc4", "frc5", "frc6"], "score": -1}},
        "winning_alliance": "", "predicted_time": None, "actual_time": None, "time": None,
    }


class KnockoutTopupTests(ScheduleTimesEndpointTests):
    # TBA adds double-elimination matches round by round; later rounds must reach the schedule.
    def setUp(self) -> None:
        super().setUp()
        with self.SessionLocal() as db:
            db.add(models.Match(match_key=f"{EVENT}_sf1m1", event_key=EVENT, comp_level="sf", set_number=1, match_number=1))
            db.commit()
        self.tba = mock.Mock()
        self.tba.event_teams.return_value = []
        for patcher in (
            mock.patch.object(settings, "tba_auth_key", "test-key"),
            mock.patch.object(settings, "public_readonly_mode", False),
            mock.patch.object(routes_matches, "TBAClient", return_value=self.tba),
            mock.patch.object(routes_matches, "get_cache", return_value=_FakeCache()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _keys(self):
        resp = self.client.get(f"/matches/event/{EVENT}/schedule", params={"includeTeamNicknames": "false"})
        self.assertEqual(resp.status_code, 200, resp.text)
        return [m["match_key"] for m in resp.json()["matches"]]

    def test_finals_added_after_the_first_playoff_round_appear(self):
        self.tba.event_matches.return_value = [
            _tba(f"{EVENT}_qm1", "qm", 1, 1), _tba(f"{EVENT}_sf1m1", "sf", 1, 1), _tba(f"{EVENT}_f1m1", "f", 1, 1),
        ]
        self.assertIn(f"{EVENT}_f1m1", self._keys())

    def test_no_refresh_when_every_tba_match_is_saved(self):
        self.tba.event_matches.return_value = [_tba(f"{EVENT}_qm1", "qm", 1, 1), _tba(f"{EVENT}_sf1m1", "sf", 1, 1)]
        with mock.patch.object(routes_matches, "_refresh_event_schedule_from_tba") as refresh:
            self._keys()
        refresh.assert_not_called()
