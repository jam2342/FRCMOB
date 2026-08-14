# Contract tests for external data adapters.
#
# These tests verify that the adapter clients parse upstream API responses
# into the shapes that downstream consumers depend on.  They mock HTTP to
# avoid network calls while asserting the structural contract.
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.clients import statbotics as statbotics_client
from app.tba.client import TBAClient

# ---------------------------------------------------------------------------
# Minimal upstream response fixtures
# ---------------------------------------------------------------------------

TBA_EVENT_FIXTURE: dict = {
    "key": "2026txhou",
    "name": "Houston Regional",
    "year": 2026,
    "city": "Houston",
    "state_prov": "TX",
    "country": "USA",
    "event_type": 0,
}

TBA_TEAM_FIXTURE: dict = {
    "key": "frc118",
    "team_number": 118,
    "nickname": "Robonauts",
    "city": "Houston",
    "state_prov": "TX",
    "country": "USA",
}

TBA_MATCH_FIXTURE: dict = {
    "key": "2026txhou_qm1",
    "comp_level": "qm",
    "set_number": 1,
    "match_number": 1,
    "event_key": "2026txhou",
    "time": 1710000000,
    "alliances": {
        "red": {"team_keys": ["frc118", "frc148", "frc3310"], "score": 45},
        "blue": {"team_keys": ["frc254", "frc1114", "frc2056"], "score": 60},
    },
    "score_breakdown": {
        "red": {"totalPoints": 45},
        "blue": {"totalPoints": 60},
    },
    "videos": [{"type": "youtube", "key": "abc123"}],
}

STATBOTICS_TEAM_EVENT_FIXTURE: dict = {
    "team": 118,
    "event": "2026txhou",
    "epa": {"total_points": {"mean": 42.5}},
    "record": {"wins": 7, "losses": 3, "ties": 0},
}

# ---------------------------------------------------------------------------
# TBA Client contract tests
# ---------------------------------------------------------------------------

class TBAContractTests(unittest.TestCase):
    # Verify that TBAClient methods return data with the expected shape.

    def _make_client_with_mock(self, response_data):
        # Create a TBAClient whose HTTP layer returns *response_data*.
        client = TBAClient.__new__(TBAClient)
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()
        mock_session.request.return_value = mock_response
        client.s = mock_session
        client._headers = {"X-TBA-Auth-Key": "test"}
        return client

    def test_event_returns_dict_with_required_keys(self):
        client = self._make_client_with_mock(TBA_EVENT_FIXTURE)
        result = client.event("2026txhou")
        self.assertIsInstance(result, dict)
        for key in ("key", "name", "year"):
            self.assertIn(key, result, f"Missing required key: {key}")
        self.assertIsInstance(result["year"], int)

    def test_event_teams_returns_list_of_team_dicts(self):
        client = self._make_client_with_mock([TBA_TEAM_FIXTURE])
        result = client.event_teams("2026txhou")
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        team = result[0]
        for key in ("key", "team_number"):
            self.assertIn(key, team, f"Missing required team key: {key}")
        self.assertIsInstance(team["team_number"], int)
        self.assertTrue(team["key"].startswith("frc"))

    def test_event_matches_returns_list_with_alliances(self):
        client = self._make_client_with_mock([TBA_MATCH_FIXTURE])
        result = client.event_matches("2026txhou")
        self.assertIsInstance(result, list)
        match = result[0]
        for key in ("key", "comp_level", "alliances"):
            self.assertIn(key, match, f"Missing required match key: {key}")
        self.assertIn("red", match["alliances"])
        self.assertIn("blue", match["alliances"])
        for alliance in ("red", "blue"):
            self.assertIn("team_keys", match["alliances"][alliance])
            self.assertIsInstance(match["alliances"][alliance]["team_keys"], list)
            self.assertEqual(len(match["alliances"][alliance]["team_keys"]), 3)

    def test_match_videos_contract(self):
        client = self._make_client_with_mock(TBA_MATCH_FIXTURE)
        result = client.match("2026txhou_qm1")
        self.assertIn("videos", result)
        self.assertIsInstance(result["videos"], list)
        if result["videos"]:
            video = result["videos"][0]
            self.assertIn("type", video)
            self.assertIn("key", video)

    def test_match_score_breakdown_contract(self):
        client = self._make_client_with_mock(TBA_MATCH_FIXTURE)
        result = client.match("2026txhou_qm1")
        self.assertIn("score_breakdown", result)
        breakdown = result["score_breakdown"]
        self.assertIn("red", breakdown)
        self.assertIn("blue", breakdown)

# ---------------------------------------------------------------------------
# Statbotics Client contract tests
# ---------------------------------------------------------------------------

class _FakeStatboticsResponse:
    def __init__(self, data):
        self.status_code = 200
        self._data = data

    def json(self):
        return self._data

class _FakeStatboticsHTTPClient:
    def __init__(self, data):
        self._response = _FakeStatboticsResponse(data)

    async def get(self, _url, headers=None):
        return self._response

class StatboticsContractTests(unittest.TestCase):
    # Verify Statbotics adapter parses responses correctly.

    def setUp(self):
        statbotics_client._CACHE.clear()
        statbotics_client._CIRCUIT.clear()

    def test_get_team_event_returns_epa_structure(self):
        fake_client = _FakeStatboticsHTTPClient(STATBOTICS_TEAM_EVENT_FIXTURE)
        with patch(
            "app.services.clients.statbotics._get_client",
            new=AsyncMock(return_value=fake_client),
        ):
            result = asyncio.run(statbotics_client.get_team_event(118, "2026txhou"))
        self.assertIsInstance(result, dict)
        self.assertIn("epa", result)
        epa = result["epa"]
        self.assertIn("total_points", epa)
        self.assertIn("mean", epa["total_points"])

    def test_get_team_returns_dict(self):
        fixture = {"team": 118, "name": "Robonauts", "epa": {"total_points": {"mean": 42.0}}}
        fake_client = _FakeStatboticsHTTPClient(fixture)
        with patch(
            "app.services.clients.statbotics._get_client",
            new=AsyncMock(return_value=fake_client),
        ):
            result = asyncio.run(statbotics_client.get_team(118))
        self.assertIsInstance(result, dict)

    def test_get_event_returns_dict(self):
        fixture = {"key": "2026txhou", "name": "Houston Regional"}
        fake_client = _FakeStatboticsHTTPClient(fixture)
        with patch(
            "app.services.clients.statbotics._get_client",
            new=AsyncMock(return_value=fake_client),
        ):
            result = asyncio.run(statbotics_client.get_event("2026txhou"))
        self.assertIsInstance(result, dict)

# ---------------------------------------------------------------------------
# FIRST Events Client contract tests
# ---------------------------------------------------------------------------

class FIRSTEventsContractTests(unittest.TestCase):
    # Verify FIRSTEventsClient parses upstream schedule/alliance data.

    def test_event_schedule_parses_schedule_list(self):
        from app.services.events.first_events_client import FIRSTEventsClient

        fixture = {
            "Schedule": [
                {
                    "matchNumber": 1,
                    "description": "Qualification 1",
                    "startTime": "2026-03-15T09:00:00",
                    "teams": [
                        {"teamNumber": 118, "station": "Red1"},
                        {"teamNumber": 148, "station": "Red2"},
                        {"teamNumber": 3310, "station": "Red3"},
                        {"teamNumber": 254, "station": "Blue1"},
                        {"teamNumber": 1114, "station": "Blue2"},
                        {"teamNumber": 2056, "station": "Blue3"},
                    ],
                }
            ]
        }
        # Mock the _get method to return our fixture
        client = FIRSTEventsClient.__new__(FIRSTEventsClient)
        client._get = MagicMock(return_value=(fixture, None, False))

        result, _last_modified, _not_modified = client.event_schedule(
            season=2026, event_code="txhou"
        )
        self.assertIn("Schedule", result)
        schedule = result["Schedule"]
        self.assertIsInstance(schedule, list)
        self.assertTrue(len(schedule) > 0)
        match_entry = schedule[0]
        self.assertIn("matchNumber", match_entry)
        self.assertIn("teams", match_entry)
        self.assertEqual(len(match_entry["teams"]), 6)

    def test_event_alliances_parses_alliances_list(self):
        from app.services.events.first_events_client import FIRSTEventsClient

        fixture = {
            "Alliances": [
                {
                    "number": 1,
                    "captain": 118,
                    "round1": 254,
                    "round2": 148,
                    "round3": None,
                    "backup": None,
                    "backupReplaced": None,
                }
            ]
        }
        client = FIRSTEventsClient.__new__(FIRSTEventsClient)
        client._get = MagicMock(return_value=(fixture, None, False))

        result, _last_modified, _not_modified = client.event_alliances(
            season=2026, event_code="txhou"
        )
        self.assertIn("Alliances", result)
        alliances = result["Alliances"]
        self.assertIsInstance(alliances, list)
        self.assertTrue(len(alliances) > 0)
        alliance = alliances[0]
        self.assertIn("number", alliance)
        self.assertIn("captain", alliance)

if __name__ == "__main__":
    unittest.main()
