import unittest
from datetime import datetime, timedelta, timezone

from app.api import routes_teams
from app.db import models
from tests.conftest import DBTestCase


class TeamRatingFallbackTests(DBTestCase):
    # 254's page used its Einstein rating: TBA lists every Championship team on the
    # finals event, only a few play there, and the team read 39.0, 596th of 605.
    def setUp(self) -> None:
        super().setUp()
        now = datetime.now(timezone.utc)
        self.db.add_all([
            models.Event(event_key="2026cur", name="Curie", year=2026),
            models.Event(event_key="2026cmptx", name="Einstein", year=2026),
        ])
        for number in range(1, 11):
            self.db.add(models.Team(team_key=f"frc{number}", team_number=number))
        self.db.add(models.Team(team_key="frc254", team_number=254))
        self.db.flush()
        for number, event_key, matches in ((254, "2026cur", 10), (254, "2026cmptx", 2), (1, "2026cmptx", 2)):
            for index in range(matches):
                match_key = f"{event_key}_qm{number}_{index}"
                self.db.add(models.Match(match_key=match_key, event_key=event_key, comp_level="qm", set_number=1, match_number=index + 1))
                self.db.flush()
                self.db.add(models.MatchTeam(match_key=match_key, team_key=f"frc{number}", event_key=event_key, alliance="red"))
        self.db.add(models.EventTeamRating(event_key="2026cur", team_key="frc254", rating_0_100=91.0, updated_at=now - timedelta(days=3)))
        # Einstein: newer row, and a roster of teams that never played there.
        self.db.add(models.EventTeamRating(event_key="2026cmptx", team_key="frc254", rating_0_100=39.0, updated_at=now))
        self.db.add(models.EventTeamRating(event_key="2026cmptx", team_key="frc1", rating_0_100=35.0, updated_at=now))
        for number in range(2, 11):
            self.db.add(models.EventTeamRating(event_key="2026cmptx", team_key=f"frc{number}", rating_0_100=60.0, updated_at=now))
        self.db.commit()

    def test_fallback_uses_the_event_the_team_played_most(self):
        row, event_key = routes_teams._latest_team_rating_row(self.db, "frc254")
        self.assertEqual(event_key, "2026cur")
        self.assertEqual(row.rating_0_100, 91.0)

    def test_rank_ignores_roster_teams_that_never_played(self):
        context = routes_teams._event_rating_rank_context(self.db, event_key="2026cmptx", team_key="frc254")
        self.assertEqual(context, {"rank": 1, "field_size": 2})


if __name__ == "__main__":
    unittest.main()
