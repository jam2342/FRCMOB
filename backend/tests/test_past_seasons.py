# Past seasons (services/seasons.py): frozen seasons serve archived predictions and never
# rebuild, seasons without ratings say so instead of failing, and Team Center keeps each
# season's rating apart.
from __future__ import annotations

import random
from unittest.mock import patch

from app.api import routes_events, routes_teams
from app.api.teams.history import get_team_rating_history
from app.db import models
from app.services.ratings.model import recompute_event_ratings
from app.services.seasons import freeze_season, season_info
from tests.conftest import DBTestCase

EVENT = "2026done"
TEAMS = [f"frc{n}" for n in range(1, 25)]
TRUTH = {t: 10.0 * n for n, t in enumerate(TEAMS, start=1)}


def _result(db, key, event, alliance, side, total, other, season=2026):
    won = total > other
    db.add(models.MatchAllianceResult(
        match_key=key, alliance=alliance, event_key=event, season_year=season, team_keys=list(side),
        surrogate_team_keys=[], dq_team_keys=[], total_score=int(total), score_without_fouls=int(total),
        result="win" if won else "loss", ranking_points=3 if won else 0,
        components={"teleop_hub_points": total, "auto_hub_points": 0, "hub_fuel_count": total,
                    "robot_auto_tower_points": [0.0] * 3, "robot_endgame_tower_points": [0.0] * 3},
        raw_breakdown={},
    ))


class PastSeasonTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self.db.add(models.Event(event_key=EVENT, name="Done Regional", year=2026))
        for n, team in enumerate(TEAMS, start=1):
            self.db.add(models.Team(team_key=team, team_number=n))
            self.db.add(models.EventTeam(event_key=EVENT, team_key=team))
        rng = random.Random(3)
        for number in range(1, 41):
            picked = rng.sample(TEAMS, 6)
            self._match(f"{EVENT}_qm{number}", "qm", number, picked[:3], picked[3:], 1000 + number)
        # Finals: the strongest trio beats the next one twice.
        for number in (1, 2):
            self._match(f"{EVENT}_f1m{number}", "f", number, ["frc24", "frc23", "frc22"], ["frc21", "frc20", "frc19"], 2000 + number)
        self.db.commit()
        recompute_event_ratings(self.db, EVENT)

    def _match(self, key, level, number, red, blue, when):
        self.db.add(models.Match(match_key=key, event_key=EVENT, comp_level=level, set_number=1, match_number=number, time=when))
        for alliance, side in (("red", red), ("blue", blue)):
            for station, team in enumerate(side, start=1):
                self.db.add(models.MatchTeam(match_key=key, team_key=team, event_key=EVENT, alliance=alliance, station=f"{alliance[0]}{station}"))
        totals = {"red": sum(TRUTH[t] for t in red), "blue": sum(TRUTH[t] for t in blue)}
        _result(self.db, key, EVENT, "red", red, totals["red"], totals["blue"])
        _result(self.db, key, EVENT, "blue", blue, totals["blue"], totals["red"])

    def _schedule(self, event_key=EVENT):
        return routes_events.get_event_schedule_with_synergy(
            event_key=event_key, db=self.db,
        )

    def test_a_finished_event_shows_what_happened(self):
        forecast = routes_events.get_event_forecast(EVENT, db=self.db)
        self.assertTrue(forecast["finished"])
        self.assertEqual(sorted(forecast["winners"]), ["frc22", "frc23", "frc24"])
        self.assertEqual(sorted(forecast["finalists"]), ["frc19", "frc20", "frc21"])
        rows = {r["team_key"]: r for r in forecast["teams"]}
        self.assertTrue(rows["frc24"]["won_event"])
        self.assertEqual(sorted(r["current_rank"] for r in rows.values()), list(range(1, 25)))
        self.assertIsNotNone(rows["frc24"]["points_per_match"])

    def test_a_frozen_season_serves_its_archive_and_never_rebuilds(self):
        live = {m["match_key"]: m["prediction"] for m in self._schedule()["matches"]}
        result = freeze_season(self.db, 2026, allow_current=True, note="test")
        self.assertEqual(result["archived_predictions"], 42)
        self.assertEqual(freeze_season(self.db, 2026, allow_current=True)["status"], "already_frozen")
        before = {r.team_key: r.rating_0_100 for r in self.db.query(models.EventTeamRating)}
        with patch("app.services.ratings.team_model_service.season_run", side_effect=AssertionError("rebuilt a frozen season")), \
             patch("app.services.events.win_probability.season_run", side_effect=AssertionError("rebuilt a frozen season")), \
             patch("app.services.simulation.forecast.season_run", side_effect=AssertionError("rebuilt a frozen season")):
            payload = self._schedule()
            self.assertTrue(payload["season"]["frozen"])
            self.assertEqual({m["match_key"]: m["prediction"] for m in payload["matches"]}, live)
            self.assertTrue(routes_events.get_event_forecast(EVENT, db=self.db)["finished"])
            picks = routes_events.get_event_pick_values(EVENT, teams="frc24", limit=5, db=self.db)
            self.assertEqual((picks["candidates"], picks["unavailable"]), ([], "season_frozen"))
            self.assertEqual(recompute_event_ratings(self.db, EVENT)["skipped"], "season_frozen")
        self.assertEqual({r.team_key: r.rating_0_100 for r in self.db.query(models.EventTeamRating)}, before)

    def test_the_current_season_is_not_frozen_by_accident(self):
        with self.assertRaises(ValueError):
            freeze_season(self.db, 2026)
        with self.assertRaises(ValueError):
            freeze_season(self.db, 2025)

    def test_a_season_without_ratings_has_no_predictions_instead_of_failing(self):
        self.db.add(models.Event(event_key="2025old", name="Old Regional", year=2025))
        self.db.add(models.EventTeam(event_key="2025old", team_key="frc1"))
        self.db.add(models.Match(match_key="2025old_qm1", event_key="2025old", comp_level="qm", set_number=1, match_number=1, time=10))
        for alliance, side in (("red", TEAMS[:3]), ("blue", TEAMS[3:6])):
            for team in side:
                self.db.add(models.MatchTeam(match_key="2025old_qm1", team_key=team, event_key="2025old", alliance=alliance, station=None))
        self.db.commit()
        payload = self._schedule("2025old")
        self.assertEqual(payload["season"]["rated"], False)
        self.assertEqual(payload["season"]["game"], "REEFSCAPE")
        self.assertFalse(payload["matches"][0]["prediction"]["available"])
        self.assertEqual(routes_events.get_event_forecast("2025old", db=self.db)["unavailable"], "season_not_rated")
        self.assertEqual(routes_events.get_event_pick_values("2025old", teams="frc1", db=self.db)["unavailable"], "season_not_rated")
        self.assertEqual(recompute_event_ratings(self.db, "2025old")["skipped"], "season_not_rated")
        self.assertEqual(season_info(self.db, 2025)["rated"], False)

    def test_team_center_keeps_each_season_apart(self):
        # In 2027, before the team has a 2027 rating: no headline rating, last season apart.
        with patch("app.services.seasons.current_season", return_value=2027):
            rating, _fallbacks, _warnings = routes_teams._resolve_team_rating_context(
                db=self.db, team_key="frc24", event_key=None, auto_heal_ratings=False,
            )
        self.assertFalse(rating["available"])
        previous = rating["previous_season"]
        self.assertEqual((previous["season"], previous["game"], previous["event_key"]), (2026, "REBUILT", EVENT))
        self.assertIn(previous["rank"], (1, 2, 3))
        self.assertEqual(previous["field_size"], 24)
        # A 2027 event with no rating for the team doesn't borrow the 2026 one either.
        self.db.add(models.Event(event_key="2027new", name="New Regional", year=2027))
        self.db.commit()
        rating, _f, _w = routes_teams._resolve_team_rating_context(db=self.db, team_key="frc24", event_key="2027new", auto_heal_ratings=False)
        self.assertFalse(rating["available"])
        self.assertEqual(rating["previous_season"]["season"], 2026)
        # In its own season the rating is the headline.
        rating, _f, _w = routes_teams._resolve_team_rating_context(db=self.db, team_key="frc24", event_key=None, auto_heal_ratings=False)
        self.assertTrue(rating["available"])

    def test_rating_history_lists_seasons_apart(self):
        history = get_team_rating_history("frc24", db=self.db)
        self.assertEqual([s["season"] for s in history["seasons"]], [2026])
        season = history["seasons"][0]
        self.assertEqual(season["game"], "REBUILT")
        event = season["events"][0]
        self.assertEqual((event["event_key"], event["won_event"], event["field_size"]), (EVENT, True, 24))
        self.assertIn(event["rank"], (1, 2, 3))
        self.assertEqual(history["first_rated_season"], 2026)
        # A listed team that never played here has no history entry.
        self.db.add(models.Team(team_key="frc999", team_number=999))
        self.db.add(models.EventTeam(event_key=EVENT, team_key="frc999"))
        self.db.commit()
        recompute_event_ratings(self.db, EVENT)
        self.assertEqual(get_team_rating_history("frc999", db=self.db)["seasons"], [])


def test_the_history_module_imports_on_its_own():
    import subprocess
    import sys

    result = subprocess.run([sys.executable, "-c", "import app.api.teams.history"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
