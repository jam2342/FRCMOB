# Tests for app.services.intel.helpers – cache key/token generation.

import unittest

from app.services.intel.helpers import (
    _cache_key,
    _event_intel_cache_token,
    _normalize_team_key,
    _team_intel_cache_token,
)

class NormalizeTeamKeyTests(unittest.TestCase):
    def test_lowercase_frc_prefix(self):
        self.assertEqual(_normalize_team_key("frc254"), "frc254")

    def test_uppercase_frc_prefix(self):
        self.assertEqual(_normalize_team_key("FRC254"), "frc254")

    def test_number_only(self):
        self.assertEqual(_normalize_team_key("254"), "frc254")

    def test_leading_zeros_stripped(self):
        self.assertEqual(_normalize_team_key("frc0254"), "frc254")

    def test_whitespace_stripped(self):
        self.assertEqual(_normalize_team_key("  frc1  "), "frc1")

    def test_non_numeric_suffix(self):
        result = _normalize_team_key("frcabc")
        self.assertEqual(result, "frcabc")

    def test_number_without_prefix(self):
        self.assertEqual(_normalize_team_key("1"), "frc1")

class CacheKeyTests(unittest.TestCase):
    def test_basic_format(self):
        key = _cache_key("team", "frc254|2025txhou")
        self.assertTrue(key.startswith("intel:v1:team:"))
        self.assertIn("frc254", key)

    def test_whitespace_normalized(self):
        self.assertEqual(
            _cache_key("  team  ", "  token  "),
            _cache_key("team", "token"),
        )

    def test_case_insensitive(self):
        self.assertEqual(_cache_key("Team", "TOKEN"), _cache_key("team", "token"))

class TeamIntelCacheTokenTests(unittest.TestCase):
    def _token(self, **overrides):
        defaults = dict(
            team_key="frc254",
            event_key="2025txhou",
            preferred_year=2025,
            fallback_year=2024,
            include_tba=True,
            include_statbotics=True,
            allow_season_fallback=False,
            auto_heal_ratings=True,
        )
        defaults.update(overrides)
        return _team_intel_cache_token(**defaults)

    def test_contains_team_key(self):
        self.assertIn("frc254", self._token())

    def test_contains_event_key(self):
        self.assertIn("2025txhou", self._token())

    def test_none_event_key_uses_dash(self):
        token = self._token(event_key=None)
        self.assertIn("|-|", token)

    def test_empty_event_key_uses_dash(self):
        token = self._token(event_key="   ")
        self.assertIn("|-|", token)

    def test_boolean_flags_encoded(self):
        token = self._token(include_tba=True, include_statbotics=False)
        self.assertIn("tba=1", token)
        self.assertIn("sb=0", token)

    def test_different_flags_produce_different_tokens(self):
        t1 = self._token(auto_heal_ratings=True)
        t2 = self._token(auto_heal_ratings=False)
        self.assertNotEqual(t1, t2)

    def test_deterministic(self):
        self.assertEqual(self._token(), self._token())

class EventIntelCacheTokenTests(unittest.TestCase):
    def _token(self, **overrides):
        defaults = dict(
            event_key="2025txhou",
            include_tba=True,
            include_statbotics=True,
            auto_heal_ratings=True,
            include_season_fallback=False,
            include_rating_details=True,
            include_rating_signals=False,
        )
        defaults.update(overrides)
        return _event_intel_cache_token(**defaults)

    def test_contains_event_key(self):
        self.assertIn("2025txhou", self._token())

    def test_boolean_flags(self):
        token = self._token(include_rating_details=True, include_rating_signals=False)
        self.assertIn("rd=1", token)
        self.assertIn("rs=0", token)

    def test_different_events_different_tokens(self):
        t1 = self._token(event_key="2025txhou")
        t2 = self._token(event_key="2025casj")
        self.assertNotEqual(t1, t2)

if __name__ == "__main__":
    unittest.main()
