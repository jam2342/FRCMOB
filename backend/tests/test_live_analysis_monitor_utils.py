import unittest
from datetime import datetime, timezone

try:
    from app.services.analysis.live_monitor import (
        _event_is_active_today,
        _event_is_in_region_scope,
        _normalize_countries,
        _normalize_stream_url,
        _stream_url_from_webcasts,
        live_monitor_status,
    )

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - optional deps guarded import
    _IMPORT_ERROR = exc


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional service deps unavailable: {_IMPORT_ERROR}")
class LiveAnalysisMonitorUtilsTests(unittest.TestCase):
    def test_normalize_stream_url_youtube(self):
        self.assertEqual(
            _normalize_stream_url("youtube", "UqumHWHa9Qs"),
            "https://www.youtube.com/watch?v=UqumHWHa9Qs",
        )
        self.assertEqual(
            _normalize_stream_url("youtube", "https://www.youtube.com/watch?v=UqumHWHa9Qs"),
            "https://www.youtube.com/watch?v=UqumHWHa9Qs",
        )

    def test_normalize_stream_url_twitch(self):
        self.assertEqual(
            _normalize_stream_url("twitch", "firstinspires"),
            "https://www.twitch.tv/firstinspires",
        )

    def test_stream_url_prefers_youtube(self):
        webcasts = [
            {"type": "twitch", "channel": "firstinspires"},
            {"type": "youtube", "channel": "UqumHWHa9Qs"},
        ]
        self.assertEqual(
            _stream_url_from_webcasts(webcasts),
            "https://www.youtube.com/watch?v=UqumHWHa9Qs",
        )

    def test_region_scope_matching(self):
        allowed = _normalize_countries("USA,Canada")
        self.assertTrue(
            _event_is_in_region_scope(
                {"country": "USA", "state_prov": "TX", "location_name": "Houston, TX, USA"},
                allowed,
            )
        )
        self.assertTrue(
            _event_is_in_region_scope(
                {"country": "Canada", "state_prov": "ON", "location_name": "Toronto, ON, Canada"},
                allowed,
            )
        )
        self.assertFalse(
            _event_is_in_region_scope(
                {"country": "Mexico", "state_prov": "MX", "location_name": "Monterrey, MX"},
                allowed,
            )
        )

    def test_event_active_today_window(self):
        now = datetime(2026, 3, 19, 15, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(
            _event_is_active_today(
                {"start_date": "2026-03-18", "end_date": "2026-03-20"},
                now,
            )
        )
        self.assertFalse(
            _event_is_active_today(
                {"start_date": "2026-03-01", "end_date": "2026-03-02"},
                now,
            )
        )

    def test_status_shape(self):
        payload = live_monitor_status()
        self.assertTrue(payload.get("ok"))
        self.assertIn("running_count", payload)
        self.assertIn("sessions", payload)


if __name__ == "__main__":
    unittest.main()
