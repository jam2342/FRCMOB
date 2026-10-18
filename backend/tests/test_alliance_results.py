from __future__ import annotations

import unittest

from app.db import models
from app.services.events.ingest import ingest_event
from app.services.scoring.alliance_results import parse_alliance_results, upsert_alliance_results
from tests.conftest import DBTestCase

EVENT = "2026test"
RED = ["frc5181", "frc7769", "frc1690"]
BLUE = ["frc1", "frc2", "frc3"]


def _breakdown(*, hub_total: int, teleop: int, auto: int, foul_points: int, minor: int, major: int, tower: int = 0, rp: int = 0):
    # Shaped like TBA's 2026 payload (2026cmptx_sf9m1).
    return {
        "adjustPoints": 0,
        "autoTowerPoints": 0,
        "autoTowerRobot1": "None",
        "endGameTowerPoints": tower,
        "endGameTowerRobot1": "Level3" if tower else "None",
        "endGameTowerRobot2": "Level1" if tower else "None",
        "autoTowerRobot2": "Level1" if tower else "None",
        "energizedAchieved": hub_total >= 100,
        "superchargedAchieved": hub_total >= 360,
        "traversalAchieved": tower >= 50,
        "foulPoints": foul_points,
        "minorFoulCount": minor,
        "majorFoulCount": major,
        "hubScore": {
            "autoCount": auto, "autoPoints": auto,
            "teleopCount": teleop, "teleopPoints": teleop,
            "transitionPoints": 86, "shift1Points": 0, "shift2Points": 203, "shift3Points": 0, "shift4Points": 191,
            "endgamePoints": 178, "totalCount": hub_total, "totalPoints": hub_total,
        },
        "rp": rp,
        "totalPoints": hub_total + tower + foul_points,
        "totalTowerPoints": tower,
    }


def _match(number: int = 1, *, played: bool = True, winner: str = "red") -> dict:
    return {
        "key": f"{EVENT}_qm{number}",
        "comp_level": "qm",
        "set_number": 1,
        "match_number": number,
        "time": 1000 + number,
        "winning_alliance": winner if played else "",
        "alliances": {
            "red": {"team_keys": RED, "score": 831 if played else -1, "surrogate_team_keys": ["frc1690"], "dq_team_keys": []},
            "blue": {"team_keys": BLUE, "score": 513 if played else -1, "surrogate_team_keys": [], "dq_team_keys": ["FRC3"]},
        },
        "score_breakdown": {
            "red": _breakdown(hub_total=826, teleop=658, auto=168, foul_points=5, minor=0, major=0, rp=4),
            "blue": _breakdown(hub_total=513, teleop=400, auto=113, foul_points=0, minor=1, major=0),
        } if played else None,
    }


class ParseAllianceResultsTests(unittest.TestCase):
    def test_reads_scores_fouls_rp_and_flags(self):
        red, blue = parse_alliance_results(_match(), 2026)
        self.assertEqual(red["total_score"], 831)
        # Foul points received from blue's committed foul; red committed none.
        self.assertEqual((red["foul_points_received"], red["minor_fouls_committed"]), (5, 0))
        self.assertEqual((blue["foul_points_received"], blue["minor_fouls_committed"]), (0, 1))
        self.assertEqual(red["score_without_fouls"], 826)
        self.assertEqual((red["result"], blue["result"]), ("win", "loss"))
        self.assertEqual(red["ranking_points"], 4)
        self.assertEqual(red["surrogate_team_keys"], ["frc1690"])
        self.assertEqual(blue["dq_team_keys"], ["frc3"])
        components = red["components"]
        self.assertEqual((components["auto_hub_points"], components["teleop_hub_points"], components["hub_fuel_count"]), (168, 658, 826))
        self.assertEqual(components["shift2_hub_points"], 203)
        self.assertTrue(components["energized_rp"] and components["supercharged_rp"])
        self.assertEqual(components["robot_endgame_tower_points"], [0.0, 0.0, 0.0])
        self.assertFalse(components["traversal_rp"])

    def test_unplayed_match_has_no_rows_and_a_tie_has_no_winner(self):
        self.assertEqual(parse_alliance_results(_match(played=False), 2026), [])
        tie = _match(winner="")
        self.assertEqual({row["result"] for row in parse_alliance_results(tie, 2026)}, {"tie"})

    def test_season_without_a_parser_keeps_the_universal_fields(self):
        red, _blue = parse_alliance_results(_match(), 2025)
        self.assertEqual(red["components"], {})
        self.assertEqual(red["foul_points_received"], 5)
        self.assertEqual(red["raw_breakdown"]["hubScore"]["teleopPoints"], 658)


class _FakeTBA:
    def __init__(self, matches):
        self.matches = matches

    def event(self, key):
        return {"key": key, "name": "Test", "year": 2026}

    def event_teams(self, _key):
        return [{"key": key, "team_number": int(key[3:])} for key in RED + BLUE]

    def event_matches(self, _key):
        return self.matches

    def event_coprs(self, _key):
        return {}

    def event_oprs(self, _key):
        return {}


class StoreAllianceResultsTests(DBTestCase):
    def test_ingest_writes_both_alliances_and_reruns_replace_them(self):
        tba = _FakeTBA([_match(1), _match(2, played=False)])
        ingest_event(EVENT, tba, self.db, run_post_compute=False)
        rows = self.db.query(models.MatchAllianceResult).order_by(models.MatchAllianceResult.alliance).all()
        self.assertEqual([(row.match_key, row.alliance) for row in rows], [(f"{EVENT}_qm1", "blue"), (f"{EVENT}_qm1", "red")])
        self.assertEqual(rows[1].team_keys, RED)

        # A rerun after a score correction rewrites the row instead of adding one.
        corrected = _match(1)
        corrected["alliances"]["red"]["score"] = 836
        upsert_alliance_results(self.db, event_key=EVENT, season_year=2026, matches=[corrected])
        self.db.commit()
        rows = self.db.query(models.MatchAllianceResult).filter_by(alliance="red").all()
        self.assertEqual([row.total_score for row in rows], [836])

    def test_results_for_matches_not_stored_are_skipped(self):
        self.db.add(models.Event(event_key=EVENT, name="Test", year=2026))
        self.db.commit()
        self.assertEqual(upsert_alliance_results(self.db, event_key=EVENT, season_year=2026, matches=[_match(1)]), 0)


if __name__ == "__main__":
    unittest.main()


class RobotTowerTests(unittest.TestCase):
    def test_each_robot_keeps_its_own_tower_points(self):
        match = _match()
        match["score_breakdown"]["red"] = _breakdown(hub_total=300, teleop=250, auto=50, foul_points=0, minor=0, major=0, tower=40)
        components = parse_alliance_results(match, 2026)[0]["components"]
        self.assertEqual(components["robot_endgame_tower_points"], [30.0, 10.0, 0.0])
        self.assertEqual(components["robot_auto_tower_points"], [0.0, 15.0, 0.0])
