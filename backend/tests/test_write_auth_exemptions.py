import unittest

from fastapi import HTTPException
from starlette.requests import Request

from app.core.config import settings
from app.core.security import enforce_write_request_access


def _make_request(method: str, path: str) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 12345),
    }
    return Request(scope)


class WriteAuthExemptionsTests(unittest.TestCase):
    def setUp(self):
        self._prior_app_env = str(settings.app_env or "")
        self._prior_public_readonly_mode = bool(settings.public_readonly_mode)
        self._prior_enforce_admin = bool(settings.enforce_admin_auth_for_writes)
        self._prior_admin_key = str(settings.admin_api_key or "")
        self._prior_admin_session_token_secret = str(settings.admin_session_token_secret or "")
        settings.app_env = "development"
        settings.public_readonly_mode = False
        settings.enforce_admin_auth_for_writes = True
        settings.admin_api_key = "test-admin-key"
        settings.admin_session_token_secret = "test-admin-session-secret"

    def tearDown(self):
        settings.app_env = self._prior_app_env
        settings.public_readonly_mode = self._prior_public_readonly_mode
        settings.enforce_admin_auth_for_writes = self._prior_enforce_admin
        settings.admin_api_key = self._prior_admin_key
        settings.admin_session_token_secret = self._prior_admin_session_token_secret

    def test_scouting_rooms_writes_are_allowed_without_admin_header(self):
        request = _make_request("POST", "/scouting/rooms")
        enforce_write_request_access(request)

    def test_non_exempt_writes_require_admin_header(self):
        request = _make_request("POST", "/analysis/recompute")
        with self.assertRaises(HTTPException) as context:
            enforce_write_request_access(request)
        self.assertEqual(context.exception.status_code, 403)

    def test_non_exempt_writes_fail_closed_in_production_when_admin_key_missing(self):
        settings.app_env = "production"
        settings.admin_api_key = ""
        request = _make_request("POST", "/analysis/recompute")
        with self.assertRaises(HTTPException) as context:
            enforce_write_request_access(request)
        self.assertEqual(context.exception.status_code, 403)

    def test_public_readonly_mode_still_blocks_scouting_rooms_writes(self):
        settings.public_readonly_mode = True
        request = _make_request("POST", "/scouting/rooms")
        with self.assertRaises(HTTPException) as context:
            enforce_write_request_access(request)
        self.assertEqual(context.exception.status_code, 403)

    def test_pit_photo_media_is_public_but_other_media_stays_admin_gated(self):
        from app.main import _enforce_media_access

        _enforce_media_access(_make_request("GET", "/media/pit_photos/2026test/frc254/robot.jpg"))

        with self.assertRaises(HTTPException) as context:
            _enforce_media_access(_make_request("GET", "/media/usage"))
        self.assertEqual(context.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
