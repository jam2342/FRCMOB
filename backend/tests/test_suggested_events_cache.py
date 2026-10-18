from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch

from app.api import routes_events


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):  # noqa: ANN001
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


class _FakeCache:
    def __init__(self) -> None:
        self.redis_client = _FakeRedis()

    def get(self, key):  # noqa: ANN001
        return self.redis_client.store.get(key)

    def set(self, key, value, ttl=3600):  # noqa: ANN001
        self.redis_client.store[key] = value
        return True

    def delete(self, key):  # noqa: ANN001
        self.redis_client.store.pop(key, None)
        return True


class _InlineThread:
    def __init__(self, target, name=None, daemon=None):  # noqa: ANN001
        self._target = target

    def start(self) -> None:
        self._target()


def _payload(source: str, marker: str) -> dict:
    return {"ok": True, "source": source, "count": 1, "events": [{"event_key": marker}]}


class SuggestedEventsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = _FakeCache()
        self.patches = [
            patch.object(routes_events, "get_cache", return_value=self.cache),
            patch.object(routes_events.threading, "Thread", _InlineThread),
            patch("app.db.session.SessionLocal"),
        ]
        for p in self.patches:
            p.start()
        self.calls: list[str] = []

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()

    def _call(self) -> dict:
        return routes_events.suggested_events(
            preferred_year=2026, fallback_year=2025, limit=25, prioritize_running=True,
            team_count_fetch_limit=None, db=object(),
        )

    def _compute(self, source: str, marker: str):
        def compute(db, **params):  # noqa: ANN001
            self.calls.append(marker)
            return _payload(source, marker)
        return compute

    def test_second_call_is_served_from_the_cache(self):
        with patch.object(routes_events, "_compute_suggested_events", side_effect=self._compute("remote_most_teams", "a")):
            first = self._call()
            second = self._call()
        self.assertEqual(first, second)
        self.assertEqual(self.calls, ["a"])

    def test_stale_answer_is_served_then_refreshed_in_the_background(self):
        with patch.object(routes_events, "_compute_suggested_events", side_effect=self._compute("remote_most_teams", "old")):
            self._call()
        key = next(k for k in self.cache.redis_client.store if k.startswith(routes_events._SUGGESTED_CACHE_PREFIX))
        envelope = json.loads(self.cache.redis_client.store[key])
        envelope["fresh_until"] = time.time() - 1
        self.cache.redis_client.store[key] = json.dumps(envelope)
        with patch.object(routes_events, "_compute_suggested_events", side_effect=self._compute("remote_most_teams", "new")):
            served = self._call()
            after = self._call()
        self.assertEqual(served["events"][0]["event_key"], "old")
        self.assertEqual(after["events"][0]["event_key"], "new")
        self.assertNotIn(f"{key}:refreshing", self.cache.redis_client.store)

    def test_local_fallback_is_only_fresh_for_a_minute(self):
        with patch.object(routes_events, "_compute_suggested_events", side_effect=self._compute("local_most_teams_remote_unavailable", "x")):
            self._call()
        key = next(iter(self.cache.redis_client.store))
        fresh_for = json.loads(self.cache.redis_client.store[key])["fresh_until"] - time.time()
        self.assertLessEqual(fresh_for, routes_events._SUGGESTED_FALLBACK_FRESH_SEC + 1)

    def test_parameters_get_separate_entries(self):
        with patch.object(routes_events, "_compute_suggested_events", side_effect=self._compute("remote_most_teams", "a")):
            self._call()
            routes_events.suggested_events(
                preferred_year=2026, fallback_year=2025, limit=80, prioritize_running=True,
                team_count_fetch_limit=None, db=object(),
            )
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()


class SuggestedEventsOrderTests(unittest.TestCase):
    def test_running_events_lead_and_the_rest_still_follow(self):
        from datetime import date, timedelta

        today = date.today()
        def event(key, start, end, teams):
            return {"key": key, "name": key, "year": 2026, "start_date": start.isoformat(), "end_date": end.isoformat(), "team_count": teams}
        tba_events = [
            event("2026big", date(2026, 3, 1), date(2026, 3, 3), 60),
            event("2026mid", date(2026, 4, 1), date(2026, 4, 3), 40),
            event("2026nowa", today, today + timedelta(days=2), 20),
            event("2026nowb", today - timedelta(days=1), today + timedelta(days=1), 30),
        ]

        class FakeTBA:
            def events(self, year):
                return tba_events if year == 2026 else []

            def event_teams(self, key):
                return []

        with patch.object(routes_events, "TBAClient", FakeTBA), \
             patch.object(routes_events, "_event_team_counts", return_value={}), \
             patch.object(routes_events.settings, "tba_auth_key", "x"), \
             patch.object(routes_events.settings, "public_readonly_mode", True):
            payload = routes_events._compute_suggested_events(
                object(), preferred_year=2026, fallback_year=2025, limit=10, prioritize_running=True, team_count_fetch_limit=0,
            )
            plain = routes_events._compute_suggested_events(
                object(), preferred_year=2026, fallback_year=2025, limit=10, prioritize_running=False, team_count_fetch_limit=0,
            )
        keys = [e["event_key"] for e in payload["events"]]
        # Two events are running: they come first (most teams first), and the season's
        # other events still follow instead of being dropped.
        self.assertEqual(keys, ["2026nowb", "2026nowa", "2026big", "2026mid"])
        self.assertEqual((payload["source"], payload["running_count"]), ("remote_live_now", 2))
        self.assertEqual([e["event_key"] for e in plain["events"]], ["2026big", "2026mid", "2026nowb", "2026nowa"])
