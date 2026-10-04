from __future__ import annotations

import os
import unittest

from app.core.config import assert_no_legacy_texas_settings


class LegacyTexasGuardTests(unittest.TestCase):
    def test_clean_environment_passes(self):
        # The committed .env is clean; the guard must not false-positive.
        assert_no_legacy_texas_settings()

    def test_stray_texas_env_var_hard_fails(self):
        os.environ["AUTOMATION_TEXAS_ENABLED"] = "true"
        try:
            with self.assertRaises(RuntimeError) as ctx:
                assert_no_legacy_texas_settings()
            self.assertIn("AUTOMATION_TEXAS_ENABLED", str(ctx.exception))
            self.assertIn("REGIONAL", str(ctx.exception))
        finally:
            os.environ.pop("AUTOMATION_TEXAS_ENABLED", None)

    def test_word_boundary_no_false_positive(self):
        os.environ["MY_TEXASTYLE_FLAG"] = "1"
        try:
            assert_no_legacy_texas_settings()  # must NOT raise
        finally:
            os.environ.pop("MY_TEXASTYLE_FLAG", None)


class DatabaseLocalhostGuardTests(unittest.TestCase):
    def test_localhost_detection(self):
        from app.main import _database_url_targets_localhost

        self.assertTrue(
            _database_url_targets_localhost(
                "postgresql+psycopg://postgres:postgres@localhost:5432/frc"
            )
        )
        self.assertTrue(
            _database_url_targets_localhost("postgresql+psycopg://u:p@127.0.0.1/db")
        )
        self.assertFalse(
            _database_url_targets_localhost(
                "postgresql+psycopg://u:p@db.prod.internal:5432/frc"
            )
        )
        self.assertFalse(_database_url_targets_localhost(""))


if __name__ == "__main__":
    unittest.main()
