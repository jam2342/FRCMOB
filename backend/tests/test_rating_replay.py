from __future__ import annotations

import random
import unittest

from app.services.ratings.replay import (
    FuelOnly,
    MatchCard,
    MatchResult,
    Prediction,
    Season,
    StrengthV15,
    calibration_table,
    outcome_scores,
    run_replay,
    split_holdout,
    typical_robot,
)
from app.services.ratings.strength import MatchPoints, StrengthParams

TEAMS = [f"frc{n}" for n in range(1, 13)]
STRENGTH = {team: 10.0 * (i + 1) for i, team in enumerate(TEAMS)}  # frc1 = 10 ... frc12 = 120


def _season(events: int = 5, matches_per_event: int = 24, noise: float = 0.0, seed: int = 7) -> Season:
    rng = random.Random(seed)
    results = []
    t = 0
    for e in range(events):
        event_key = f"2026e{e}"
        start = t + 1
        for m in range(matches_per_event):
            t += 1
            picked = rng.sample(TEAMS, 6)
            red, blue = tuple(picked[:3]), tuple(picked[3:])
            robots = {team: MatchPoints(f"{event_key}_qm{m}", t, STRENGTH[team] + rng.gauss(0, noise), 0.0, 0.0) for team in picked}
            red_points = sum(robots[x].teleop for x in red)
            blue_points = sum(robots[x].teleop for x in blue)
            if round(red_points) == round(blue_points):
                red_points += 1
            card = MatchCard(f"{event_key}_qm{m}", event_key, t, "qm", start, red, blue)
            results.append(MatchResult(card, round(red_points), round(blue_points), red_points, blue_points, robots))
    holdout = split_holdout(results)
    training = {r.card.event_key for r in results} - holdout
    return Season(2026, results, holdout, typical_robot(results, training))


class _Oracle:
    name = "oracle"

    def __init__(self):
        self.order: list[tuple[str, str]] = []

    def predict(self, card):
        self.order.append(("predict", card.match_key))
        return Prediction(sum(STRENGTH[t] for t in card.red), sum(STRENGTH[t] for t in card.blue))

    def observe(self, result):
        self.order.append(("observe", result.card.match_key))

    def team_points(self, team_key, event_key):
        return STRENGTH[team_key]


class _Coin:
    name = "coin"

    def predict(self, card):
        return Prediction(0.0, 0.0)

    def observe(self, result):
        pass

    def team_points(self, team_key, event_key):
        return 0.0


class ReplayHarnessTests(unittest.TestCase):
    def test_a_prediction_never_comes_after_its_result(self):
        season = _season()
        oracle = _Oracle()
        run_replay(season, oracle)
        # The card carries no scores, and every match is predicted before it is observed.
        self.assertFalse(any(hasattr(season.results[0].card, field) for field in ("red_score", "red_points", "robots")))
        self.assertEqual(oracle.order[0::2], [("predict", r.card.match_key) for r in season.results])
        self.assertEqual(oracle.order[1::2], [("observe", r.card.match_key) for r in season.results])

    def test_perfect_knowledge_scores_perfectly_and_a_coin_scores_half(self):
        season = _season()
        perfect = run_replay(season, _Oracle())
        self.assertEqual(perfect["matches"]["all"]["accuracy"], 1.0)
        self.assertAlmostEqual(perfect["alliance_points_error"]["mean_abs"], 0.0, places=1)
        self.assertEqual(perfect["next_event"]["spearman"], 1.0)
        coin = run_replay(season, _Coin())
        self.assertEqual(coin["matches"]["all"]["accuracy"], 0.5)
        self.assertAlmostEqual(coin["matches"]["all"]["brier"], 0.25, places=3)

    def test_slices_count_what_each_team_had_played(self):
        report = run_replay(_season(events=5), _Oracle())
        held_out = report["matches"]["all"]["n"]
        # The held-out event is every team's 5th: nobody is at a first event, and the
        # first match of the event has nobody with a match here.
        self.assertEqual(report["matches"]["a team at its first event"]["n"], 0)
        self.assertGreaterEqual(report["matches"]["nobody has played here yet"]["n"], 1)
        self.assertEqual(sum(row["n"] for row in report["calibration"]), held_out)

    def test_the_shipped_rating_learns_and_beats_fuel_with_nothing_to_learn_from(self):
        season = _season(events=6, noise=15.0)
        v15 = run_replay(season, StrengthV15(season.typical, StrengthParams()))
        coin = run_replay(season, _Coin())
        self.assertGreater(v15["matches"]["all"]["accuracy"], 0.75)
        self.assertLess(v15["matches"]["all"]["brier"], coin["matches"]["all"]["brier"])
        self.assertGreater(v15["next_event"]["spearman"], 0.9)
        fuel = run_replay(season, FuelOnly(season.typical))
        self.assertGreater(fuel["matches"]["all"]["accuracy"], 0.7)

    def test_scores_and_calibration(self):
        scores = outcome_scores([0.9, 0.2, 0.5], [1.0, 1.0, 0.0])
        self.assertEqual(scores["accuracy"], round(1.5 / 3, 4))
        self.assertAlmostEqual(scores["brier"], (0.01 + 0.64 + 0.25) / 3, places=4)
        table = calibration_table([0.05, 0.95, 0.97], [0.0, 1.0, 0.0])
        self.assertEqual([(row["n"], row["red_won"]) for row in table], [(1, 0.0), (2, 0.5)])


if __name__ == "__main__":
    unittest.main()
