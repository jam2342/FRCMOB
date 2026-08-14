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
