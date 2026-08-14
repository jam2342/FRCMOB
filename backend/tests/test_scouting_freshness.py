import time
import unittest

from app.core.config import settings
from app.services.scouting_rooms.scope import build_data_freshness_payload, event_year_from_key


class ScoutingFreshnessTests(unittest.TestCase):
    def test_event_year_from_key(self):
        self.assertEqual(event_year_from_key("2026txhou"), 2026)
        self.assertEqual(event_year_from_key("2025txabc_qm1"), 2025)
        self.assertIsNone(event_year_from_key("txhou2026"))
        self.assertIsNone(event_year_from_key(None))

    def test_freshness_warns_when_scope_is_not_active(self):
        payload = build_data_freshness_payload(
            season_scope={
                "season_year": 2025,
                "active_season_year": 2026,
                "source": "latest_available",
                "uses_active_season": False,
            },
            match_times=[],
            analyzed_matches=0,
        )
        self.assertTrue(payload["is_outdated"])
        self.assertTrue(any("Using 2025 season data" in message for message in payload["warnings"]))
        self.assertTrue(any("No analyzed matches available for 2025." in message for message in payload["warnings"]))

    def test_freshness_warns_for_old_data(self):
        days_old = max(2, int(settings.scouting_data_outdated_days) + 5)
        latest_match_time = int(time.time()) - (days_old * 86400)
        payload = build_data_freshness_payload(
            season_scope={
                "season_year": 2026,
                "active_season_year": 2026,
                "source": "active_season",
                "uses_active_season": True,
            },
            match_times=[latest_match_time],
            analyzed_matches=1,
        )
        self.assertTrue(payload["is_outdated"])
        self.assertTrue(any("Latest analyzed match is" in message for message in payload["warnings"]))


if __name__ == "__main__":
    unittest.main()
