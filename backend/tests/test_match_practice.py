from __future__ import annotations

from unittest import mock

import pytest
from fastapi import HTTPException

from app.api import routes_matches
from app.db import models
from tests.conftest import DBTestCase

KEY = "2026mndu_qm7"


class MatchPracticeTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self.db.add(models.Event(event_key="2026mndu", name="Duluth", year=2026))
        self.db.add(models.Match(match_key=KEY, event_key="2026mndu", comp_level="qm", set_number=1, match_number=7))
        for station, n in enumerate((11, 22, 33), start=1):
            self.db.add(models.Team(team_key=f"frc{n}", team_number=n))
            self.db.add(models.MatchTeam(match_key=KEY, team_key=f"frc{n}", event_key="2026mndu", alliance="blue", station=f"b{station}"))
        self.db.add(models.MatchAllianceResult(
            match_key=KEY, alliance="blue", event_key="2026mndu", season_year=2026, team_keys=["frc11", "frc22", "frc33"],
            total_score=140, result="win",
            components={"hub_fuel_count": 58, "robot_auto_tower_points": [15, 0, 0], "robot_endgame_tower_points": [10, 30, 0]},
        ))
        self.db.commit()

    def practice(self, team: str, video=None):
        with mock.patch.object(routes_matches, "_match_video", return_value=video):
            return routes_matches.get_match_practice(KEY, team=team, db=self.db)

    def test_one_robots_official_climbs_and_its_alliances_fuel(self):
        body = self.practice("22", video={"embed_url": "https://www.youtube.com/embed/abcdefghijk", "watch_url": "w"})
        self.assertEqual((body["team_key"], body["alliance"], body["label"]), ("frc22", "blue", "QM 7"))
        self.assertTrue(body["played"])
        self.assertTrue(body["checkable"])
        self.assertEqual(body["official"], {"auto_tower_points": 0.0, "endgame_tower_points": 30.0, "alliance_fuel": 58, "alliance_score": 140})
        self.assertEqual(body["video"]["embed_url"], "https://www.youtube.com/embed/abcdefghijk")
        self.assertEqual(self.practice("frc11")["official"]["auto_tower_points"], 15.0)

    def test_a_team_not_in_the_match(self):
        with pytest.raises(HTTPException) as caught:
            self.practice("frc99")
        self.assertEqual(caught.value.status_code, 404)

    def test_video_comes_from_tba_only_as_a_youtube_id(self):
        client = mock.Mock()
        client.match.return_value = {"videos": [{"type": "tba", "key": "x"}, {"type": "youtube", "key": "dQw4w9WgXcQ"}]}
        with mock.patch.object(routes_matches.settings, "tba_auth_key", "k"), mock.patch.object(routes_matches, "TBAClient", return_value=client):
            self.assertEqual(routes_matches._match_video(KEY)["watch_url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            client.match.return_value = {"videos": [{"type": "youtube", "key": "not a video id"}]}
            self.assertIsNone(routes_matches._match_video(KEY))
            client.match.side_effect = RuntimeError("down")
            self.assertIsNone(routes_matches._match_video(KEY))
        with mock.patch.object(routes_matches.settings, "tba_auth_key", ""):
            self.assertIsNone(routes_matches._match_video(KEY))
