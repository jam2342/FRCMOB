from __future__ import annotations

import unittest

from app.api.routes_maintenance import _normalize_train_model_key


class MaintenanceTrainModelKeyTests(unittest.TestCase):
    def test_allows_auto_scout_field_with_inline_field(self):
        key = _normalize_train_model_key("auto_scout_field:offense_level_1_5")
        self.assertEqual(key, "auto_scout_field:offense_level_1_5")

    def test_allows_auto_scout_field_with_separate_field_name(self):
        key = _normalize_train_model_key("auto_scout_field", field_name="defense_level_1_5")
        self.assertEqual(key, "auto_scout_field:defense_level_1_5")

    def test_rejects_unknown_model_key(self):
        with self.assertRaises(ValueError):
            _normalize_train_model_key("something_else")


if __name__ == "__main__":
    unittest.main()
