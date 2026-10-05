import unittest

from fastapi import HTTPException
from starlette.requests import Request

from app.core.config import settings
from app.core.security import enforce_write_request_access


def _make_request(method: str, path: str, query: str = "") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": query.encode(),
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

    def test_alliance_scoring_is_open_to_non_admins(self):
        # A read-only computation sent as POST: Alliance Advisor and Compare use it.
        enforce_write_request_access(_make_request("POST", "/synergy/event/2026arc/theoretical-alliance"))
        enforce_write_request_access(_make_request("POST", "/api/synergy/event/2026arc/theoretical-alliance"))

    def test_other_synergy_writes_stay_admin_gated(self):
        for path in ("/synergy/event/2026arc/precompute", "/synergy/event/2026arc/theoretical-alliance/x"):
            with self.assertRaises(HTTPException) as context:
                enforce_write_request_access(_make_request("POST", path))
            self.assertEqual(context.exception.status_code, 403)

    def test_public_readonly_mode_blocks_alliance_scoring_too(self):
        settings.public_readonly_mode = True
        with self.assertRaises(HTTPException):
            enforce_write_request_access(_make_request("POST", "/synergy/event/2026arc/theoretical-alliance"))

    def test_pit_photos_need_a_signed_link_and_other_media_stays_admin_gated(self):
        from app.core.security import sign_media_path
        from app.main import _enforce_media_access

        path = "/media/pit_photos/2026test/frc254/robot.jpg"
        signed = sign_media_path(path)
        _enforce_media_access(_make_request("GET", path, signed.split("?", 1)[1]))

        expired = sign_media_path(path, now=0)
        tampered = signed.split("?", 1)[1].replace("sig=", "sig=0")
        other_photo = sign_media_path("/media/pit_photos/2026test/frc254/other.jpg").split("?", 1)[1]
        for query in ("", expired.split("?", 1)[1], tampered, other_photo):
            with self.assertRaises(HTTPException) as context:
                _enforce_media_access(_make_request("GET", path, query))
            self.assertEqual(context.exception.status_code, 403)

        with self.assertRaises(HTTPException) as context:
            _enforce_media_access(_make_request("GET", "/media/usage"))
        self.assertEqual(context.exception.status_code, 403)

if __name__ == "__main__":
    unittest.main()
