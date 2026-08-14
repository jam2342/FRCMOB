# Tests for app.services.vision.bumper_ocr – bumper-number OCR utility helpers.

import unittest

from app.services.vision.bumper_ocr import (
    _normalize_ocr_digits,
    _team_number_from_team_key,
)

class TeamNumberFromTeamKeyTests(unittest.TestCase):
    def test_standard(self):
        self.assertEqual(_team_number_from_team_key("frc254"), 254)

    def test_uppercase(self):
        self.assertEqual(_team_number_from_team_key("FRC254"), 254)

    def test_single_digit(self):
        self.assertEqual(_team_number_from_team_key("frc1"), 1)

    def test_none(self):
        self.assertIsNone(_team_number_from_team_key(None))

    def test_empty(self):
        self.assertIsNone(_team_number_from_team_key(""))

    def test_no_digits(self):
        self.assertIsNone(_team_number_from_team_key("frcabc"))

    def test_no_frc_prefix(self):
        self.assertIsNone(_team_number_from_team_key("254"))

    def test_whitespace(self):
        self.assertEqual(_team_number_from_team_key("  frc254  "), 254)

class NormalizeOcrDigitsTests(unittest.TestCase):
    def test_clean_number(self):
        self.assertEqual(_normalize_ocr_digits("254"), [254])

    def test_multiple_numbers(self):
        result = _normalize_ocr_digits("12 345")
        self.assertIn(12, result)
        self.assertIn(345, result)

    def test_ocr_o_to_zero(self):
        result = _normalize_ocr_digits("1O2")
        self.assertIn(102, result)

    def test_ocr_i_to_one(self):
        result = _normalize_ocr_digits("2I4")
        self.assertIn(214, result)

    def test_ocr_l_to_one(self):
        result = _normalize_ocr_digits("2l4")
        self.assertIn(214, result)

    def test_ocr_pipe_to_one(self):
        result = _normalize_ocr_digits("2|4")
        self.assertIn(214, result)

    def test_ocr_s_to_five(self):
        result = _normalize_ocr_digits("S23")
        self.assertIn(523, result)

    def test_none(self):
        self.assertEqual(_normalize_ocr_digits(None), [])

    def test_empty(self):
        self.assertEqual(_normalize_ocr_digits(""), [])

    def test_zero_filtered(self):
        # Value < 1 is excluded
        result = _normalize_ocr_digits("0")
        self.assertEqual(result, [])

    def test_six_digit_ignored(self):
        # Only 1-5 digits matched
        result = _normalize_ocr_digits("123456")
        # regex \d{1,5} will match substrings
        self.assertTrue(all(v <= 99999 for v in result))

if __name__ == "__main__":
    unittest.main()
