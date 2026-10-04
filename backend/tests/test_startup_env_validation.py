from __future__ import annotations

import unittest
from unittest.mock import patch

from app.core.config import settings
from app.main import _startup_env_validation_report


def _prod_patches(**overrides):
    base = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://user:pass@example.test/db",
        "redis_url": "rediss://redis.example.test/0",
        "admin_api_key": "test-admin-key-at-least-32-bytes-long",
        "admin_session_token_secret": "test-session-secret-at-least-32-bytes",
        "enforce_admin_auth_for_writes": True,
        "strict_startup_env_validation": False,
        "cors_allow_origins": "https://example.test",
        "statbotics_base_url": "https://statbotics.example.test",
        "ml_shadow_auto_train_activate": False,
        "on_device_sync_require_signed_token": True,
    }
    base.update(overrides)
    return base


class StartupEnvValidationMlGatingTests(unittest.TestCase):
    def test_complete_production_config_passes(self):
        with patch.multiple(settings, **_prod_patches()):
            report = _startup_env_validation_report()
        self.assertTrue(report["ok"], report["errors"])

    def test_auto_train_activate_true_fails_production_validation(self):
        with patch.multiple(settings, **_prod_patches(ml_shadow_auto_train_activate=True)):
            report = _startup_env_validation_report()
        self.assertFalse(report["ok"])
        self.assertTrue(any("ML_SHADOW_AUTO_TRAIN_ACTIVATE" in e for e in report["errors"]))

    def test_unsigned_on_device_sync_fails_production_validation(self):
        with patch.multiple(
            settings,
            **_prod_patches(on_device_sync_require_signed_token=False),
        ):
            report = _startup_env_validation_report()
        self.assertFalse(report["ok"])
        self.assertTrue(
            any("ON_DEVICE_SYNC_REQUIRE_SIGNED_TOKEN" in e for e in report["errors"])
        )


if __name__ == "__main__":
    unittest.main()
