import unittest

try:
    from app.api.routes_matches import (
        _match_score,
        _parse_webcast_date,
        _select_preferred_webcast_stream,
        _serialize_webcast_stream,
        _youtube_video_id,
    )

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - dependency-gated import
    _IMPORT_ERROR = exc


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional api deps unavailable: {_IMPORT_ERROR}")
class LiveStreamHelpersTests(unittest.TestCase):
    def test_youtube_video_id_parser_supports_ids_and_urls(self):
        self.assertEqual(_youtube_video_id("UqumHWHa9Qs"), "UqumHWHa9Qs")
        self.assertEqual(_youtube_video_id("https://youtu.be/UqumHWHa9Qs"), "UqumHWHa9Qs")
        self.assertEqual(
            _youtube_video_id("https://www.youtube.com/watch?v=UqumHWHa9Qs"),
            "UqumHWHa9Qs",
        )
        self.assertIsNone(_youtube_video_id("not-a-valid-youtube-id"))

    def test_serialize_webcast_stream_supports_youtube_embed(self):
        stream = _serialize_webcast_stream({"type": "youtube", "channel": "UqumHWHa9Qs"})
        self.assertIsNotNone(stream)
        assert stream is not None
        self.assertTrue(stream["supports_embed"])
        self.assertEqual(stream["watch_url"], "https://www.youtube.com/watch?v=UqumHWHa9Qs")
        self.assertEqual(stream["embed_url"], "https://www.youtube.com/embed/UqumHWHa9Qs")

    def test_serialize_webcast_stream_twitch_watch_only(self):
        stream = _serialize_webcast_stream({"type": "twitch", "channel": "firstinspires"})
        self.assertIsNotNone(stream)
        assert stream is not None
        self.assertFalse(stream["supports_embed"])
        self.assertEqual(stream["watch_url"], "https://www.twitch.tv/firstinspires")
        self.assertIsNone(stream["embed_url"])

    def test_match_score_treats_negative_scores_as_unresolved(self):
        payload = {
            "alliances": {
                "red": {"score": -1},
                "blue": {"score": 0},
            }
        }
        self.assertIsNone(_match_score(payload, "red"))
        self.assertEqual(_match_score(payload, "blue"), 0)

    def test_select_preferred_webcast_stream_prefers_latest_dated_embed(self):
        older = _serialize_webcast_stream({"type": "youtube", "channel": "Zmw9hk2Mucw"})
        newer = _serialize_webcast_stream({"type": "youtube", "channel": "srMwHku-l2M"})
        self.assertIsNotNone(older)
        self.assertIsNotNone(newer)
        assert older is not None
        assert newer is not None
        preferred = _select_preferred_webcast_stream(
            [
                (older, _parse_webcast_date("2026-03-06")),
                (newer, _parse_webcast_date("2026-03-08")),
            ]
        )
        self.assertEqual(preferred, newer)


if __name__ == "__main__":
    unittest.main()
