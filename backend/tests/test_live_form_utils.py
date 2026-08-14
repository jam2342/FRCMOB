import unittest

try:
    from app.api.routes_matches import (
        _form_label,
        _is_match_completed,
        _team_result_for_match,
    )

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - dependency-gated import
    _IMPORT_ERROR = exc


def _mock_match(
    *,
    winner: str = "",
    red_score: int = -1,
    blue_score: int = -1,
    red_teams: list[str] | None = None,
    blue_teams: list[str] | None = None,
) -> dict:
    return {
        "winning_alliance": winner,
        "alliances": {
            "red": {
                "team_keys": red_teams or ["frc1", "frc2", "frc3"],
                "score": red_score,
            },
            "blue": {
                "team_keys": blue_teams or ["frc4", "frc5", "frc6"],
                "score": blue_score,
            },
        },
    }


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional api deps unavailable: {_IMPORT_ERROR}")
class LiveFormUtilsTests(unittest.TestCase):
    def test_match_completion_detection(self):
        self.assertFalse(_is_match_completed(_mock_match()))
        self.assertTrue(_is_match_completed(_mock_match(winner="red")))
        self.assertTrue(_is_match_completed(_mock_match(red_score=120, blue_score=113)))

    def test_team_result_for_match(self):
        match = _mock_match(winner="blue")
        self.assertEqual(_team_result_for_match(match, "frc4"), "W")
        self.assertEqual(_team_result_for_match(match, "frc2"), "L")

        tie_match = _mock_match(winner="", red_score=88, blue_score=88)
        self.assertEqual(_team_result_for_match(tie_match, "frc1"), "T")

    def test_form_label(self):
        self.assertEqual(_form_label([]), "insufficient_data")
        self.assertEqual(_form_label(["W", "W", "L"]), "in_form")
        self.assertEqual(_form_label(["L", "L", "T"]), "cold")
        self.assertEqual(_form_label(["W", "L", "T"]), "mixed")


if __name__ == "__main__":
    unittest.main()
