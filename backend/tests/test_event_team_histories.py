import unittest

from app.api import routes_scouting
from app.db import models
from tests.conftest import DBTestCase


class EventTeamHistoriesTests(DBTestCase):
    # Built with one query for the whole roster (it used to run one per team).
    def setUp(self) -> None:
        super().setUp()
        self.db.add_all([
            models.Event(event_key="2026now", name="Now", year=2026),
            models.Event(event_key="2026before", name="Before", year=2026),
        ])
        for number in (1, 2):
            self.db.add(models.Team(team_key=f"frc{number}", team_number=number))
            self.db.add(models.EventTeam(event_key="2026now", team_key=f"frc{number}"))
        run = models.AnalysisRun(match_key="2026before_qm1", version="t", run_kind="official_truth", status="completed")
        for event_key, matches in (("2026before", 3), ("2026now", 1)):
            for index in range(matches):
                self.db.add(models.Match(match_key=f"{event_key}_qm{index + 1}", event_key=event_key, comp_level="qm",
                                         set_number=1, match_number=index + 1, time=1000 + index))
        self.db.flush()
        self.db.add(run)
        self.db.flush()

        def finding(team, event_key, index, rate):
            self.db.add(models.TeamMatchFinding(analysis_run_id=run.id, match_key=f"{event_key}_qm{index}", event_key=event_key,
                                                team_key=team, alliance="red", source="tba_score_breakdown",
                                                fuel_scoring_rate=rate, summary={}))

        for index in (1, 2, 3):
            finding("frc1", "2026before", index, 10.0)
        finding("frc2", "2026before", 1, 20.0)
        finding("frc1", "2026now", 1, 99.0)
        self.db.commit()

    def test_each_team_gets_its_own_prior_matches(self):
        payload = routes_scouting.get_event_team_histories("2026now", history_limit=6, exclude_current_event=True, db=self.db)
        by_team = {team["team_key"]: team for team in payload["teams"]}
        self.assertEqual(by_team["frc1"]["history_count"], 3)
        self.assertEqual(by_team["frc2"]["history_count"], 1)
        self.assertEqual({game["event_key"] for game in by_team["frc1"]["previous_games"]}, {"2026before"})

    def test_current_event_included_when_asked(self):
        payload = routes_scouting.get_event_team_histories("2026now", history_limit=6, exclude_current_event=False, db=self.db)
        by_team = {team["team_key"]: team for team in payload["teams"]}
        self.assertEqual(by_team["frc1"]["history_count"], 4)


if __name__ == "__main__":
    unittest.main()
