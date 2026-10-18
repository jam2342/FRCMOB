from __future__ import annotations

import json
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import routes_telemetry
from app.core import security
from app.core.config import settings


class _FakePipe:
    def __init__(self, redis: "_FakeRedis") -> None:
        self.redis = redis
        self.ops: list = []

    def hincrby(self, key, field, amount):  # noqa: ANN001
        self.ops.append(("hincrby", key, field, amount))

    def expire(self, key, seconds):  # noqa: ANN001
        self.ops.append(("expire", key, seconds))

    def hexists(self, key, field):  # noqa: ANN001
        self.ops.append(("hexists", key, field))

    def execute(self):
        if self.ops and all(op[0] == "hexists" for op in self.ops):
            return [op[2] in self.redis.hashes.get(op[1], {}) for op in self.ops]
        for op in self.ops:
            if op[0] == "hincrby":
                bucket = self.redis.hashes.setdefault(op[1], {})
                bucket[op[2]] = str(int(bucket.get(op[2], "0")) + op[3])
        return []


class _FakeRedis:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def incr(self, key):  # noqa: ANN001
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key, seconds):  # noqa: ANN001
        return True

    def pipeline(self):
        return _FakePipe(self)

    def hgetall(self, key):  # noqa: ANN001
        return dict(self.hashes.get(key, {}))

    def hlen(self, key):  # noqa: ANN001
        return len(self.hashes.get(key, {}))


def _sample(**overrides):
    base = {"route": "/team-center", "metric": "LCP", "value": 1800, "device": "phone", "connection": "4g", "display": "browser", "source": "network"}
    base.update(overrides)
    return base


class TelemetryVitalsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = _FakeRedis()
        cache = mock.Mock(redis_client=self.redis)
        patcher = mock.patch.object(routes_telemetry, "get_cache", return_value=cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(routes_telemetry.router)
        self.client = TestClient(app)

    def _post(self, samples):
        return self.client.post("/telemetry/vitals", content=json.dumps({"samples": samples}), headers={"content-type": "text/plain"})

    def test_counts_valid_samples_into_buckets_and_drops_the_rest(self):
        resp = self._post([
            _sample(),
            _sample(metric="CLS", value=0.12),
            _sample(route="/team-center?team=frc254"),  # query strings carry keys
            _sample(route="/x/2026arc/frc254/extra"),
            _sample(metric="EVIL"),
            _sample(value=-5),
            _sample(value=float("nan")),
            _sample(device="fridge"),
        ])
        self.assertEqual(resp.status_code, 204)
        [day] = self.redis.hashes.values()
        self.assertEqual(day, {
            "/team-center|LCP|phone|4g|browser|network|2000": "1",
            "/team-center|CLS|phone|4g|browser|network|0.15": "1",
        })

    def test_rate_limit_drops_extra_beacons_silently(self):
        for _ in range(routes_telemetry.MAX_BEACONS_PER_MINUTE + 5):
            self.assertEqual(self._post([_sample()]).status_code, 204)
        [day] = self.redis.hashes.values()
        self.assertEqual(sum(int(v) for v in day.values()), routes_telemetry.MAX_BEACONS_PER_MINUTE)
        self.assertTrue(all(len(key.split(":")[-1]) == 24 for key in self.redis.counters))

    def test_a_full_day_keeps_counting_known_cells_but_adds_no_new_ones(self):
        with mock.patch.object(routes_telemetry, "MAX_FIELDS_PER_DAY", 1):
            self.assertEqual(self._post([_sample()]).status_code, 204)
            self.assertEqual(self._post([_sample(), _sample(route="/made-up-route")]).status_code, 204)
        [day] = self.redis.hashes.values()
        self.assertEqual(len(day), 1)
        self.assertEqual(list(day.values()), ["2"])

    def test_bad_bodies_are_refused(self):
        self.assertEqual(self.client.post("/telemetry/vitals", content="nope").status_code, 400)
        self.assertEqual(self._post([]).status_code, 400)
        big = json.dumps({"samples": [_sample()] * 400})
        self.assertEqual(self.client.post("/telemetry/vitals", content=big).status_code, 413)

    def test_summary_reads_percentiles_and_needs_admin(self):
        self._post([_sample(value=v) for v in (150, 450, 900, 1800, 2400, 2600, 9000, 9500)])
        with mock.patch.object(settings, "admin_api_key", "k" * 40), mock.patch.object(settings, "enforce_admin_auth_for_writes", True), mock.patch.object(settings, "app_env", "production"):
            self.assertEqual(self.client.get("/telemetry/vitals/summary").status_code, 403)
            resp = self.client.get("/telemetry/vitals/summary", headers={"X-Admin-Key": "k" * 40})
        self.assertEqual(resp.status_code, 200, resp.text)
        [row] = resp.json()["rows"]
        self.assertEqual((row["route"], row["metric"], row["samples"]), ("/team-center", "LCP", 8))
        self.assertEqual((row["p50"], row["p75"], row["p95"]), (2000.0, 3000.0, 10000.0))

    def test_write_guard_lets_anonymous_beacons_through(self):
        scope = {"type": "http", "method": "POST", "path": "/telemetry/vitals", "headers": [], "query_string": b""}
        with mock.patch.object(settings, "enforce_admin_auth_for_writes", True), mock.patch.object(settings, "admin_api_key", "k" * 40), mock.patch.object(settings, "app_env", "production"):
            security.enforce_write_request_access(Request(scope))
            with self.assertRaises(Exception):
                security.enforce_write_request_access(Request({**scope, "path": "/maintenance/rebuild"}))


if __name__ == "__main__":
    unittest.main()
