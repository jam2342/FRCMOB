# Tests for app.services.team_utils – shared team/event utility helpers.

import unittest

from app.services.team_utils import (
    _model_uses_calibrated_public_scale,
    _region_label,
    _team_number_from_team_key,
)

class RegionLabelTests(unittest.TestCase):
    def test_usa_with_state(self):
        self.assertEqual(_region_label("TX", "USA"), "TX")

    def test_canada_with_province(self):
        self.assertEqual(_region_label("ON", "Canada"), "ON")

    def test_usa_without_state(self):
        self.assertEqual(_region_label(None, "USA"), "USA")

    def test_usa_empty_state(self):
        self.assertEqual(_region_label("", "USA"), "USA")

    def test_usa_whitespace_state(self):
        self.assertEqual(_region_label("   ", "USA"), "USA")

    def test_foreign_country(self):
        self.assertEqual(_region_label(None, "Israel"), "Israel")

    def test_foreign_country_with_state(self):
        # Non-US/Canada countries return country, not state
        self.assertEqual(_region_label("Tel Aviv", "Israel"), "Israel")

    def test_none_country_none_state(self):
        self.assertEqual(_region_label(None, None), "Unknown")

    def test_empty_everything(self):
        self.assertEqual(_region_label("", ""), "Unknown")

    def test_whitespace_country(self):
        self.assertEqual(_region_label(None, "  "), "Unknown")

    def test_canada_without_province(self):
        self.assertEqual(_region_label(None, "Canada"), "Canada")

class ModelUsesCalibTests(unittest.TestCase):
    def test_calibrated_scale_substring(self):
        self.assertTrue(_model_uses_calibrated_public_scale("v2_calibrated_scale"))

    def test_rating_v11_prefix(self):
        self.assertTrue(_model_uses_calibrated_public_scale("rating_v11_base"))

    def test_plain_model_version(self):
        self.assertFalse(_model_uses_calibrated_public_scale("rating_v10"))

    def test_non_string_input(self):
        self.assertFalse(_model_uses_calibrated_public_scale(42))
        self.assertFalse(_model_uses_calibrated_public_scale(None))

    def test_empty_string(self):
        self.assertFalse(_model_uses_calibrated_public_scale(""))

    def test_case_insensitive(self):
        self.assertTrue(_model_uses_calibrated_public_scale("RATING_V11_UPPER"))
        self.assertTrue(_model_uses_calibrated_public_scale("Calibrated_Scale"))

class TeamNumberFromKeyTests(unittest.TestCase):
    def test_standard_key(self):
        self.assertEqual(_team_number_from_team_key("frc254"), 254)

    def test_uppercase(self):
        self.assertEqual(_team_number_from_team_key("FRC254"), 254)

    def test_number_only(self):
        self.assertEqual(_team_number_from_team_key("254"), 254)

    def test_frc_prefix_only(self):
        self.assertEqual(_team_number_from_team_key("frc"), 0)

    def test_empty_string(self):
        self.assertEqual(_team_number_from_team_key(""), 0)

    def test_invalid_chars(self):
        self.assertEqual(_team_number_from_team_key("frcabc"), 0)

    def test_whitespace(self):
        self.assertEqual(_team_number_from_team_key(" frc1 "), 1)

    def test_leading_zeros(self):
        self.assertEqual(_team_number_from_team_key("frc0254"), 254)

if __name__ == "__main__":
    unittest.main()
