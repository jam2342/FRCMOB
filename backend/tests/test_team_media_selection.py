from __future__ import annotations

import unittest

import app.api.routes_teams  # noqa: F401  (loads first, as the app does; media imports it)
from app.api.teams import media


class TeamMediaSelectionTests(unittest.TestCase):
    rows = [
        {"type": "imgur", "direct_url": "https://i.imgur.com/old.png", "view_url": "https://imgur.com/old"},
        {"type": "imgur", "direct_url": "https://i.imgur.com/best.png", "view_url": "https://imgur.com/best", "preferred": True},
        {"type": "avatar", "details": {"base64Image": "QUJD"}},
    ]

    def test_robot_image_prefers_the_preferred_photo(self):
        url, media_type, view_url = media._select_robot_image(self.rows)
        self.assertEqual(url, "https://i.imgur.com/best.png")
        self.assertEqual((media_type, view_url), ("imgur", "https://imgur.com/best"))

    def test_logo_uses_the_avatar_before_any_photo(self):
        url, media_type, _view = media._select_team_logo(self.rows)
        self.assertEqual(url, "data:image/png;base64,QUJD")
        self.assertEqual(media_type, "avatar")

    def test_logo_falls_back_to_a_photo_without_an_avatar(self):
        url, _type, _view = media._select_team_logo(self.rows[:2])
        self.assertEqual(url, "https://i.imgur.com/best.png")

    def test_nothing_usable_is_none(self):
        self.assertIsNone(media._select_robot_image([{"type": "avatar"}, "junk"]))  # type: ignore[list-item]


if __name__ == "__main__":
    unittest.main()
