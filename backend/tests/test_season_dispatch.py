from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import phase_windows
from app.services.game_config import load_game_config
from app.services.scoring.truth import _extract_score_breakdown_truth_rows
from app.services.season_config import UnsupportedSeasonRulesError


class SeasonDispatchTests(unittest.TestCase):
    def test_future_phase_rules_fail_closed(self):
        future = load_game_config().model_copy(update={"season_year": 2027})
        with patch.object(phase_windows, "load_game_config", return_value=future):
            with self.assertRaisesRegex(UnsupportedSeasonRulesError, "season 2027"):
                phase_windows.compute_phase_windows_payload(None)

    def test_future_score_breakdown_rules_fail_closed(self):
        with self.assertRaisesRegex(UnsupportedSeasonRulesError, "season 2027"):
            _extract_score_breakdown_truth_rows(
                match={},
                season_year=2027,
                context={},
            )


if __name__ == "__main__":
    unittest.main()
