import unittest

from fastapi import HTTPException
from starlette.requests import Request

from app.api import routes_storage, routes_synergy
from app.core.config import settings
from app.db import models
from tests.conftest import DBTestCase


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


class RouteWriteGuardTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self._prior_app_env = str(settings.app_env or "")
        self._prior_public_readonly_mode = bool(settings.public_readonly_mode)
        self._prior_enforce_admin = bool(settings.enforce_admin_auth_for_writes)
        self._prior_admin_key = str(settings.admin_api_key or "")
        self._prior_admin_session_token_secret = str(settings.admin_session_token_secret or "")
        self._prior_storage_cleanup_enabled = bool(settings.storage_cleanup_enabled)
        settings.app_env = "development"
        settings.public_readonly_mode = False
        settings.enforce_admin_auth_for_writes = True
        settings.admin_api_key = "test-admin-key"
        settings.admin_session_token_secret = "test-admin-session-secret"
        settings.storage_cleanup_enabled = True

    def tearDown(self):
        settings.app_env = self._prior_app_env
        settings.public_readonly_mode = self._prior_public_readonly_mode
        settings.enforce_admin_auth_for_writes = self._prior_enforce_admin
        settings.admin_api_key = self._prior_admin_key
        settings.admin_session_token_secret = self._prior_admin_session_token_secret
        settings.storage_cleanup_enabled = self._prior_storage_cleanup_enabled
        super().tearDown()

    def test_storage_cleanup_routes_require_admin_access(self):
        cases = [
            (
                routes_storage.cleanup_match,
                ("2026test_qm1", _make_request("POST", "/storage/cleanup/match/2026test_qm1")),
            ),
            (
                routes_storage.cleanup_event,
                ("2026test", _make_request("POST", "/storage/cleanup/event/2026test")),
            ),
            (
                routes_storage.cleanup_old,
                (_make_request("POST", "/storage/cleanup/old"),),
            ),
        ]

        for handler, args in cases:
            with self.subTest(handler=handler.__name__):
                with self.assertRaises(HTTPException) as context:
                    handler(*args)
                self.assertEqual(context.exception.status_code, 403)

    def test_theoretical_alliance_auto_precompute_requires_admin_access(self):
        assert self.db is not None
        self.db.add(models.Event(event_key="2026test", name="Test Event", year=2026))
        for team_number in (1, 2, 3):
            team_key = f"frc{team_number}"
            self.db.add(models.Team(team_key=team_key, team_number=team_number))
            self.db.add(models.EventTeam(event_key="2026test", team_key=team_key))
        self.db.commit()

        payload = routes_synergy.TheoreticalAllianceRequest(team_keys=["frc1", "frc2", "frc3"])

        with self.assertRaises(HTTPException) as context:
            routes_synergy.score_theoretical_alliance(
                "2026test",
                payload,
                _make_request("POST", "/synergy/event/2026test/theoretical-alliance"),
                self.db,
            )
        self.assertEqual(context.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
