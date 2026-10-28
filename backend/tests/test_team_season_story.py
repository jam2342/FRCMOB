from __future__ import annotations

from app.api.teams.season_story import get_team_season_story
from app.db import models
from tests.conftest import DBTestCase


class SeasonStoryTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self.db.add(models.Event(event_key="2026mndu", name="Duluth", year=2026))
        self.db.add(models.Event(event_key="2026mnmi", name="Minneapolis", year=2026))
        self.db.add(models.Event(event_key="2025mnmi", name="Last year", year=2025))
        for n in range(1, 8):
            self.db.add(models.Team(team_key=f"frc{n}", team_number=n, nickname="One" if n == 1 else None))
            for event_key in ("2026mndu", "2026mnmi", "2025mnmi"):
                self.db.add(models.EventTeam(event_key=event_key, team_key=f"frc{n}"))
        # (match, event, level, time, red, blue, red score, blue score)
        games = [
            ("2026mndu_qm1", "2026mndu", "qm", 100, (1, 2, 3), (4, 5, 6), 80, 40),
            ("2026mndu_qm2", "2026mndu", "qm", 200, (4, 5, 7), (1, 6, 2), 30, 90),
            ("2026mndu_f1m1", "2026mndu", "f", 300, (1, 2, 3), (4, 5, 6), 120, 100),
            ("2026mndu_f1m2", "2026mndu", "f", 400, (1, 2, 3), (4, 5, 6), 110, 90),
            ("2026mnmi_qm1", "2026mnmi", "qm", 1000, (1, 2, 3), (4, 5, 6), 50, 50),
            ("2026mnmi_qm2", "2026mnmi", "qm", 1100, (1, 4, 7), (2, 3, 5), None, None),  # not played yet
            ("2025mnmi_qm1", "2025mnmi", "qm", 10, (1, 2, 3), (4, 5, 6), 1, 0),  # another season
        ]
        for key, event_key, level, time, red, blue, red_score, blue_score in games:
            number = int(key[-1])
            self.db.add(models.Match(match_key=key, event_key=event_key, comp_level=level, set_number=1, match_number=number, time=time))
            for alliance, side in (("red", red), ("blue", blue)):
                for station, n in enumerate(side, start=1):
                    self.db.add(models.MatchTeam(match_key=key, team_key=f"frc{n}", event_key=event_key, alliance=alliance, station=str(station)))
            if red_score is None:
                continue
            for alliance, side, score, other in (("red", red, red_score, blue_score), ("blue", blue, blue_score, red_score)):
                won = score > other
                self.db.add(models.MatchAllianceResult(
                    match_key=key, alliance=alliance, event_key=event_key, season_year=int(event_key[:4]),
                    team_keys=[f"frc{n}" for n in side], total_score=score, score_without_fouls=score,
                    result="win" if won else "loss" if score < other else "tie", ranking_points=3 if won else 1 if score == other else 0,
                ))
        self.db.add(models.EventTeamRating(event_key="2026mndu", team_key="frc1", rating_0_100=81.234, details_json={"expected_points": {"total": 42.5}}))
        self.db.commit()

    def test_tells_the_season_event_by_event(self):
        story = get_team_season_story("1", season=2026, db=self.db)
        self.assertEqual((story["team_key"], story["nickname"], story["season"]), ("frc1", "One", 2026))
        self.assertEqual([e["event_key"] for e in story["events"]], ["2026mndu", "2026mnmi"])
        duluth, minneapolis = story["events"]
        self.assertEqual(duluth["record"], {"wins": 4, "losses": 0, "ties": 0})
        self.assertEqual(duluth["finish"], "won")
        self.assertEqual(duluth["qual_rank"], 1)
        self.assertEqual(duluth["rating_0_100"], 81.2)
        self.assertEqual(duluth["points_per_match"], 42.5)
        # The best match is the highest-scoring win.
        self.assertEqual(duluth["best_match"], {"match_key": "2026mndu_f1m1", "alliance": "red", "score": 120, "opponent_score": 100, "result": "win"})
        self.assertEqual(minneapolis["record"], {"wins": 0, "losses": 0, "ties": 1})
        self.assertEqual(minneapolis["finish"], "quals")
        self.assertIsNone(minneapolis["rating_0_100"])
        self.assertEqual(story["totals"]["wins"], 4)
        self.assertEqual(story["totals"]["ties"], 1)
        self.assertEqual(story["totals"]["events"], 2)
        self.assertEqual(story["totals"]["event_wins"], 1)
        self.assertEqual(story["totals"]["best_match"]["event_key"], "2026mndu")

    def test_another_season_and_a_team_with_nothing(self):
        self.assertEqual(len(get_team_season_story("frc1", season=2025, db=self.db)["events"]), 1)
        empty = get_team_season_story("frc99", season=2026, db=self.db)
        self.assertEqual((empty["events"], empty["totals"]["events"]), ([], 0))
