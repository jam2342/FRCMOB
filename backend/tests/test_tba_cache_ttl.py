import unittest
from unittest import mock

from app.tba import client as tba_client


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class TbaCacheTtlTests(unittest.TestCase):
    def setUp(self):
        tba_client._TBA_CACHE.clear()
        self.calls = 0

        def fake_request(method, url, **kwargs):
            self.calls += 1
            return _Response([{"call": self.calls}])

        self.session = mock.Mock()
        self.session.request.side_effect = fake_request

    def _client(self):
        with mock.patch.object(tba_client, "_thread_local_tba_session", return_value=self.session):
            return tba_client.TBAClient()

    def test_live_endpoints_refresh_after_seconds_not_minutes(self):
        client = self._client()
        with mock.patch.object(tba_client.time, "monotonic", return_value=1000.0):
            client.event_matches("2026test")
            client.event_matches("2026test")
        self.assertEqual(self.calls, 1)
        with mock.patch.object(tba_client.time, "monotonic", return_value=1000.0 + tba_client.TBA_LIVE_CACHE_TTL_SEC + 1):
            client.event_matches("2026test")
        self.assertEqual(self.calls, 2)

    def test_static_endpoints_keep_the_long_cache(self):
        client = self._client()
        with mock.patch.object(tba_client.time, "monotonic", return_value=1000.0):
            client.event_teams("2026test")
        with mock.patch.object(tba_client.time, "monotonic", return_value=1000.0 + 60):
            client.event_teams("2026test")
        self.assertEqual(self.calls, 1)


if __name__ == "__main__":
    unittest.main()
