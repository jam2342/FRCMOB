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

    def test_pit_photos_need_a_signed_link_and_other_media_stays_admin_gated(self):
        from app.core.security import sign_media_path
        from app.main import _enforce_media_access

        path = "/media/pit_photos/2026test/frc254/robot.jpg"
        signed = sign_media_path(path, member_id=7)
        # A valid link names the member it was issued to; the middleware checks them.
        self.assertEqual(_enforce_media_access(_make_request("GET", path, signed.split("?", 1)[1])), 7)

        expired = sign_media_path(path, member_id=7, now=0)
        tampered = signed.split("?", 1)[1].replace("sig=", "sig=0")
        other_member = signed.split("?", 1)[1].replace("m=7", "m=8")
        other_photo = sign_media_path("/media/pit_photos/2026test/frc254/other.jpg", member_id=7).split("?", 1)[1]
        for query in ("", expired.split("?", 1)[1], tampered, other_member, other_photo):
            with self.assertRaises(HTTPException) as context:
                _enforce_media_access(_make_request("GET", path, query))
            self.assertEqual(context.exception.status_code, 403)

        with self.assertRaises(HTTPException) as context:
            _enforce_media_access(_make_request("GET", "/media/usage"))
        self.assertEqual(context.exception.status_code, 403)

    def test_a_removed_members_photo_links_stop_working(self):
        from datetime import datetime, timezone
        from unittest import mock

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app import main
        from app.db import models
        from app.db.base import Base
        from tests.workspace_helpers import seed_workspace

        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        with factory() as db:
            _workspace, member, _headers = seed_workspace(db)
            member_id = member.id
        with mock.patch.object(main, "SessionLocal", factory):
            self.assertTrue(main._media_member_active(member_id))
            with factory() as db:
                db.get(models.TeamWorkspaceMember, member_id).removed_at = datetime.now(timezone.utc)
                db.commit()
            self.assertFalse(main._media_member_active(member_id))
            self.assertFalse(main._media_member_active(999))
        engine.dispose()

if __name__ == "__main__":
    unittest.main()
