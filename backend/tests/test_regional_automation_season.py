from __future__ import annotations

import unittest
from unittest import mock

from app.core.config import settings
from app.services import season_config


class RegionalAutomationSeasonTests(unittest.TestCase):
    def test_configured_season_wins(self):
        with mock.patch.object(settings, "automation_regional_halfday_season", 2026):
            self.assertEqual(season_config.regional_automation_season(), 2026)

    def test_unset_falls_back_to_the_game_config_not_the_calendar(self):
        with mock.patch.object(settings, "automation_regional_halfday_season", 0):
            self.assertEqual(season_config.regional_automation_season(), season_config.CURRENT_SEASON_YEAR)


if __name__ == "__main__":
    unittest.main()
