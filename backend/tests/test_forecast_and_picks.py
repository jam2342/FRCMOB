from __future__ import annotations

import random

import pytest
from fastapi import HTTPException

from app.api import routes_alliances, routes_events
from app.db import models
from tests.conftest import DBTestCase

EVENT = "2026sim"
TEAMS = [f"frc{n}" for n in range(1, 31)]


class ForecastAndPickValueTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self.db.add(models.Event(event_key=EVENT, name="Sim", year=2026))
        for n, team in enumerate(TEAMS, start=1):
            self.db.add(models.Team(team_key=team, team_number=n))
            self.db.add(models.EventTeam(event_key=EVENT, team_key=team))
        rng = random.Random(4)
        truth = {t: 10.0 * n for n, t in enumerate(TEAMS, start=1)}  # frc30 is the best
        for number in range(1, 61):
            picked = rng.sample(TEAMS, 6)
            key = f"{EVENT}_qm{number}"
            self.db.add(models.Match(match_key=key, event_key=EVENT, comp_level="qm", set_number=1, match_number=number, time=1000 + number))
            for alliance, side in (("red", picked[:3]), ("blue", picked[3:])):
                for station, team in enumerate(side, start=1):
                    self.db.add(models.MatchTeam(match_key=key, team_key=team, event_key=EVENT, alliance=alliance, station=f"{alliance[0]}{station}"))
            if number > 40:
                continue  # the last 20 haven't been played
            totals = {"red": sum(truth[t] for t in picked[:3]), "blue": sum(truth[t] for t in picked[3:])}
            for alliance, side in (("red", picked[:3]), ("blue", picked[3:])):
                other = totals["blue" if alliance == "red" else "red"]
                won = totals[alliance] > other
                self.db.add(models.MatchAllianceResult(
                    match_key=key, alliance=alliance, event_key=EVENT, season_year=2026, team_keys=side,
                    surrogate_team_keys=[], dq_team_keys=[], total_score=int(totals[alliance]),
                    score_without_fouls=int(totals[alliance]), result="win" if won else "loss",
                    ranking_points=3 if won else 0,
                    components={"teleop_hub_points": totals[alliance], "auto_hub_points": 0, "hub_fuel_count": totals[alliance],
                                "energized_rp": totals[alliance] >= 100, "robot_auto_tower_points": [0.0] * 3,
                                "robot_endgame_tower_points": [0.0] * 3},
                    raw_breakdown={},
                ))
        self.db.commit()

    def test_forecast_covers_the_rest_of_the_event(self):
        forecast = routes_events.get_event_forecast(EVENT, db=self.db)
        self.assertEqual((forecast["quals_played"], forecast["quals_remaining"]), (40, 20))
        rows = {r["team_key"]: r for r in forecast["teams"]}
        self.assertEqual(len(rows), 30)
        self.assertAlmostEqual(sum(r["top8"] for r in rows.values()), 8.0, places=2)
        self.assertAlmostEqual(sum(r["win_event"] for r in rows.values()), 3.0, places=2)
        # The best robots are the likeliest to win; the weakest almost never do.
        self.assertGreater(rows["frc30"]["win_event"], rows["frc1"]["win_event"])
        self.assertLess(rows["frc30"]["expected_rank"], rows["frc1"]["expected_rank"])
        # A second request is served from the cache.
        self.assertIs(routes_events.get_event_forecast(EVENT, db=self.db), forecast)

    def test_pick_values_rank_candidates_for_a_captain(self):
        result = routes_events.get_event_pick_values(EVENT, teams="frc30", limit=5, db=self.db)
        self.assertEqual(result["partners"], ["frc30"])
        candidates = [c["team_key"] for c in result["candidates"]]
        self.assertNotIn("frc30", candidates)
        self.assertEqual(len(candidates), 5)
        # The best picks are the strongest robots left (40 matches can swap close
        # neighbours), and win chances fall down the list.
        self.assertTrue(set(candidates[:3]) <= {"frc29", "frc28", "frc27", "frc26"})
        chances = [c["win_vs_strong_alliance"] for c in result["candidates"]]
        self.assertEqual(chances, sorted(chances, reverse=True))
        self.assertEqual(len(result["reference"]), 3)
        self.assertIn("energized", result["candidates"][0]["bonus_rp_chances"])

    def test_alliance_outlook_ranks_a_strong_trio_above_a_weak_one(self):
        strong = routes_alliances.get_alliance_outlook(EVENT, teams="frc30,frc29,frc28", db=self.db)
        weak = routes_alliances.get_alliance_outlook(EVENT, teams="1,2,3", db=self.db)
        self.assertEqual([t["team_key"] for t in weak["teams"]], ["frc1", "frc2", "frc3"])
        self.assertGreater(strong["alliance_points"], weak["alliance_points"])
        self.assertGreater(strong["vs_strong"]["win_prob"], weak["vs_strong"]["win_prob"])
        # A typical alliance is easier to beat than a strong one.
        self.assertGreater(strong["vs_typical"]["win_prob"], strong["vs_strong"]["win_prob"])
        # The reference alliances never include the teams being judged.
        self.assertFalse(set(strong["vs_strong"]["teams"]) & {"frc30", "frc29", "frc28"})
        self.assertGreaterEqual(weak["alliance_points_range_80"][0], 0.0)
        low, high = strong["alliance_points_range_80"]
        self.assertLess(low, strong["alliance_points"])
        self.assertLess(strong["alliance_points"], high)

    def test_alliance_outlook_refuses_bad_lineups(self):
        for teams in ("frc30,frc29", "frc30,frc30,frc29", "frc30,frc29,frc999"):
            with pytest.raises(HTTPException) as caught:
                routes_alliances.get_alliance_outlook(EVENT, teams=teams, db=self.db)
            self.assertEqual(caught.value.status_code, 400)

    def test_selection_model_picks_strong_teams_first(self):
        result = routes_alliances.get_selection_model(
            EVENT, rank_weight=1.0, scale=35.0, captains=8, simulations=200, rank_source="model", db=self.db
        )["selection_model"]
        self.assertEqual(len(result["captains"]), 8)
        probs = result["team_pick_probability_0_1"]
        captains = set(result["captains"])
        best_pick = max((t for t in probs if t not in captains), key=probs.get)
        self.assertNotIn(best_pick, {"frc1", "frc2", "frc3"})

    def test_a_playoff_only_event_is_not_simulated(self):
        self.db.add(models.Event(event_key="2026einstein", name="Einstein", year=2026))
        for team in TEAMS:
            self.db.add(models.EventTeam(event_key="2026einstein", team_key=team))
        self.db.commit()
        forecast = routes_events.get_event_forecast("2026einstein", db=self.db)
        self.assertFalse(forecast["simulated"])
        self.assertEqual(len(forecast["teams"]), 30)
        self.assertNotIn("top8", forecast["teams"][0])

    def test_matchup_favours_the_stronger_six_and_adds_up(self):
        result = routes_events.get_event_matchup(EVENT, red="frc30,frc29,frc28", blue="1,2,3", playoff=False, db=self.db)
        self.assertEqual(result["red"], ["frc30", "frc29", "frc28"])
        self.assertEqual(result["blue"], ["frc1", "frc2", "frc3"])
        self.assertGreater(result["red_points"], result["blue_points"])
        self.assertGreater(result["red_win_prob"], 0.9)
        self.assertAlmostEqual(result["red_win_prob"] + result["blue_win_prob"], 1.0, places=3)
        swapped = routes_events.get_event_matchup(EVENT, red="1,2,3", blue="frc30,frc29,frc28", playoff=False, db=self.db)
        self.assertAlmostEqual(swapped["blue_win_prob"], result["red_win_prob"], places=2)
        playoff = routes_events.get_event_matchup(EVENT, red="frc30,frc29,frc28", blue="1,2,3", playoff=True, db=self.db)
        self.assertTrue(playoff["playoff"])

    def test_matchup_refuses_bad_lineups(self):
        bad = (
            ("frc30,frc29", "1,2,3"),
            ("frc30,frc29,frc28", "frc30,2,3"),
            ("frc30,frc29,frc999", "1,2,3"),
            ("frc30,frc29,frc28,frc27", "1,2,3"),  # a fourth team is refused, not dropped
        )
        for red, blue in bad:
            with pytest.raises(HTTPException) as caught:
                routes_events.get_event_matchup(EVENT, red=red, blue=blue, playoff=False, db=self.db)
            self.assertEqual(caught.value.status_code, 400)
        with pytest.raises(HTTPException) as missing:
            routes_events.get_event_matchup("2026nowhere", red="1,2,3", blue="4,5,6", playoff=False, db=self.db)
        self.assertEqual(missing.value.status_code, 404)

    def test_what_if_moves_a_team_up_when_it_wins_out(self):
        model = routes_events.get_event_what_if(EVENT, team="frc5", wins="", losses="", db=self.db)
        matches = model["matches"]
        self.assertTrue(matches)
        self.assertEqual(model["field_size"], 30)
        for row in matches:
            self.assertIn(row["alliance"], ("red", "blue"))
            self.assertEqual(len(row["partners"]), 2)
            self.assertEqual(len(row["opponents"]), 3)
            self.assertIsNone(row["forced"])
        # Nothing forced: the what-if is the model.
        self.assertEqual(model["what_if"]["expected_rank"], model["model"]["expected_rank"])
        keys = ",".join(row["match_key"] for row in matches)
        win_out = routes_events.get_event_what_if(EVENT, team="5", wins=keys, losses="", db=self.db)
        lose_out = routes_events.get_event_what_if(EVENT, team="frc5", wins="", losses=keys, db=self.db)
        self.assertTrue(all(row["forced"] == "win" for row in win_out["matches"]))
        self.assertLess(win_out["what_if"]["expected_rank"], model["model"]["expected_rank"])
        self.assertGreater(lose_out["what_if"]["expected_rank"], model["model"]["expected_rank"])
        self.assertGreaterEqual(win_out["what_if"]["top8"], model["model"]["top8"])
        counts = win_out["what_if"]["rank_counts"]
        self.assertEqual((len(counts), sum(counts)), (30, win_out["runs"]))
        self.assertLessEqual(win_out["what_if"]["rank_likely_best"], win_out["what_if"]["rank_likely_worst"])

    def test_what_if_refuses_a_team_not_at_the_event(self):
        with pytest.raises(HTTPException) as caught:
            routes_events.get_event_what_if(EVENT, team="frc9999", wins="", losses="", db=self.db)
        self.assertEqual(caught.value.status_code, 400)

    def test_what_if_refuses_a_match_set_as_both_a_win_and_a_loss(self):
        key = routes_events.get_event_what_if(EVENT, team="frc5", wins="", losses="", db=self.db)["matches"][0]["match_key"]
        with pytest.raises(HTTPException) as caught:
            routes_events.get_event_what_if(EVENT, team="frc5", wins=key, losses=key.upper(), db=self.db)
        self.assertEqual(caught.value.status_code, 400)
