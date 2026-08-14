from __future__ import annotations

import hashlib
import http.server
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.services.vision import model_provisioning


class _StaticBytesHandler(http.server.BaseHTTPRequestHandler):
    payload = b""

    def do_GET(self):  # noqa: N802 - stdlib handler method name
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):  # noqa: D401 - silence test server logging
        pass


class _TestHTTPServer:
    def __init__(self, payload: bytes):
        handler = type("Handler", (_StaticBytesHandler,), {"payload": payload})
        self.server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/detector.pt"

    def __exit__(self, *exc):
        self.server.shutdown()
        self.thread.join(timeout=2)


class PrimaryModelProvisioningTests(unittest.TestCase):
    def test_existing_primary_model_requires_matching_configured_checksum(self):
        payload = b"m" * 100_001
        expected_hash = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "detector.pt"
            target.write_bytes(payload)
            with (
                patch.object(settings, "video_tracking_yolo_model_sha256", expected_hash),
                patch.object(model_provisioning, "primary_model_path", return_value=target),
            ):
                status = model_provisioning.ensure_primary_model_available()

        self.assertTrue(status["present"])
        self.assertTrue(status["checksum_verified"])
        self.assertEqual(status["sha256"], expected_hash)
        self.assertEqual(status["source"], "local")

    def test_existing_primary_model_rejects_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "detector.pt"
            target.write_bytes(b"m" * 100_001)
            with (
                patch.object(settings, "video_tracking_yolo_model_sha256", "a" * 64),
                patch.object(settings, "video_tracking_yolo_model_url", ""),
                patch.object(model_provisioning, "primary_model_path", return_value=target),
            ):
                status = model_provisioning.ensure_primary_model_available()

        self.assertFalse(status["present"])
        self.assertFalse(status["checksum_verified"])
        self.assertEqual(status["error"], "existing_model_checksum_mismatch")

    def test_production_rejects_non_https_detector_url(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "missing.pt"
            with (
                patch.object(settings, "app_env", "production"),
                patch.object(settings, "video_tracking_yolo_model_sha256", ""),
                patch.object(settings, "video_tracking_yolo_model_url", "http://example.test/detector.pt"),
                patch.object(model_provisioning, "primary_model_path", return_value=target),
            ):
                status = model_provisioning.ensure_primary_model_available()

        self.assertFalse(status["present"])
        self.assertEqual(status["error"], "production_model_url_must_use_https")

    def test_download_verifies_checksum_and_installs_atomically(self):
        payload = b"m" * 200_000
        expected_hash = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory, _TestHTTPServer(payload) as url:
            target = Path(directory) / "detector.pt"
            with (
                patch.object(settings, "video_tracking_yolo_model_sha256", expected_hash),
                patch.object(settings, "video_tracking_yolo_model_url", url),
                patch.object(settings, "app_env", "development"),
                patch.object(model_provisioning, "primary_model_path", return_value=target),
            ):
                status = model_provisioning.ensure_primary_model_available()

            self.assertTrue(status["present"])
            self.assertTrue(status["downloaded"])
            self.assertTrue(status["checksum_verified"])
            self.assertEqual(status["sha256"], expected_hash)
            self.assertTrue(target.is_file())
            # No leftover partial-download temp files in the target directory.
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_download_rejects_checksum_mismatch_and_leaves_no_partial_file(self):
        payload = b"m" * 200_000
        with tempfile.TemporaryDirectory() as directory, _TestHTTPServer(payload) as url:
            target = Path(directory) / "detector.pt"
            with (
                patch.object(settings, "video_tracking_yolo_model_sha256", "a" * 64),
                patch.object(settings, "video_tracking_yolo_model_url", url),
                patch.object(settings, "app_env", "development"),
                patch.object(model_provisioning, "primary_model_path", return_value=target),
            ):
                status = model_provisioning.ensure_primary_model_available()

            self.assertFalse(status["present"])
            self.assertEqual(status["error"], "download_failed")
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_download_rejects_payload_exceeding_configured_max_bytes(self):
        payload = b"m" * 200_000
        with tempfile.TemporaryDirectory() as directory, _TestHTTPServer(payload) as url:
            target = Path(directory) / "detector.pt"
            with (
                patch.object(settings, "video_tracking_yolo_model_sha256", ""),
                patch.object(settings, "video_tracking_yolo_model_url", url),
                patch.object(settings, "video_tracking_yolo_model_max_bytes", 100_000),
                patch.object(settings, "app_env", "development"),
                patch.object(model_provisioning, "primary_model_path", return_value=target),
            ):
                status = model_provisioning.ensure_primary_model_available()

            self.assertFalse(status["present"])
            self.assertEqual(status["error"], "download_failed")
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
