from __future__ import annotations

import unittest

from app.services.simulation.event_sim import QualMatch, Standing, select_alliances, simulate

TEAMS = [f"frc{n}" for n in range(1, 31)]
# Strength rises with the number: frc30 is the best robot.
STRENGTH = {t: float(i) for i, t in enumerate(TEAMS, start=1)}


class SelectionTests(unittest.TestCase):
    def test_serpentine_picks_and_a_picked_captain_is_replaced(self):
        # Ranking: frc1..frc30 in order; captain 1 (frc1) picks the strongest robot, frc30.
        order = list(TEAMS)
        # Make frc5 (a captain) the strongest so captain 1 picks it.
        strength = dict(STRENGTH, frc5=100.0)
        alliances = select_alliances(order, strength)
        self.assertEqual(alliances[0][:2], ["frc1", "frc5"])
        captains = [a[0] for a in alliances]
        # frc5 left the captains; frc9 (next ranked) moved up as the 8th captain.
        self.assertNotIn("frc5", captains)
        self.assertEqual(captains, ["frc1", "frc2", "frc3", "frc4", "frc6", "frc7", "frc8", "frc9"])
        # Round 2 runs 8 -> 1: the 8th alliance gets the best robot left after round 1.
        round1 = {a[1] for a in alliances}
        best_left = max((t for t in TEAMS if t not in round1 and t not in captains), key=lambda t: strength[t])
        self.assertEqual(alliances[7][2], best_left)
        self.assertTrue(all(len(a) == 3 for a in alliances))


class SimulateTests(unittest.TestCase):
    def _standings(self):
        # 10 matches each; RP rises with the number, so frc30 leads.
        return {t: Standing(ranking_points=float(i), matches=10, points=100.0) for i, t in enumerate(TEAMS, start=1)}

    def test_finished_quals_make_the_top_eight_certain(self):
        result = simulate(TEAMS, self._standings(), [], STRENGTH, lambda a, b: 0.5, runs=200)
        top8 = {t for t in TEAMS if result[t]["top8"] == 1.0}
        self.assertEqual(top8, {f"frc{n}" for n in range(23, 31)})
        self.assertAlmostEqual(sum(r["top8"] for r in result.values()), 8.0)
        self.assertAlmostEqual(sum(r["win_event"] for r in result.values()), 3.0)

    def test_the_stronger_alliance_wins_when_playoffs_are_lopsided(self):
        def stronger(a, b):
            return 1.0 if sum(STRENGTH[t] for t in a) > sum(STRENGTH[t] for t in b) else 0.0

        result = simulate(TEAMS, self._standings(), [], STRENGTH, stronger, runs=50)
        champion = {t for t, r in result.items() if r["win_event"] == 1.0}
        # Captain 1 (frc30) picks the strongest left (frc29) and round 2 is weaker: it wins.
        self.assertIn("frc30", champion)
        self.assertEqual(len(champion), 3)

    def test_remaining_quals_move_the_standings(self):
        standings = {t: Standing(ranking_points=10.0, matches=5, points=100.0) for t in TEAMS}
        # frc1-3 win every remaining match with every bonus: they rise to the top.
        remaining = [
            QualMatch(("frc1", "frc2", "frc3"), (f"frc{4 + i}", f"frc{5 + i}", f"frc{6 + i}"), 1.0,
                      {"energized": 1.0}, {"energized": 0.0}, 200.0, 50.0)
            for i in range(0, 18, 3)
        ]
        result = simulate(TEAMS, standings, remaining, STRENGTH, lambda a, b: 0.5, runs=100)
        for team in ("frc1", "frc2", "frc3"):
            self.assertEqual(result[team]["top8"], 1.0)
            self.assertLessEqual(result[team]["expected_rank"], 3.0)
            self.assertEqual(result[team]["expected_rp"], 10.0 + 6 * 4.0)


if __name__ == "__main__":
    unittest.main()
