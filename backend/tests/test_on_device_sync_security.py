from __future__ import annotations

import contextlib
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app.core.config import settings
from app.core.security import (
    ROOM_ACCESS_HEADER,
    issue_admin_session_token,
    issue_room_access_token,
    on_device_sync_identity,
)


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/tracks/on-device-session",
            "headers": [
                (k.lower().encode("ascii"), v.encode("ascii"))
                for k, v in headers.items()
            ],
        }
    )


@contextlib.contextmanager
def _env(*, app_env: str, admin_api_key: str, enforce: bool):
    # These tests must not read the developer's .env. Local checkouts set
    # APP_ENV=production with a real ADMIN_API_KEY, which skips the dev fail-open
    # that CI hits — the exact divergence that let a viewer-token bug ship green.
    with (
        patch.object(settings, "admin_session_token_secret", "x" * 48),
        patch.object(settings, "app_env", app_env),
        patch.object(settings, "admin_api_key", admin_api_key),
        patch.object(settings, "admin_api_header", "X-Admin-Key"),
        patch.object(settings, "enforce_admin_auth_for_writes", enforce),
    ):
        yield


def _dev_env():
    # An unconfigured development box: request_has_admin_access fails open here.
    return _env(app_env="development", admin_api_key="", enforce=True)


def _prod_env():
    return _env(app_env="production", admin_api_key="super-secret-key", enforce=True)


class OnDeviceSyncSecurityTests(unittest.TestCase):
    def test_viewer_room_token_cannot_sync_even_when_admin_fails_open(self):
        with _dev_env():
            token = issue_room_access_token(
                room_key="room-a", scout_profile="alice", role="viewer"
            )["token"]
            authorized, identity = on_device_sync_identity(
                _request({ROOM_ACCESS_HEADER: token})
            )
        self.assertFalse(authorized)
        self.assertEqual(identity, "n")

    def test_viewer_room_token_cannot_sync_when_enforcement_disabled(self):
        with _env(app_env="production", admin_api_key="k", enforce=False):
            token = issue_room_access_token(
                room_key="room-a", scout_profile="alice", role="viewer"
            )["token"]
            authorized, identity = on_device_sync_identity(
                _request({ROOM_ACCESS_HEADER: token})
            )
        self.assertFalse(authorized)
        self.assertEqual(identity, "n")

    def test_editor_room_token_can_sync_with_room_scoped_identity(self):
        with _dev_env():
            token = issue_room_access_token(
                room_key="room-a", scout_profile="alice", role="editor"
            )["token"]
            authorized, identity = on_device_sync_identity(
                _request({ROOM_ACCESS_HEADER: token})
            )
        self.assertTrue(authorized)
        self.assertEqual(identity, "r:room-a:alice")

    def test_two_editors_in_one_room_get_distinct_identities(self):
        with _dev_env():
            identities = []
            for profile in ("alice", "bob"):
                token = issue_room_access_token(
                    room_key="room-a", scout_profile=profile, role="editor"
                )["token"]
                identities.append(
                    on_device_sync_identity(_request({ROOM_ACCESS_HEADER: token}))[1]
                )
        self.assertEqual(identities, ["r:room-a:alice", "r:room-a:bob"])

    def test_admin_session_via_bearer_still_authorises(self):
        # Authorization: Bearer is shared between the admin session token and the
        # room token. Resolving room credentials first must not reject real admins.
        with _prod_env():
            token = issue_admin_session_token()["token"]
            authorized, identity = on_device_sync_identity(
                _request({"Authorization": f"Bearer {token}"})
            )
        self.assertTrue(authorized)
        self.assertEqual(identity, "a")

    def test_editor_room_token_via_bearer_authorises_room_scoped(self):
        with _prod_env():
            token = issue_room_access_token(
                room_key="room-a", scout_profile="alice", role="editor"
            )["token"]
            authorized, identity = on_device_sync_identity(
                _request({"Authorization": f"Bearer {token}"})
            )
        self.assertTrue(authorized)
        self.assertEqual(identity, "r:room-a:alice")

    def test_genuine_admin_key_outranks_insufficient_room_token(self):
        with _prod_env():
            token = issue_room_access_token(
                room_key="room-a", scout_profile="alice", role="viewer"
            )["token"]
            authorized, identity = on_device_sync_identity(
                _request({ROOM_ACCESS_HEADER: token, "X-Admin-Key": "super-secret-key"})
            )
        self.assertTrue(authorized)
        self.assertEqual(identity, "a")

    def test_garbage_room_header_is_rejected_not_upgraded(self):
        with _dev_env():
            authorized, identity = on_device_sync_identity(
                _request({ROOM_ACCESS_HEADER: "not-a-token"})
            )
        self.assertFalse(authorized)
        self.assertEqual(identity, "n")

    def test_unauthenticated_request_is_rejected_in_production(self):
        with _prod_env():
            authorized, identity = on_device_sync_identity(_request({}))
        self.assertFalse(authorized)
        self.assertEqual(identity, "n")

    def test_unauthenticated_request_still_fails_open_in_development(self):
        # Unchanged dev ergonomics: no credential at all on an unconfigured box
        # keeps working. Only a *presented* room credential is held to its role.
        with _dev_env():
            authorized, identity = on_device_sync_identity(_request({}))
        self.assertTrue(authorized)
        self.assertEqual(identity, "a")


if __name__ == "__main__":
    unittest.main()
