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
        "video_tracking_require_primary_model_in_production": True,
        "video_tracking_require_primary_model_sha256_in_production": True,
        "ml_shadow_auto_train_activate": False,
    }
    base.update(overrides)
    return base


class StartupEnvValidationMlGatingTests(unittest.TestCase):
    def test_missing_primary_model_fails_production_validation(self):
        with patch.multiple(settings, **_prod_patches()):
            report = _startup_env_validation_report(model_status={"present": False})
        self.assertFalse(report["ok"])
        self.assertTrue(any("Primary FRC detector is unavailable" in e for e in report["errors"]))

    def test_unverified_checksum_fails_production_validation(self):
        with patch.multiple(settings, **_prod_patches()):
            report = _startup_env_validation_report(
                model_status={"present": True, "checksum_verified": False}
            )
        self.assertFalse(report["ok"])
        self.assertTrue(any("checksum is not verified" in e for e in report["errors"]))

    def test_present_and_verified_model_passes_production_validation(self):
        with patch.multiple(settings, **_prod_patches()):
            report = _startup_env_validation_report(
                model_status={"present": True, "checksum_verified": True}
            )
        self.assertTrue(report["ok"])

    def test_auto_train_activate_true_fails_production_validation(self):
        with patch.multiple(settings, **_prod_patches(ml_shadow_auto_train_activate=True)):
            report = _startup_env_validation_report(
                model_status={"present": True, "checksum_verified": True}
            )
        self.assertFalse(report["ok"])
        self.assertTrue(any("ML_SHADOW_AUTO_TRAIN_ACTIVATE" in e for e in report["errors"]))

    def test_checksum_requirement_can_be_relaxed_via_setting(self):
        with patch.multiple(
            settings,
            **_prod_patches(video_tracking_require_primary_model_sha256_in_production=False),
        ):
            report = _startup_env_validation_report(
                model_status={"present": True, "checksum_verified": False}
            )
        self.assertTrue(report["ok"])


if __name__ == "__main__":
    unittest.main()
