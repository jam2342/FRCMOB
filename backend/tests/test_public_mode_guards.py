import unittest

from fastapi import HTTPException

from app.api.routes_automation import _ensure_automation_write_enabled, automate_regional_post_event_breakdowns_tick
from app.core.config import settings


class PublicModeGuardTests(unittest.TestCase):
    def setUp(self):
        self._prior_public_readonly_mode = bool(settings.public_readonly_mode)

    def tearDown(self):
        settings.public_readonly_mode = self._prior_public_readonly_mode

    def test_guard_blocks_when_public_mode_enabled(self):
        settings.public_readonly_mode = True
        with self.assertRaises(HTTPException) as context:
            _ensure_automation_write_enabled()
        self.assertEqual(context.exception.status_code, 403)
        self.assertIn("disabled in public mode", str(context.exception.detail).lower())

    def test_guard_allows_when_public_mode_disabled(self):
        settings.public_readonly_mode = False
        _ensure_automation_write_enabled()

    def test_tick_route_blocks_before_db_access(self):
        settings.public_readonly_mode = True
        with self.assertRaises(HTTPException) as context:
            automate_regional_post_event_breakdowns_tick(2026, db=None)  # type: ignore[arg-type]
        self.assertEqual(context.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
