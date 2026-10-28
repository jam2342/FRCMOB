from __future__ import annotations

import random
import unittest
import unittest.mock

from app.services.ratings.replay import MatchCard, MatchResult, Season, StrengthV15, run_replay, split_holdout, typical_robot
from app.services.ratings.strength import MatchPoints, StrengthParams
from tests.conftest import DBTestCase
from app.services.ratings.team_model import (
    ComponentParams,
    SeasonTeamModel,
    TeamModelParams,
    component_priors,
)

TEAMS = [f"frc{n}" for n in range(1, 25)]
TRUE = {team: 10.0 + 8.0 * i for i, team in enumerate(TEAMS)}  # 10 .. 194 teleop points


def _components(teams, noise_rng, noise):
    total = sum(TRUE[t] for t in teams) + noise_rng.gauss(0, noise)
    return {
        "teleop_hub_points": total,
        "auto_hub_points": 0.0,
        "robot_auto_tower_points": [0.0, 0.0, 0.0],
        "robot_endgame_tower_points": [0.0, 0.0, 0.0],
    }, total


def _season(events=6, per_event=40, noise=30.0, seed=3) -> Season:
    # Findings carry an even split of each alliance's teleop, the worst case for a
    # per-robot rating; the alliance totals are real.
    rng = random.Random(seed)
    results, t = [], 0
    for e in range(events):
        event_key = f"2026e{e}"
        start = t + 1
        for m in range(per_event):
            t += 1
            picked = rng.sample(TEAMS, 6)
            red, blue = tuple(picked[:3]), tuple(picked[3:])
            red_c, red_total = _components(red, rng, noise)
            blue_c, blue_total = _components(blue, rng, noise)
            if round(red_total) == round(blue_total):
                red_total += 1
                red_c["teleop_hub_points"] = red_total
            robots = {team: MatchPoints(f"{event_key}_qm{m}", t, red_total / 3, 0.0, 0.0) for team in red}
            robots |= {team: MatchPoints(f"{event_key}_qm{m}", t, blue_total / 3, 0.0, 0.0) for team in blue}
            card = MatchCard(f"{event_key}_qm{m}", event_key, t, "qm", start, red, blue)
            results.append(MatchResult(card, round(red_total), round(blue_total), red_total, blue_total, robots, red_c, blue_c))
    holdout = split_holdout(results)
    return Season(2026, results, holdout, typical_robot(results, {r.card.event_key for r in results} - holdout))


class TeamModelTests(unittest.TestCase):
    def test_one_match_moves_the_unsure_team_most(self):
        params = TeamModelParams(teleop=ComponentParams(prior_var=100.0, match_var=0.0, event_var=0.0, noise_var=100.0))
        model = SeasonTeamModel({"teleop": 50.0, "auto": 0.0, "tower": 0.0}, params)
        # frc1 has 10 earlier matches of evidence; frc2 and frc3 have none.
        for i in range(10):
            card = MatchCard(f"m{i}", "e0", i, "qm", 0, ("frc1", "x1", "x2"), ("y1", "y2", "y3"))
            model.predict(card)
            model.observe(MatchResult(card, 150, 150, 150.0, 150.0, {}, {"teleop_hub_points": 150.0}, {"teleop_hub_points": 150.0}))
        before = model.belief("frc1", "e0")["teleop"]
        card = MatchCard("m10", "e0", 10, "qm", 0, ("frc1", "frc2", "frc3"), ("y1", "y2", "y3"))
        model.predict(card)
        model.observe(MatchResult(card, 240, 150, 240.0, 150.0, {}, {"teleop_hub_points": 240.0}, {"teleop_hub_points": 150.0}))
        frc1, frc2 = model.belief("frc1", "e0")["teleop"], model.belief("frc2", "e0")["teleop"]
        self.assertLess(abs(frc1.mean - before.mean), abs(frc2.mean - 50.0))
        self.assertLess(frc2.var, 100.0)

    def test_a_new_event_widens_the_belief(self):
        params = TeamModelParams()
        model = SeasonTeamModel({"teleop": 50.0, "auto": 10.0, "tower": 2.0}, params)
        card = MatchCard("a1", "e0", 1, "qm", 1, ("frc1", "frc2", "frc3"), ("frc4", "frc5", "frc6"))
        model.predict(card)
        model.observe(MatchResult(card, 200, 100, 200.0, 100.0, {}, {"teleop_hub_points": 180.0}, {"teleop_hub_points": 90.0}))
        here = model.belief("frc1", "e0")["teleop"]
        next_event = model.belief("frc1", "e1")["teleop"]
        self.assertEqual(here.mean, next_event.mean)
        self.assertAlmostEqual(next_event.var, here.var + params.teleop.event_var)

    def test_recovers_robots_from_alliance_totals_where_an_even_split_cannot(self):
        season = _season()
        priors = component_priors(season.results, {r.card.event_key for r in season.results} - season.holdout_events)
        team = run_replay(season, SeasonTeamModel(priors, TeamModelParams()))
        v15 = run_replay(season, StrengthV15(season.typical, StrengthParams()))
        self.assertLess(team["alliance_points_error"]["mean_abs"], v15["alliance_points_error"]["mean_abs"])
        self.assertLess(team["matches"]["all"]["brier"], v15["matches"]["all"]["brier"])
        # (No next-event check: its target is each robot's findings, here an even split.)
        model = SeasonTeamModel(priors, TeamModelParams())
        for result in season.results:
            model.predict(result.card)
            model.observe(result)
        # The strongest and weakest robots come out near their true values, not squeezed together.
        self.assertGreater(model.belief("frc24")["teleop"].mean, 160.0)
        self.assertLess(model.belief("frc1")["teleop"].mean, 45.0)


if __name__ == "__main__":
    unittest.main()


class SeasonRunCacheTests(unittest.TestCase):
    def test_a_page_gets_the_last_run_at_once_while_a_new_one_builds(self):
        from unittest.mock import patch

        from app.services.ratings import team_model_service as service

        old_run, new_run = object(), object()
        built = []
        service.clear_cache()
        service._cache[2026] = (("old",), old_run)
        with (
            patch.object(service, "STALE_READS", True),
            patch.object(service, "_fingerprint", return_value=("new",)),
            patch.object(service, "_compute", side_effect=lambda db, season, fp: built.append(fp) or new_run),
            patch.object(service.threading, "Thread") as thread,
        ):
            db = unittest.mock.MagicMock()
            # A page request: the stale run now, a rebuild started in the background.
            self.assertIs(service.season_run(db, 2026, allow_stale=True), old_run)
            thread.assert_called_once()
            # A second request while it builds doesn't start another.
            self.assertIs(service.season_run(db, 2026, allow_stale=True), old_run)
            thread.assert_called_once()
            # A ratings job waits for the fresh run.
            self.assertIs(service.season_run(db, 2026), new_run)
            self.assertEqual(built, [("new",)])
        service.clear_cache()
        service._refreshing.clear()


# Match noise as in this synthetic data (alliance sd ~15-20); the 2026 settings assume ~63.
QUIET = ComponentParams(prior_var=4000.0, match_var=10.0, event_var=300.0, noise_var=400.0)


class DudTests(unittest.TestCase):
    def _run(self, params):
        # frc1 scores 150 but dies (0) one match in five; frc2 scores 150 every match.
        rng = random.Random(5)
        others = [f"frc{n}" for n in range(3, 15)]
        model = SeasonTeamModel({"teleop": 30.0, "auto": 0.0, "tower": 0.0}, params)
        for m in range(120):
            star = "frc1" if m % 2 == 0 else "frc2"
            mates = rng.sample(others, 5)
            red, blue = (star, mates[0], mates[1]), tuple(mates[2:])
            card = MatchCard(f"m{m}", "e0", m, "qm", 0, red, blue)
            model.predict(card)
            star_points = 0.0 if (star == "frc1" and m % 10 == 0) else 150.0
            red_total = star_points + 30.0 + 30.0 + rng.gauss(0, 15)
            blue_total = 90.0 + rng.gauss(0, 15)
            comp = lambda total: {"teleop_hub_points": total, "auto_hub_points": 0.0}  # noqa: E731
            model.observe(MatchResult(card, round(red_total), round(blue_total), red_total, blue_total, {}, comp(red_total), comp(blue_total)))
        return model

    def test_a_robot_that_breaks_down_gets_a_higher_dud_rate(self):
        params = TeamModelParams(teleop=QUIET, dud_base_rate=0.03)
        model = self._run(params)
        self.assertGreater(model.dud_rate("frc1"), 2 * model.dud_rate("frc2"))
        self.assertGreater(model.dud_rate("frc1"), 0.1)

    def test_the_robust_update_keeps_a_flaky_robots_ability(self):
        plain = self._run(TeamModelParams(teleop=QUIET, dud_base_rate=0.03))
        robust = self._run(TeamModelParams(teleop=QUIET, dud_base_rate=0.03, dud_robust=True))
        plain_mean = plain.belief("frc1", "e0")["teleop"].mean
        robust_mean = robust.belief("frc1", "e0")["teleop"].mean
        self.assertGreater(robust_mean, plain_mean)
        self.assertAlmostEqual(robust_mean, 150.0, delta=20.0)

    def test_predictions_allow_for_duds(self):
        model = self._run(TeamModelParams(teleop=QUIET, dud_base_rate=0.03, dud_predict=True))
        card = MatchCard("next", "e0", 999, "qm", 0, ("frc1", "frc3", "frc4"), ("frc2", "frc5", "frc6"))
        prediction = model.predict(card)
        # Same normal output, but frc1's breakdowns cost its alliance expected points and add spread.
        self.assertLess(prediction.red_mean, prediction.blue_mean)
        self.assertGreater(prediction.red_var, prediction.blue_var)


class SeasonPriorTests(unittest.TestCase):
    def test_last_seasons_strength_and_rookies_set_the_first_belief(self):
        import math

        params = TeamModelParams()
        model = SeasonTeamModel(
            {"teleop": 50.0, "auto": 10.0, "tower": 1.0}, params,
            team_multipliers={"frc254": math.exp(0.9 * 1.4), "frc9999": 0.6},
            team_var_multipliers={"frc254": 0.5},
        )
        card = MatchCard("m1", "e0", 1, "qm", 1, ("frc254", "frc9999", "frc1"), ("frc2", "frc3", "frc4"))
        model.predict(card)
        star, rookie, stranger = (model.belief(t, "e0")["teleop"] for t in ("frc254", "frc9999", "frc1"))
        self.assertAlmostEqual(star.mean, 50.0 * math.exp(1.26))
        self.assertAlmostEqual(star.var, params.teleop.prior_var * 0.5)
        self.assertAlmostEqual(rookie.mean, 30.0)
        self.assertEqual((stranger.mean, stranger.var), (50.0, params.teleop.prior_var))


class SeasonPriorMultiplierTests(DBTestCase):
    def test_multipliers_come_from_last_season_and_rookie_years(self):
        import math

        from app.db import models
        from app.services.ratings.team_model_service import season_prior_multipliers

        self.db.add_all([
            models.Team(team_key="frc254", team_number=254, rookie_year=1999),
            models.Team(team_key="frc9999", team_number=9999, rookie_year=2026),
            models.TeamPriorStrength(season=2025, team_key="frc254", points=110.0, z=1.4, matches=60, source="tba"),
        ])
        self.db.commit()
        multipliers, var = season_prior_multipliers(
            self.db, 2026, {"prior_beta": 0.9, "rookie_multiplier": 0.6, "prior_known_var_multiplier": 0.5},
        )
        self.assertAlmostEqual(multipliers["frc254"], math.exp(0.9 * 1.4))
        self.assertAlmostEqual(multipliers["frc9999"], 0.6)
        self.assertEqual(var, {"frc254": 0.5})
        # No beta and no rookie setting: everyone starts at the season prior.
        self.assertEqual(season_prior_multipliers(self.db, 2026, {}), ({}, {}))


class ContextTests(unittest.TestCase):
    def _season(self, params, *, foul_team=None, defender=None, offset=0.0, matches=150):
        rng = random.Random(9)
        teams = [f"frc{n}" for n in range(1, 13)]
        truth = {t: 40.0 + 5.0 * i for i, t in enumerate(teams)}
        model = SeasonTeamModel({"teleop": 70.0, "auto": 0.0, "tower": 0.0, "fouls": 2.0}, params)
        for m in range(matches):
            picked = rng.sample(teams, 6)
            red, blue = tuple(picked[:3]), tuple(picked[3:])
            card = MatchCard(f"m{m}", "e0", m, "qm", 0, red, blue)
            model.predict(card)
            comps = []
            for side, other in ((red, blue), (blue, red)):
                teleop = sum(truth[t] for t in side) + offset + rng.gauss(0, 10)
                if defender in other:
                    teleop -= 40.0
                fouls = 15.0 if foul_team in other else 0.0
                comps.append({"teleop_hub_points": teleop, "auto_hub_points": 0.0, "foul_points_received": fouls})
            model.observe(MatchResult(card, 0, 1, comps[0]["teleop_hub_points"], comps[1]["teleop_hub_points"], {}, comps[0], comps[1]))
        return model, truth

    def test_a_team_that_fouls_gives_its_opponents_points(self):
        params = TeamModelParams(teleop=QUIET, model_fouls=True)
        model, _truth = self._season(params, foul_team="frc5")
        self.assertGreater(model.side_belief("frc5", "fouls", "e0").mean, 10.0)
        self.assertLess(model.side_belief("frc6", "fouls", "e0").mean, 3.0)
        prediction = model.forecast("e0", ("frc1", "frc2", "frc3"), ("frc5", "frc7", "frc8"))
        self.assertGreater(prediction.red_fouls, prediction.blue_fouls + 8.0)

    def test_a_defender_is_credited_with_the_points_it_takes_away(self):
        params = TeamModelParams(teleop=QUIET, model_defense=True, defense=ComponentParams(prior_var=400.0, match_var=1.0, event_var=50.0, noise_var=400.0))
        model, truth = self._season(params, defender="frc5", matches=300)
        self.assertGreater(model.side_belief("frc5", "defense", "e0").mean, 20.0)
        # Its opponents aren't blamed for the points it took: their means stay near the truth.
        self.assertAlmostEqual(model.belief("frc9", "e0")["teleop"].mean, truth["frc9"], delta=12.0)

    def test_an_event_offset_absorbs_a_high_scoring_field(self):
        params = TeamModelParams(teleop=QUIET, event_offset_var=1600.0)
        model, truth = self._season(params, offset=60.0)
        block = model.blocks[("e0", "teleop")]
        # One event alone can't separate the field's level from its teams' (it's split by
        # their prior uncertainties); across events teams carry over and it can.
        self.assertGreater(float(block.mean[block.index["__event__e0"]]), 10.0)
        plain, _ = self._season(TeamModelParams(teleop=QUIET), offset=60.0)
        # With the offset, teams aren't credited the field's extra 20 points each.
        self.assertLess(model.belief("frc1", "e0")["teleop"].mean, plain.belief("frc1", "e0")["teleop"].mean)


class SeasonRunExtendTests(unittest.TestCase):
    # During an event every new result changes the season; the run is extended with the
    # new matches instead of replaying ~22k. It must equal a replay from the start.
    def _compute(self, results):
        from types import SimpleNamespace
        from unittest.mock import patch

        from app.services.ratings import team_model_service as service

        observed = []
        real_observe = SeasonTeamModel.observe

        def counting_observe(model, result):
            observed.append(result.card.match_key)
            return real_observe(model, result)

        with (
            patch.object(service, "load_season", return_value=SimpleNamespace(results=list(results))),
            patch.object(service, "season_prior_multipliers", return_value=({}, {})),
            patch.object(SeasonTeamModel, "observe", counting_observe),
        ):
            run = service._compute(unittest.mock.MagicMock(), 2026, (len(results),))
        return run, observed

    def _summary(self, run):
        return (
            run.before_match,
            run.played_total,
            run.played_here,
            run.changes,
            {key: run.model.team_points(*key) for key in run.played_here},
        )

    def setUp(self):
        from app.services.ratings import team_model_service as service

        self.service = service
        service.clear_cache()
        self.results = _season(events=3, per_event=30).results

    def tearDown(self):
        self.service.clear_cache()

    def test_new_matches_extend_the_run_and_match_a_full_replay(self):
        first, _ = self._compute(self.results[:-12])
        extended, observed = self._compute(self.results)
        self.assertEqual(observed, [r.card.match_key for r in self.results[-12:]])
        self.assertEqual(len(first.before_match), len(self.results) - 12)  # the cached run is not touched
        self.service.clear_cache()
        full, _ = self._compute(self.results)
        self.assertEqual(self._summary(extended), self._summary(full))

    def test_a_corrected_earlier_score_replays_from_the_start(self):
        self._compute(self.results)
        corrected = list(self.results)
        old = corrected[5]
        corrected[5] = MatchResult(old.card, old.red_score + 40, old.blue_score, old.red_points, old.blue_points, old.robots, old.red_components, old.blue_components)
        rerun, observed = self._compute(corrected)
        self.assertEqual(len(observed), len(corrected))
        self.service.clear_cache()
        full, _ = self._compute(corrected)
        self.assertEqual(self._summary(rerun), self._summary(full))


class LoadSeasonTests(DBTestCase):
    def test_a_match_without_stored_results_takes_its_score_and_lineup_from_findings(self):
        from app.db import models
        from app.services.ratings.replay import load_season

        db = self.db
        db.add(models.Event(event_key="2026tst", name="Test", year=2026))
        for n in range(1, 7):
            db.add(models.Team(team_key=f"frc{n}", team_number=n, nickname=str(n)))
        db.add(models.Match(match_key="2026tst_qm1", event_key="2026tst", comp_level="qm", set_number=1, match_number=1, time=1_772_000_000))
        db.add(models.AnalysisRun(id=1, match_key="2026tst_qm1", run_kind="official_truth", status="completed"))
        db.flush()
        for n in range(1, 7):
            alliance = "red" if n <= 3 else "blue"
            db.add(models.MatchTeam(match_key="2026tst_qm1", team_key=f"frc{n}", event_key="2026tst", alliance=alliance))
            db.add(models.TeamMatchFinding(
                analysis_run_id=1, match_key="2026tst_qm1", event_key="2026tst", team_key=f"frc{n}", alliance=alliance,
                source="tba_score_breakdown", fuel_scoring_rate=2.0, auto_contribution=4.0,
                summary={"red_score": 101, "blue_score": 77, "status": {"auto_allocation": "no_hub_fuel", "endgame_tower": "Level2", "hub_shift_counts": {"a": 1}}},
            ))
        db.commit()

        result = load_season(db, 2026).results[0]
        self.assertEqual((result.red_score, result.blue_score), (101, 77))
        self.assertEqual(sorted(result.card.red), ["frc1", "frc2", "frc3"])
        self.assertEqual(sorted(result.card.blue), ["frc4", "frc5", "frc6"])
        robot = result.robots["frc1"]
        self.assertEqual(robot.auto, 4.0)  # no_hub_fuel: the robot's own auto counts
        self.assertGreater(robot.climb, 0.0)  # Level2 read from the summary's status
