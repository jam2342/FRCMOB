from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.clients import statbotics as statbotics_client


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = int(status_code)
        self.text = text

    def json(self):
        return {}


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls = 0

    async def get(self, _url: str, headers=None):  # noqa: ANN001
        self.calls += 1
        return self._response


class StatboticsClientTests(unittest.TestCase):
    def setUp(self) -> None:
        statbotics_client._CACHE.clear()
        statbotics_client._CIRCUIT.clear()

    def test_fetch_json_4xx_is_not_retried(self):
        fake_client = _FakeClient(_FakeResponse(404, "not found"))
        with patch(
            "app.services.clients.statbotics._get_client",
            new=AsyncMock(return_value=fake_client),
        ):
            with self.assertRaises(RuntimeError) as exc_info:
                asyncio.run(statbotics_client._fetch_json_with_retry("/team/999999", max_retries=3))
        self.assertIn("Statbotics API returned 404", str(exc_info.exception))
        self.assertEqual(fake_client.calls, 1)


if __name__ == "__main__":
    unittest.main()


class StatboticsClientLoopTests(unittest.TestCase):
    # Sync callers (ratings in the worker, scheduler threads) each run on a fresh loop;
    # sharing one AsyncClient across loops failed with "Event loop is closed".
    def test_each_loop_gets_its_own_client_and_run_sync_closes_it(self):
        from app.services.clients import statbotics as client_module

        seen = []

        async def grab():
            client = await client_module._get_client()
            seen.append(client)
            return client

        first = client_module.run_sync(grab())
        second = client_module.run_sync(grab())
        self.assertIsNot(first, second)
        self.assertTrue(first.is_closed)
        self.assertTrue(second.is_closed)
        self.assertEqual(len(client_module._CLIENTS), 0)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key):  # noqa: ANN001
        return self.store.get(key)

    def set(self, key, value, nx=False, ex=None):  # noqa: ANN001
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    def incr(self, key):  # noqa: ANN001
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    def expire(self, key, seconds):  # noqa: ANN001
        return True

    def delete(self, *keys):  # noqa: ANN001
        for key in keys:
            self.store.pop(key, None)
        return len(keys)


class StatboticsSharedCircuitTests(unittest.TestCase):
    # During the Oct 2026 outage each process rediscovered it on its own and every
    # cold team page waited 13-23 s; the breaker is now shared and grows.
    def setUp(self) -> None:
        statbotics_client._CACHE.clear()
        statbotics_client._CIRCUIT.clear()
        self.redis = _FakeRedis()
        self.redis_patch = patch.object(statbotics_client, "_shared_redis", return_value=self.redis)
        self.redis_patch.start()

    def tearDown(self) -> None:
        self.redis_patch.stop()
        statbotics_client._CIRCUIT.clear()

    def _fail(self, endpoint: str, times: int) -> None:
        for _ in range(times):
            statbotics_client._circuit_record_failure(endpoint, error="500")

    def test_open_circuit_is_seen_by_another_process(self):
        self._fail("team", statbotics_client._circuit_threshold())
        statbotics_client._CIRCUIT.clear()  # another worker: no memory of the outage
        is_open, remaining = statbotics_client._circuit_open("team")
        self.assertTrue(is_open)
        self.assertGreater(remaining, 60)

    def test_cooldown_doubles_while_the_outage_lasts_and_is_capped(self):
        threshold = statbotics_client._circuit_threshold()
        cooldowns = []
        for _ in range(7):
            self._fail("team", threshold)
            cooldowns.append(round(statbotics_client._CIRCUIT["team"]["open_until_ts"] - __import__("time").time()))
        self.assertEqual(cooldowns[:3], [90, 180, 360])
        self.assertEqual(cooldowns[-1], 1800)

    def test_only_one_probe_after_cooldown_and_a_failed_probe_reopens(self):
        self._fail("team", statbotics_client._circuit_threshold())
        # Cooldown over, outage still remembered.
        self.redis.delete("statbotics:circuit:team:open_until")
        statbotics_client._CIRCUIT["team"]["open_until_ts"] = 0.0
        self.assertTrue(statbotics_client._circuit_claim_probe("team"))
        self.assertFalse(statbotics_client._circuit_claim_probe("team"))
        statbotics_client._circuit_record_failure("team", error="500")
        self.assertTrue(statbotics_client._circuit_open("team")[0])
        self.assertEqual(statbotics_client._CIRCUIT["team"]["opens"], 2)

    def test_success_ends_the_outage(self):
        self._fail("team", statbotics_client._circuit_threshold())
        statbotics_client._circuit_record_success("team")
        self.assertFalse(statbotics_client._circuit_open("team")[0])
        self.assertTrue(statbotics_client._circuit_claim_probe("team"))
        self.assertTrue(statbotics_client._circuit_claim_probe("team"))
        self.assertEqual(self.redis.store, {})

    def test_a_4xx_closes_a_half_open_breaker(self):
        self._fail("team", statbotics_client._circuit_threshold())
        self.redis.delete("statbotics:circuit:team:open_until")
        statbotics_client._CIRCUIT["team"]["open_until_ts"] = 0.0
        fake_client = _FakeClient(_FakeResponse(404, "not found"))
        with patch.object(statbotics_client, "_get_client", new=AsyncMock(return_value=fake_client)):
            with self.assertRaises(RuntimeError):
                asyncio.run(statbotics_client._fetch_json_with_retry("/team/999999"))
        self.assertNotIn("statbotics:circuit:team:opens", self.redis.store)

    def test_slow_5xx_is_not_retried(self):
        class _SlowClient(_FakeClient):
            async def get(self, _url, headers=None):  # noqa: ANN001
                self.calls += 1
                return self._response

        fake_client = _SlowClient(_FakeResponse(500, "down"))
        ticks = {"now": 0.0}

        def slow_clock():
            # Every reading is 5 s later: each attempt looks like a slow 500.
            ticks["now"] += 5.0
            return ticks["now"]

        with patch.object(statbotics_client, "_get_client", new=AsyncMock(return_value=fake_client)), patch.object(
            statbotics_client.time, "perf_counter", side_effect=slow_clock
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(statbotics_client._fetch_json_with_retry("/team/254", max_retries=3))
        self.assertEqual(fake_client.calls, 1)

    def test_quick_5xx_is_retried_once(self):
        fake_client = _FakeClient(_FakeResponse(500, "blip"))
        with patch.object(statbotics_client, "_get_client", new=AsyncMock(return_value=fake_client)):
            with self.assertRaises(RuntimeError):
                asyncio.run(statbotics_client._fetch_json_with_retry("/team/254", max_retries=2))
        self.assertEqual(fake_client.calls, 2)


class StatboticsBudgetTests(unittest.TestCase):
    def test_within_budget_times_out_but_lets_the_call_finish(self):
        finished = []

        async def slow():
            await asyncio.sleep(0.2)
            finished.append(True)
            return "late"

        async def scenario():
            with self.assertRaises(asyncio.TimeoutError):
                await statbotics_client.within_budget(slow(), 0.05)
            await asyncio.sleep(0.3)

        asyncio.run(scenario())
        self.assertEqual(finished, [True])

    def test_within_budget_returns_fast_results(self):
        async def fast():
            return 7

        self.assertEqual(asyncio.run(statbotics_client.within_budget(fast(), 1.0)), 7)
