from __future__ import annotations

import random
import unittest

from app.db import models
from app.services.ratings.model import recompute_event_ratings
from app.services.ratings.official_points import active_hub_minutes, match_points
from app.services.ratings.output_builder import build_response_payload
from app.services.ratings.strength import (
    MatchPoints,
    curve_for_anchors,
    StrengthParams,
    estimate_strength,
    points_to_rating,
    rating_to_points,
    scale_for_median,
)
from tests.conftest import DBTestCase

TYPICAL = {"teleop": 40.0, "auto": 8.0, "climb": 0.0}
PARAMS = StrengthParams(event_shrink=3.0, prior_shrink=4.0, prior_window=24, recent_window=10, recent_weight=2.0, match_window=20)


def _mp(n: int, teleop: float, auto: float | None = 0.0, climb: float | None = 0.0) -> MatchPoints:
    return MatchPoints(match_key=f"m{n}", time=n, teleop=teleop, auto=auto, climb=climb)


class RatingScaleTests(unittest.TestCase):
    def test_the_median_robot_is_50_and_the_scale_never_reaches_100(self):
        scale = scale_for_median(47.0)
        self.assertAlmostEqual(points_to_rating(47.0, scale), 50.0)
        self.assertEqual(points_to_rating(0.0, scale), 0.0)
        self.assertLess(points_to_rating(400.0, scale), 100.0)
        # The best robots stay apart: 200 and 340 points don't both read 99.
        self.assertGreater(points_to_rating(340.0, scale) - points_to_rating(200.0, scale), 3.0)

    def test_two_anchor_curve_puts_the_median_at_50_and_the_99th_percentile_at_95(self):
        scale, shape = curve_for_anchors(39.74, 253.9)
        self.assertAlmostEqual(points_to_rating(39.74, scale, shape), 50.0, places=6)
        self.assertAlmostEqual(points_to_rating(253.9, scale, shape), 95.0, places=6)
        self.assertAlmostEqual(rating_to_points(points_to_rating(120.0, scale, shape), scale, shape), 120.0, places=6)

    def test_rating_and_points_round_trip(self):
        scale = scale_for_median(47.0)
        for points in (5.0, 47.0, 130.0):
            self.assertAlmostEqual(rating_to_points(points_to_rating(points, scale), scale), points)


class StrengthTests(unittest.TestCase):
    def test_no_history_is_the_typical_robot(self):
        strength = estimate_strength([], [], TYPICAL, PARAMS)
        self.assertEqual(strength.basis, "season_typical")
        self.assertAlmostEqual(strength.points, 48.0)

    def test_earlier_events_shrink_toward_typical(self):
        strength = estimate_strength([], [_mp(1, 100.0, 8.0)], TYPICAL, PARAMS)
        self.assertEqual(strength.basis, "earlier_events")
        self.assertAlmostEqual(strength.teleop, (100.0 + 4 * 40.0) / 5)

    def test_matches_here_shrink_toward_the_earlier_estimate(self):
        before = [_mp(i, 60.0, 8.0) for i in range(1, 25)]  # 24 matches: prior = (24*60 + 4*40) / 28
        prior = (24 * 60.0 + 4 * 40.0) / 28
        strength = estimate_strength([_mp(100, 120.0, 8.0)], before, TYPICAL, PARAMS)
        self.assertEqual(strength.basis, "this_event")
        self.assertAlmostEqual(strength.teleop, (120.0 + 3 * prior) / 4)

    def test_recent_matches_count_double_and_only_the_last_20_count(self):
        here = [_mp(i, 0.0) for i in range(1, 6)] + [_mp(i, 100.0) for i in range(6, 31)]
        strength = estimate_strength(here, [], TYPICAL, StrengthParams(event_shrink=0.0001, prior_shrink=4.0))
        # The five zeros are outside the 20-match window.
        self.assertAlmostEqual(strength.teleop, 100.0, places=2)
        here = [_mp(1, 0.0)] + [_mp(i, 100.0) for i in range(2, 12)]
        strength = estimate_strength(here, [], TYPICAL, StrengthParams(event_shrink=0.0001, prior_shrink=4.0))
        # Ten recent at weight 2, one older at weight 1.
        self.assertAlmostEqual(strength.teleop, 2000.0 / 21.0, places=2)

    def test_unknown_components_fall_back_without_counting_as_zero(self):
        strength = estimate_strength([_mp(1, 80.0, auto=None, climb=None)], [], TYPICAL, PARAMS)
        self.assertAlmostEqual(strength.auto, 8.0)
        self.assertAlmostEqual(strength.climb, 0.0)


class OfficialPointsTests(unittest.TestCase):
    def test_teleop_climb_and_auto(self):
        mp = match_points(
            match_key="2026x_QM1", time=5, fuel_rate=60.0, auto=12.0,
            summary={"status": {"auto_allocation": "event_auto_fuel_copr", "endgame_tower": "Level2"}},
        )
        self.assertEqual(mp.match_key, "2026x_qm1")
        self.assertAlmostEqual(mp.teleop, 60.0 * active_hub_minutes())
        self.assertAlmostEqual(active_hub_minutes(), 99.0 / 60.0)
        self.assertEqual(mp.auto, 12.0)
        self.assertEqual(mp.climb, 20.0)

    def test_even_split_auto_and_missing_climb_are_unknown(self):
        mp = match_points(match_key="m", time=1, fuel_rate=30.0, auto=12.0, summary={"status": {"teleop_allocation": "equal_split"}})
        self.assertIsNone(mp.auto)
        self.assertIsNone(mp.climb)
        self.assertIsNotNone(mp.teleop)


class RecomputeTests(DBTestCase):
    # Ratings now come from the team model (alliance results), so fixtures rotate
    # alliances like a real schedule: robots that always play together can't be told apart.
    def _event(self, event_key: str, teams: list[str]):
        self.db.add(models.Event(event_key=event_key, name=event_key, year=2026))
        for team in teams:
            self.db.add(models.EventTeam(event_key=event_key, team_key=team))

    def _play(self, event_key: str, teams: list[str], teleop: dict[str, float], start: int, matches: int = 16, seed: int = 1):
        rng = random.Random(seed)
        for n in range(1, matches + 1):
            picked = rng.sample(teams, 6)
            match_key = f"{event_key}_qm{n}"
            self.db.add(models.Match(match_key=match_key, event_key=event_key, comp_level="qm", set_number=1, match_number=n, time=start + n))
            self.db.flush()
            run = models.AnalysisRun(match_key=match_key, run_kind="official_truth", status="completed")
            self.db.add(run)
            self.db.flush()
            for alliance, side in (("red", picked[:3]), ("blue", picked[3:])):
                total = sum(teleop[t] for t in side)
                other = sum(teleop[t] for t in (picked[3:] if alliance == "red" else picked[:3]))
                self.db.add(models.MatchAllianceResult(
                    match_key=match_key, alliance=alliance, event_key=event_key, season_year=2026,
                    team_keys=side, surrogate_team_keys=[], dq_team_keys=[],
                    total_score=int(total), score_without_fouls=int(total),
                    result="win" if total > other else "loss" if total < other else "tie",
                    components={"teleop_hub_points": total, "auto_hub_points": 0,
                                "robot_auto_tower_points": [0.0] * 3, "robot_endgame_tower_points": [0.0] * 3},
                    raw_breakdown={},
                ))
                for team in side:
                    self.db.add(models.TeamMatchFinding(
                        analysis_run_id=run.id, match_key=match_key, event_key=event_key, team_key=team,
                        alliance=alliance, source="tba_score_breakdown", fuel_scoring_rate=total / 3 / active_hub_minutes(),
                        auto_contribution=0.0,
                        summary={"status": {"auto_allocation": "event_auto_fuel_copr", "endgame_tower": "None"}},
                    ))
        self.db.commit()

    def setUp(self):
        super().setUp()
        for n in range(1, 13):
            self.db.add(models.Team(team_key=f"frc{n}", team_number=n))
        self.db.commit()

    def _row(self, event_key, team_key):
        return self.db.get(models.EventTeamRating, (event_key, team_key))

    def test_same_output_same_rating_whatever_the_field(self):
        # frc1 scores ~100 at a weak and a strong event; v14 rated it by rank in each.
        weak = ["frc1"] + [f"frc{n}" for n in range(2, 9)]
        strong = ["frc1"] + [f"frc{n}" for n in range(9, 13)] + ["frc2", "frc3", "frc4"]
        self._event("2026weak", weak)
        self._event("2026strong", strong)
        self._play("2026weak", weak, {t: (100.0 if t == "frc1" else 20.0) for t in weak}, start=1000, matches=24)
        self._play("2026strong", strong, {t: (100.0 if t == "frc1" else 20.0 if t in ("frc2", "frc3", "frc4") else 200.0) for t in strong},
                   start=5000, matches=24, seed=2)
        recompute_event_ratings(self.db, "2026weak")
        recompute_event_ratings(self.db, "2026strong")
        weak_row, strong_row = self._row("2026weak", "frc1"), self._row("2026strong", "frc1")
        self.assertAlmostEqual(weak_row.details_json["expected_points"]["teleop"], 100.0, delta=15.0)
        self.assertAlmostEqual(strong_row.details_json["expected_points"]["teleop"], 100.0, delta=20.0)
        self.assertLess(abs(weak_row.rating_0_100 - strong_row.rating_0_100), 8.0)
        self.assertEqual(max(self.db.query(models.EventTeamRating).filter_by(event_key="2026weak"), key=lambda r: r.rating_0_100).team_key, "frc1")

    def test_a_listed_team_that_has_not_played_here_keeps_its_earlier_form(self):
        # Einstein: TBA lists every Championship team, most never play there. v14 pulled
        # all of them to ~47; the best robot in the world read 44.7.
        division = [f"frc{n}" for n in range(1, 9)]
        self._event("2026div", division)
        self._event("2026einstein", division + ["frc12"])
        self._play("2026div", division, {t: (180.0 if t == "frc1" else 30.0) for t in division}, start=1000, matches=24)
        self.db.add(models.Match(match_key="2026einstein_f1m1", event_key="2026einstein", comp_level="f", set_number=1, match_number=1, time=5000))
        self.db.commit()
        recompute_event_ratings(self.db, "2026einstein")
        star, newcomer = self._row("2026einstein", "frc1"), self._row("2026einstein", "frc12")
        self.assertGreater(star.rating_0_100, 85.0)
        self.assertEqual(star.details_json["basis"], "earlier_events")
        self.assertEqual(newcomer.details_json["basis"], "season_typical")
        # A team with no history is the season's prior, with a wide range.
        low, high = newcomer.details_json["rating_range_80"]
        self.assertGreater(high - low, 20.0)

    def test_row_shape(self):
        teams = [f"frc{n}" for n in range(1, 9)]
        self._event("2026solo", teams)
        self._play("2026solo", teams, {t: 25.0 * int(t[3:]) for t in teams}, start=1000, matches=20)
        recompute_event_ratings(self.db, "2026solo")
        row = self._row("2026solo", "frc8")
        self.assertIsNone(row.driver_skill_0_100)
        self.assertIsNone(row.results_anchor)
        self.assertIsNone(row.shift_productivity)
        self.assertEqual(row.model_version, "rating_v16_team_model")
        self.assertAlmostEqual(row.details_json["expected_points"]["teleop"], 200.0, delta=30.0)
        low, high = row.details_json["rating_range_80"]
        self.assertLess(low, row.rating_0_100)
        self.assertGreater(high, row.rating_0_100)
        payload = build_response_payload([(row, self.db.get(models.Team, "frc8"))])[0]
        played = sum(1 for r in self.db.query(models.MatchAllianceResult) if "frc8" in r.team_keys)
        self.assertEqual(payload["matches_here"], played)
        self.assertIn("teleop", payload["expected_points"])
        self.assertIsNone(payload["subscores"]["results_anchor"])
        self.assertTrue(any(p["rule_id"] == "high_teleop" for p in row.pros_json))
        self.assertLess(self._row("2026solo", "frc1").rating_0_100, row.rating_0_100)
        # Why it moved: the last matches here, newest first, with their effect.
        changes = payload["recent_changes"]
        self.assertTrue(0 < len(changes) <= 5)
        self.assertTrue(all(c["match_key"].startswith("2026solo_qm") for c in changes))
        self.assertGreater(int(changes[0]["match_key"].split("qm")[1]), int(changes[-1]["match_key"].split("qm")[1]))
        # The live trend: the rating before the last 12 matches here, then after each,
        # ending at the rating itself; its last step is the newest change.
        history = row.details_json["rating_history"]
        self.assertEqual(len(history), 13)
        self.assertAlmostEqual(history[-1], row.rating_0_100, delta=0.01)
        self.assertAlmostEqual(history[-1] - history[-2], changes[0]["rating_change"], delta=0.02)
        from app.api.routes_scouting import get_event_ratings

        live = get_event_ratings("2026solo", request=None, include_trend=True, view="summary", db=self.db)
        trend = next(r for r in live["ratings"] if r["team_key"] == "frc8")["rating_trend"]
        self.assertEqual(trend["sparkline"], [round(v, 2) for v in history[-12:]])
        self.assertEqual(trend["snapshot_count"], 13)

    def test_events_not_started_are_rated_from_earlier_events(self):
        from app.services.events.regional_automation import rate_events_before_play

        teams = [f"frc{n}" for n in range(1, 9)]
        self._event("2026early", teams)
        self._play("2026early", teams, {t: 25.0 * int(t[3:]) for t in teams}, start=1000)
        self._event("2026later", ["frc1", "frc8"])
        self.db.commit()
        result = rate_events_before_play(self.db, 2026)
        self.assertEqual(result["rated_event_count"], 1)
        self.assertIsNone(self._row("2026early", "frc8"))  # started events are the refresh's job
        strong, weak = self._row("2026later", "frc8"), self._row("2026later", "frc1")
        self.assertEqual(strong.details_json["basis"], "earlier_events")
        self.assertGreater(strong.rating_0_100, weak.rating_0_100)
        self.assertEqual(strong.details_json["rating_history"], [])

    def test_a_team_without_matches_this_season_says_where_its_start_comes_from(self):
        from datetime import datetime, timezone

        self.db.add(models.TeamPriorStrength(season=2025, team_key="frc11", points=90.0, z=0.8, matches=40, source="tba", updated_at=datetime.now(timezone.utc)))
        self.db.get(models.Team, "frc12").rookie_year = 2026
        self._event("2026fresh", ["frc10", "frc11", "frc12"])
        self.db.commit()
        recompute_event_ratings(self.db, "2026fresh")
        bases = {t: self._row("2026fresh", t).details_json["basis"] for t in ("frc10", "frc11", "frc12")}
        self.assertEqual(bases, {"frc10": "season_typical", "frc11": "last_season", "frc12": "rookie"})
        self.assertTrue(any(c["rule_id"] == "last_season_only" for c in self._row("2026fresh", "frc11").cons_json))
        self.assertGreater(self._row("2026fresh", "frc11").rating_0_100, self._row("2026fresh", "frc10").rating_0_100)


if __name__ == "__main__":
    unittest.main()
