import unittest
from unittest import mock

from fastapi import HTTPException

from app.api import routes_events
from app.core import security
from app.core.config import settings
from app.tba import client as tba_client
from app.tba.client import TBAClient, TBAClientError


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, int] = {}

    def get(self, key):
        return self.values.get(key)

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, *_args):
        return True


class AdminKeyBudgetTests(unittest.TestCase):
    def setUp(self):
        self._prior = (settings.enforce_admin_auth_for_writes, settings.admin_api_key)
        settings.enforce_admin_auth_for_writes = True
        settings.admin_api_key = "the-real-admin-key"
        self.redis = _FakeRedis()
        patcher = mock.patch.object(security, "_admin_key_failure_redis", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        settings.enforce_admin_auth_for_writes, settings.admin_api_key = self._prior

    def test_wrong_keys_are_counted_but_never_lock_the_real_key_out(self):
        # 20 bad headers on /health used to lock the operator out for 15 minutes.
        self.assertTrue(security.admin_api_key_authorized("the-real-admin-key"))
        for attempt in range(security.ADMIN_KEY_FAILURE_BUDGET * 2):
            self.assertFalse(security.admin_api_key_authorized(f"guess-{attempt}"))
        self.assertEqual(sum(self.redis.values.values()), security.ADMIN_KEY_FAILURE_BUDGET * 2)
        self.assertTrue(security.admin_api_key_authorized("the-real-admin-key"))

    def test_the_header_path_counts_too(self):
        request = mock.Mock(headers={"X-Admin-Key": "wrong"})
        for _ in range(security.ADMIN_KEY_FAILURE_BUDGET):
            self.assertFalse(security.request_has_explicit_admin_credential(request))
        self.assertEqual(sum(self.redis.values.values()), security.ADMIN_KEY_FAILURE_BUDGET)
        request.headers = {"X-Admin-Key": "the-real-admin-key"}
        self.assertTrue(security.request_has_explicit_admin_credential(request))

    def test_production_refuses_a_short_admin_key(self):
        from app import main

        with (
            mock.patch.object(type(settings), "is_production_like", new_callable=mock.PropertyMock, return_value=True),
            mock.patch.object(settings, "admin_api_key", "short-key"),
        ):
            errors = main._startup_env_validation_report()["errors"]
        self.assertTrue(any("ADMIN_API_KEY must be at least" in error for error in errors), errors)

    def test_without_redis_the_key_still_works(self):
        with mock.patch.object(security, "_admin_key_failure_redis", return_value=None):
            self.assertFalse(security.admin_api_key_authorized("wrong"))
            self.assertTrue(security.admin_api_key_authorized("the-real-admin-key"))


class TbaEventKeyTests(unittest.TestCase):
    def test_only_well_formed_event_keys_reach_tba(self):
        self.assertEqual(routes_events._tba_event_key_or_404("2026TXHOU"), "2026txhou")
        for bad in ("..", "2026txhou?x=1", "x", "1800abc", "2026" + "a" * 40, "2026tx%2F.."):
            with self.assertRaises(HTTPException) as raised:
                routes_events._tba_event_key_or_404(bad)
            self.assertEqual(raised.exception.status_code, 404)

    def test_a_tba_404_is_remembered_briefly(self):
        session = mock.Mock()
        session.request.return_value = mock.Mock(status_code=404)
        tba = TBAClient()
        tba.s = session
        url = "https://www.thebluealliance.com/api/v3/event/2026nosuchevent/rankings"
        for _ in range(3):
            with self.assertRaises(TBAClientError) as raised:
                tba._request("GET", url)
            self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(session.request.call_count, 1)
        tba_client._TBA_MISSING.clear()


if __name__ == "__main__":
    unittest.main()
