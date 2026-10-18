from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi import HTTPException

from app.api import routes_pit_scouting
from app.api.routes_workspaces import router as workspace_router
from app.core.config import settings
from app.services import signup_challenge, workspace_limits
from tests.test_workspace_abuse_limits import _DbCase
from tests.workspace_helpers import seed_workspace
from tests.image_helpers import jpeg_bytes


class PhotoStorageSecurityTests(_DbCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(mock.patch.object(routes_pit_scouting, "PIT_PHOTOS_ROOT", self.root))
        self.enterContext(mock.patch.multiple(settings, pit_photo_minimum_free_bytes=1,
                                              pit_photo_quota_bytes_per_workspace=10000,
                                              pit_photo_quota_bytes_total=10000))
        with self.SessionLocal() as db:
            _, _, self.headers = seed_workspace(db)
            _, _, self.other_headers = seed_workspace(db, name="Other team")
        self.client = self._client(routes_pit_scouting.router)
        self.image = jpeg_bytes()

    def upload(self, event="2026one", headers=None):
        return self.client.post("/pit-scouting/photo", headers=headers or self.headers, json={
            "event_key": event, "team_key": "frc1", "image_base64": base64.b64encode(self.image).decode(),
        })

    def test_changing_event_does_not_reset_workspace_quota(self):
        with mock.patch.object(settings, "pit_photo_quota_bytes_per_workspace", len(self.image)):
            self.assertEqual(self.upload().status_code, 200)
            self.assertEqual(self.upload("2026invented").status_code, 413)
        self.assertEqual(sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file()), len(self.image))

    def test_other_workspaces_do_not_reset_site_quota(self):
        with mock.patch.object(settings, "pit_photo_quota_bytes_total", len(self.image)):
            self.assertEqual(self.upload().status_code, 200)
            self.assertEqual(self.upload("2026other", self.other_headers).status_code, 413)

    def test_valid_tiny_photos_are_count_limited_across_events(self):
        with mock.patch.object(settings, "pit_photo_max_files_per_workspace", 2):
            self.assertEqual(self.upload().status_code, 200)
            self.assertEqual(self.upload("2026two").status_code, 200)
            self.assertEqual(self.upload("2026three").status_code, 413)

    def test_global_photo_count_is_shared_by_workspaces(self):
        with mock.patch.object(settings, "pit_photo_max_files_total", 1):
            self.assertEqual(self.upload().status_code, 200)
            self.assertEqual(self.upload("2026other", self.other_headers).status_code, 413)

    def test_forged_magic_header_and_truncated_payload_are_rejected(self):
        for data in (b"\xff\xd8\xff", self.image[:-10]):
            self.image = data
            self.assertEqual(self.upload().status_code, 400)
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])

    def test_large_decoded_pixel_count_is_rejected_before_storage(self):
        with mock.patch.object(routes_pit_scouting, "MAX_PHOTO_PIXELS", 1):
            self.assertEqual(self.upload().status_code, 413)
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])

    def test_photo_only_upload_delete_churn_keeps_no_empty_directories_or_rows(self):
        from app.db import models

        for event in ("2026one", "2026two", "2026three"):
            uploaded = self.upload(event)
            self.assertEqual(uploaded.status_code, 200)
            deleted = self.client.post("/pit-scouting/photo/delete", headers=self.headers, json={
                "event_key": event, "team_key": "frc1", "photo_path": uploaded.json()["photo"],
            })
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(deleted.json()["entry"]["photos"], [])
        self.assertEqual(list(self.root.rglob("*")), [])
        with self.SessionLocal() as db:
            self.assertEqual(db.query(models.PitScoutingEntry).count(), 0)

    def test_disk_reserve_refuses_photos_but_allows_notes(self):
        with mock.patch.object(workspace_limits.shutil, "disk_usage", return_value=mock.Mock(free=1)):
            self.assertEqual(self.upload().status_code, 503)
        saved = self.client.post("/pit-scouting", headers=self.headers, json={
            "event_key": "2026one", "team_key": "frc1", "payload": {"notes": "Still saved"},
        })
        self.assertEqual(saved.status_code, 200)

    def test_failed_database_commit_removes_uploaded_file(self):
        with mock.patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("disposable commit failure")):
            with self.assertRaisesRegex(RuntimeError, "disposable commit failure"):
                self.upload()
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])


class SignupChallengeTests(_DbCase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.multiple(settings, workspace_signup_challenge_required=True,
                                              turnstile_site_key="test-public-site", turnstile_secret_key="test-secret-only",
                                              turnstile_allowed_hostnames="app.example"))
        self.client = self._client(workspace_router)
        self.body = {"name": "Team", "display_name": "Lead", "challenge_token": "test-token"}
        self.post = self.enterContext(mock.patch.object(signup_challenge.requests, "post"))
        self.post.return_value.status_code = 200
        self.post.return_value.json.return_value = {"success": True, "action": "workspace_create", "hostname": "app.example"}

    def test_policy_is_public_and_never_returns_secret(self):
        response = self.client.get("/workspaces/signup-policy")
        self.assertEqual(response.json(), {"required": True, "site_key": "test-public-site"})
        self.assertNotIn("test-secret", response.text)

    def test_missing_failed_and_replayed_challenges_do_not_consume_signup_slots(self):
        missing = self.client.post("/workspaces", json={**self.body, "challenge_token": None})
        self.assertEqual(missing.status_code, 403)
        self.post.assert_not_called()
        for result in ({"success": False}, {"success": True, "hostname": "attacker.example", "action": "workspace_create"},
                       {"success": True, "hostname": "app.example", "action": "wrong_action"}):
            self.post.return_value.json.return_value = result
            self.assertEqual(self.client.post("/workspaces", json=self.body).status_code, 403)
        self.assertEqual(self.redis.values, {})

    def test_success_requires_server_validation_before_slot_consumption(self):
        self.assertEqual(self.client.post("/workspaces", json=self.body).status_code, 200)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.args[0], signup_challenge.SITEVERIFY_URL)
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])
        self.assertEqual(sum(int(value) for key, value in self.redis.values.items() if key.startswith("workspaces:created:")), 1)
        self.post.return_value.json.return_value = {"success": False, "error-codes": ["timeout-or-duplicate"]}
        self.assertEqual(self.client.post("/workspaces", json=self.body).status_code, 403)

    def test_missing_configuration_and_verification_outage_fail_closed(self):
        with mock.patch.object(settings, "turnstile_secret_key", ""):
            self.assertEqual(self.client.post("/workspaces", json=self.body).status_code, 503)
        self.post.side_effect = signup_challenge.requests.Timeout("no response")
        response = self.client.post("/workspaces", json=self.body)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("test-secret", response.text)
        self.assertEqual(self.redis.values, {})

    def test_production_cannot_disable_challenge_with_setting(self):
        with mock.patch.object(settings, "workspace_signup_challenge_required", False), \
                mock.patch.object(type(settings), "is_production_like", new_callable=mock.PropertyMock, return_value=True):
            self.assertTrue(signup_challenge.challenge_required())
            with mock.patch.object(workspace_limits, "get_cache", return_value=mock.Mock(redis_client=None)):
                with self.assertRaises(HTTPException) as error:
                    workspace_limits.take_workspace_creation_slot()
                self.assertEqual(error.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
