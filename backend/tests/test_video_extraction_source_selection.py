from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from app.services.vision import video_extraction
    from app.services.vision.video_extraction import PreparedYoutubeSource, VideoExtractionError

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - optional deps guarded import
    _IMPORT_ERROR = exc


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional service deps unavailable: {_IMPORT_ERROR}")
class VideoExtractionSourceSelectionTests(unittest.TestCase):
    def test_prepare_source_prefers_stream_when_available(self):
        with (
            patch.object(video_extraction, "resolve_stream_url", return_value="https://stream.example/video"),
            patch.object(video_extraction, "probe_video_metadata", return_value={"duration_sec": 152.3, "width": 1280, "height": 720}),
            patch.object(video_extraction, "download_youtube_video") as mock_download,
        ):
            prepared = video_extraction.prepare_youtube_video_source(
                match_key="2026tx_qm1",
                youtube_url="https://www.youtube.com/watch?v=abc123",
                force_download_refresh=False,
                prefer_streaming=True,
                allow_download_fallback=True,
            )

        self.assertEqual(prepared.source_mode, "youtube_stream")
        self.assertEqual(prepared.video_source, "https://stream.example/video")
        self.assertIsNone(prepared.local_video_path)
        self.assertIsNone(prepared.stream_error)
        mock_download.assert_not_called()

    def test_prepare_source_falls_back_to_download_when_stream_fails(self):
        local_path = Path("/tmp/2026tx_qm1.mp4")
        with (
            patch.object(video_extraction, "resolve_stream_url", side_effect=VideoExtractionError("stream resolve failed")),
            patch.object(video_extraction, "download_youtube_video", return_value=local_path) as mock_download,
            patch.object(video_extraction, "probe_video_metadata", return_value={"duration_sec": 140.0, "width": 1920, "height": 1080}),
        ):
            prepared = video_extraction.prepare_youtube_video_source(
                match_key="2026tx_qm1",
                youtube_url="https://www.youtube.com/watch?v=abc123",
                force_download_refresh=False,
                prefer_streaming=True,
                allow_download_fallback=True,
            )

        self.assertEqual(prepared.source_mode, "downloaded_video")
        self.assertEqual(prepared.video_source, local_path)
        self.assertEqual(prepared.local_video_path, local_path)
        self.assertIn("stream resolve failed", str(prepared.stream_error))
        mock_download.assert_called_once()

    def test_prepare_source_raises_when_stream_fails_and_fallback_disabled(self):
        with patch.object(video_extraction, "resolve_stream_url", side_effect=VideoExtractionError("stream resolve failed")):
            with self.assertRaises(VideoExtractionError):
                video_extraction.prepare_youtube_video_source(
                    match_key="2026tx_qm1",
                    youtube_url="https://www.youtube.com/watch?v=abc123",
                    force_download_refresh=False,
                    prefer_streaming=True,
                    allow_download_fallback=False,
                )

    def test_cleanup_prepared_source_deletes_fallback_file_when_enabled(self):
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        prepared = PreparedYoutubeSource(
            video_source=temp_path,
            video_metadata={},
            source_mode="downloaded_video",
            local_video_path=temp_path,
        )
        self.assertTrue(temp_path.exists())
        with patch.object(video_extraction.settings, "video_extraction_cleanup_fallback_download", True, create=True):
            video_extraction.cleanup_prepared_youtube_source(prepared)
        self.assertFalse(temp_path.exists())

    def test_cleanup_prepared_source_keeps_file_when_disabled(self):
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        prepared = PreparedYoutubeSource(
            video_source=temp_path,
            video_metadata={},
            source_mode="downloaded_video",
            local_video_path=temp_path,
        )
        self.assertTrue(temp_path.exists())
        with patch.object(video_extraction.settings, "video_extraction_cleanup_fallback_download", False, create=True):
            video_extraction.cleanup_prepared_youtube_source(prepared)
        self.assertTrue(temp_path.exists())
        temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
