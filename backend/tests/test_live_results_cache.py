from __future__ import annotations

import time
import unittest
from unittest import mock

from app.api import routes_matches


class _FakeCache:
    def __init__(self) -> None:
        self.ttls: dict[str, int] = {}
        self.values: dict[str, str] = {}

    def get(self, key):  # noqa: ANN001
        return self.values.get(key)

    def set(self, key, value, ttl=3600):  # noqa: ANN001
        self.values[key] = value
        self.ttls[key] = ttl
        return True


def _tba_match(key: str, done: bool, actual: int | None):
    return {
        "key": key,
        "alliances": {"red": {"score": 100 if done else -1}, "blue": {"score": 90 if done else -1}},
        "winning_alliance": "red" if done else "",
        "actual_time": actual,
        "predicted_time": actual,
    }


class LiveResultsCacheTests(unittest.TestCase):
    def _ttl_for(self, matches):
        cache = _FakeCache()
        tba = mock.Mock()
        tba.event_matches.return_value = matches
        with mock.patch.object(routes_matches, "get_cache", return_value=cache):
            routes_matches._cached_live_results_for_event(tba, "2026arc")
        return cache.ttls["matches:live_results:2026arc"]

    def test_finished_event_is_kept_for_hours(self):
        old = int(time.time()) - 30 * 86400
        ttl = self._ttl_for([_tba_match("2026arc_qm1", True, old), _tba_match("2026arc_f1m1", True, old + 3600)])
        self.assertEqual(ttl, routes_matches.SETTLED_RESULTS_CACHE_TTL_SEC)

    def test_running_event_stays_live(self):
        now = int(time.time())
        self.assertLess(self._ttl_for([_tba_match("2026arc_qm1", True, now - 600), _tba_match("2026arc_qm2", False, None)]), 60)
        # Every match played but the last one ended an hour ago (between quals and playoffs).
        self.assertLess(self._ttl_for([_tba_match("2026arc_qm1", True, now - 3600)]), 60)
        self.assertLess(self._ttl_for([]), 60)


if __name__ == "__main__":
    unittest.main()
