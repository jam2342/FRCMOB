import asyncio
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_matches
from app.services.events import live_signal


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    def set(self, key, value, ex=None):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def scan_iter(self, match="*", count=None):
        prefix = match.rstrip("*")
        return [key for key in list(self.values) if key.startswith(prefix)]

    def pipeline(self):
        redis, ops = self, []

        class _Pipe:
            def get(self, key):
                ops.append(lambda: redis.get(key))

            def set(self, key, value, ex=None):
                ops.append(lambda: redis.set(key, value, ex=ex))

            def execute(self):
                return [op() for op in ops]

        return _Pipe()


RESULTS = {
    "2026txhou_qm1": {"is_completed": True, "red_score": 80, "blue_score": 60, "predicted_time": 1},
    "2026txhou_qm2": {"is_completed": False, "red_score": -1, "blue_score": -1, "predicted_time": 2},
}


class LiveSignalTests(unittest.TestCase):
    def setUp(self):
        self.redis = _FakeRedis()
        patcher = mock.patch.object(live_signal, "get_cache", return_value=mock.Mock(redis_client=self.redis))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_version_follows_scores_and_completion_only(self):
        version = live_signal.results_version(RESULTS)
        moved = {**RESULTS, "2026txhou_qm2": {**RESULTS["2026txhou_qm2"], "predicted_time": 99}}
        self.assertEqual(live_signal.results_version(moved), version)
        scored = {**RESULTS, "2026txhou_qm2": {"is_completed": True, "red_score": 70, "blue_score": 71}}
        self.assertNotEqual(live_signal.results_version(scored), version)

    def test_a_socket_hears_the_current_version_then_each_change(self):
        app = FastAPI()
        app.include_router(routes_matches.router)
        live_signal.note_results("2026txhou", RESULTS)
        first = live_signal.results_version(RESULTS)
        hub = live_signal.LiveResultsHub()
        with (
            mock.patch.object(routes_matches, "live_results_hub", hub),
            mock.patch.object(routes_matches, "_event_known", return_value=True),
            mock.patch.object(live_signal, "POLL_SEC", 0.05),
        ):
            with TestClient(app) as client, client.websocket_connect("/matches/event/2026txhou/live") as socket:
                self.assertEqual(socket.receive_json(), {"type": "results", "event_key": "2026txhou", "version": first})
                # A page being open marks the event watched, so the scheduler keeps it fresh.
                self.assertEqual(live_signal.watched_events(), ["2026txhou"])
                scored = {**RESULTS, "2026txhou_qm2": {"is_completed": True, "red_score": 70, "blue_score": 71}}
                live_signal.note_results("2026txhou", scored)
                message = socket.receive_json()
                self.assertEqual(message["version"], live_signal.results_version(scored))
            asyncio.run(hub.shutdown())

    def test_a_malformed_event_key_is_refused(self):
        app = FastAPI()
        app.include_router(routes_matches.router)
        with TestClient(app) as client, self.assertRaises(Exception):
            with client.websocket_connect("/matches/event/not-an-event/live") as socket:
                socket.receive_json()

    def test_an_event_we_dont_have_is_refused_and_never_watched(self):
        app = FastAPI()
        app.include_router(routes_matches.router)
        with mock.patch.object(routes_matches, "_event_known", return_value=False):
            with TestClient(app) as client, self.assertRaises(Exception):
                with client.websocket_connect("/matches/event/2026zzzz/live") as socket:
                    socket.receive_json()
        self.assertEqual(live_signal.watched_events(), [])

    def test_the_scheduler_refreshes_watched_events(self):
        self.redis.set("live:watched:2026txhou", "1")
        self.redis.set("live:watched:bad key", "1")
        with mock.patch.object(routes_matches, "_cached_live_results_for_event") as refresh:
            self.assertEqual(routes_matches.refresh_watched_live_results(), 1)
        self.assertEqual(refresh.call_args.args[1], "2026txhou")


if __name__ == "__main__":
    unittest.main()
