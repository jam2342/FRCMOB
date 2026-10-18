from __future__ import annotations

import json
import re
import unittest
from unittest import mock

from app.api import routes_matches
from app.core.config import settings
from app.services.events import first_live_results as flr

# Washington Girl's Generation, Upper Bracket R2 M6 (2026wagg_sf6m1), as both APIs
# returned it on 2026-10-18. FRC Events had it 7 s after the field posted it, TBA 162 s.
NOW = 1792357700
FIRST_SF6 = {
    "isReplay": False,
    "description": "Match 6 (R2)",
    "matchNumber": 6,
    "scoreRedFinal": 186,
    "scoreBlueFinal": 116,
    "actualStartTime": "2026-10-18T14:07:39.467",
    "tournamentLevel": "Playoff",
    "postResultTime": "2026-10-18T14:12:08.163",
    "teams": [
        {"teamNumber": 5937, "station": "Red1", "dq": False},
        {"teamNumber": 3786, "station": "Red2", "dq": False},
        {"teamNumber": 9036, "station": "Red3", "dq": False},
        {"teamNumber": 1983, "station": "Blue1", "dq": False},
        {"teamNumber": 1899, "station": "Blue2", "dq": False},
        {"teamNumber": 949, "station": "Blue3", "dq": False},
    ],
}


def _tba(key, red, blue, *, score=None, predicted=NOW - 60):
    m = re.search(r"_(qm)(\d+)$", key) or re.search(r"_(ef|qf|sf|f)(\d+)m(\d+)$", key)
    level = m.group(1)
    set_number, match_number = (1, int(m.group(2))) if level == "qm" else (int(m.group(2)), int(m.group(3)))
    return {
        "key": key,
        "comp_level": level,
        "set_number": set_number,
        "match_number": match_number,
        "alliances": {
            "red": {"team_keys": [f"frc{t}" for t in red], "score": score[0] if score else -1},
            "blue": {"team_keys": [f"frc{t}" for t in blue], "score": score[1] if score else -1},
        },
        "winning_alliance": ("red" if score[0] > score[1] else "blue") if score else "",
        "actual_time": None,
        "predicted_time": predicted,
        "time": predicted,
    }


def _first(number, red, blue, score, level="Playoff"):
    teams = [{"teamNumber": t, "station": f"Red{i}"} for i, t in enumerate(red, 1)]
    teams += [{"teamNumber": t, "station": f"Blue{i}"} for i, t in enumerate(blue, 1)]
    return {"matchNumber": number, "scoreRedFinal": score[0], "scoreBlueFinal": score[1],
            "tournamentLevel": level, "teams": teams}


class _FakeCache:
    def __init__(self):
        self.values = {}

    def get(self, key):  # noqa: ANN001
        return self.values.get(key)

    def set(self, key, value, ttl=3600):  # noqa: ANN001
        self.values[key] = value
        return True

    def delete(self, key):  # noqa: ANN001
        self.values.pop(key, None)
        return True


class _FakeFirst:
    def __init__(self, matches_by_level, last_modified="Sun, 18 Oct 2026 21:12:10 GMT"):
        self.matches_by_level = matches_by_level
        self.last_modified = last_modified
        self.calls = []

    def event_matches(self, season, code, *, tournament_level, if_modified_since=None, timeout, max_retries):
        self.calls.append((season, code, tournament_level, if_modified_since))
        if if_modified_since == self.last_modified:
            return {}, self.last_modified, True
        return {"Matches": self.matches_by_level.get(tournament_level, [])}, self.last_modified, False


class FirstLiveResultsTests(unittest.TestCase):
    def setUp(self):
        self.cache = _FakeCache()
        self.tba = mock.Mock()
        self.tba.event.return_value = {"key": "2026wagg", "year": 2026, "first_event_code": "wamap",
                                       "timezone": "America/Los_Angeles"}
        previous = settings.first_live_results_enabled
        settings.first_live_results_enabled = True
        self.addCleanup(setattr, settings, "first_live_results_enabled", previous)
        for target in (mock.patch.object(flr, "get_cache", return_value=self.cache),
                       mock.patch.object(routes_matches, "get_cache", return_value=self.cache),
                       mock.patch.object(flr.time, "time", return_value=NOW),
                       # Background fetches run inline, so each test sees their result at once.
                       mock.patch.object(flr, "_spawn", side_effect=lambda fn, name: fn())):
            target.start()
            self.addCleanup(target.stop)

    def _use_first(self, fake):
        patcher = mock.patch.object(flr, "_first_client", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fill(self, tba_matches):
        # The real caller passes TBA's own results alongside.
        results = {m["key"]: routes_matches._match_result_snapshot(m) for m in tba_matches}
        return flr.first_results_for_waiting_matches(self.tba, "2026wagg", tba_matches, results, now=NOW)

    def test_real_washington_result_fills_the_waiting_tba_match(self):
        self._use_first(_FakeFirst({"Playoff": [FIRST_SF6]}))
        filled = self._fill([_tba("2026wagg_sf6m1", [5937, 3786, 9036], [1983, 1899, 949])])
        payload = filled["2026wagg_sf6m1"]
        self.assertEqual(payload["alliances"], {"red": {"score": 186}, "blue": {"score": 116}})
        self.assertEqual(payload["winning_alliance"], "red")
        # FRC Events' local Pacific time lands on TBA's own actual_time for this match.
        self.assertEqual(payload["actual_time"], 1792357659)

    def test_schedule_results_carry_the_first_events_score(self):
        self._use_first(_FakeFirst({"Playoff": [FIRST_SF6]}))
        self.tba.event_matches.return_value = [_tba("2026wagg_sf6m1", [5937, 3786, 9036], [1983, 1899, 949])]
        results = routes_matches._cached_live_results_for_event(self.tba, "2026wagg")
        self.assertEqual(results["2026wagg_sf6m1"]["red_score"], 186)
        self.assertEqual(results["2026wagg_sf6m1"]["winner_alliance"], "red")
        self.assertTrue(results["2026wagg_sf6m1"]["is_completed"])

    def test_tba_result_is_never_replaced(self):
        fake = _FakeFirst({"Playoff": [_first(6, [5937, 3786, 9036], [1983, 1899, 949], (1, 2))]})
        self._use_first(fake)
        done = _tba("2026wagg_sf6m1", [5937, 3786, 9036], [1983, 1899, 949], score=(186, 116))
        self.tba.event_matches.return_value = [done]
        results = routes_matches._cached_live_results_for_event(self.tba, "2026wagg")
        self.assertEqual(results["2026wagg_sf6m1"]["red_score"], 186)
        # Nothing was waiting, so FRC Events wasn't even asked.
        self.assertEqual(fake.calls, [])

    def test_rematch_goes_to_the_right_match(self):
        red, blue = [1, 2, 3], [4, 5, 6]
        # FRC Events' list skips the earlier meeting (match 2): numbering still places 14 and 15.
        self._use_first(_FakeFirst({"Playoff": [_first(14, red, blue, (100, 90)), _first(15, red, blue, (80, 95))]}))
        filled = self._fill([
            _tba("2026wagg_sf2m1", red, blue, score=(50, 60)),  # met earlier in the bracket
            _tba("2026wagg_f1m1", red, blue),
            _tba("2026wagg_f1m2", red, blue),
        ])
        self.assertEqual(filled["2026wagg_f1m1"]["alliances"]["red"]["score"], 100)
        self.assertEqual(filled["2026wagg_f1m2"]["alliances"]["blue"]["score"], 95)
        self.assertNotIn("2026wagg_sf2m1", filled)

    def test_other_bracket_formats_pair_in_playing_order(self):
        red, blue = [1, 2, 3], [4, 5, 6]
        # A 4-alliance event: TBA keys don't follow the 8-alliance numbering.
        self._use_first(_FakeFirst({"Playoff": [_first(1, red, blue, (50, 60)), _first(2, red, blue, (70, 65))]}))
        filled = self._fill([_tba("2026wagg_f1m1", red, blue, score=(50, 60)), _tba("2026wagg_f1m2", red, blue)])
        self.assertEqual(filled, {"2026wagg_f1m2": mock.ANY})
        self.assertEqual(filled["2026wagg_f1m2"]["alliances"]["red"]["score"], 70)

    def test_rematch_alignment_counts_matches_tba_already_has(self):
        red, blue = [1, 2, 3], [4, 5, 6]
        self._use_first(_FakeFirst({"Playoff": [_first(2, red, blue, (50, 60)), _first(14, red, blue, (100, 90))]}))
        filled = self._fill([_tba("2026wagg_sf2m1", red, blue, score=(50, 60)), _tba("2026wagg_f1m1", red, blue)])
        self.assertEqual(filled["2026wagg_f1m1"]["alliances"]["red"]["score"], 100)

    def test_off_season_b_teams_still_pair(self):
        # Tidal Tumble SF7 (2026catt_sf7m1) on 2026-10-18: TBA says frc973B and frc2659B,
        # FRC Events gives the field's placeholders 9973 and 9979 in those stations.
        first = _first(7, [4414, 9973, 254], [8732, 9979, 9084], (631, 279))
        self._use_first(_FakeFirst({"Playoff": [first]}))
        tba_match = _tba("2026wagg_sf7m1", [4414, "973B", 254], [8732, "2659B", 9084])
        filled = self._fill([tba_match])
        self.assertEqual(filled["2026wagg_sf7m1"]["alliances"]["red"]["score"], 631)

    def test_backup_robot_rotation_still_pairs(self):
        # Tidal Tumble SF8 on 2026-10-18: TBA listed 3925 until its result showed 599 had
        # played (both on alliance 3), and the robots sat in different stations.
        first = _first(8, [5012, 7415, 4322], [599, 973, 8005], (358, 358))
        self._use_first(_FakeFirst({"Playoff": [first]}))
        filled = self._fill([_tba("2026wagg_sf8m1", [5012, 7415, 4322], [973, 3925, 8005])])
        self.assertEqual(filled["2026wagg_sf8m1"]["alliances"]["blue"]["score"], 358)
        self.assertEqual(filled["2026wagg_sf8m1"]["winning_alliance"], "")

    def test_different_alliances_under_the_usual_number_are_refused(self):
        first = _first(8, [5012, 1, 2], [3, 4, 8005], (100, 90))
        self._use_first(_FakeFirst({"Playoff": [first]}))
        self.assertEqual(self._fill([_tba("2026wagg_sf8m1", [5012, 7415, 4322], [973, 3925, 8005])]), {})

    def test_robot_on_the_other_colour_is_refused(self):
        first = _first(8, [5012, 7415, 973], [4322, 3925, 8005], (100, 90))
        self._use_first(_FakeFirst({"Playoff": [first]}))
        self.assertEqual(self._fill([_tba("2026wagg_sf8m1", [5012, 7415, 4322], [973, 3925, 8005])]), {})

    def test_swapped_or_different_teams_are_left_alone(self):
        self._use_first(_FakeFirst({"Qualification": [
            _first(3, [4, 5, 6], [1, 2, 3], (10, 20), "Qualification"),  # colours swapped
            _first(4, [7, 8, 9], [1, 2, 3], (10, 20), "Qualification"),
        ]}))
        self.assertEqual(self._fill([_tba("2026wagg_qm3", [1, 2, 3], [4, 5, 6]), _tba("2026wagg_qm4", [7, 8, 10], [1, 2, 3])]), {})

    def test_only_asks_while_a_match_should_be_finishing(self):
        fake = _FakeFirst({})
        self._use_first(fake)
        self._fill([_tba("2026wagg_qm9", [1, 2, 3], [4, 5, 6], predicted=NOW + 3600)])  # later today
        self._fill([_tba("2026wagg_qm9", [1, 2, 3], [4, 5, 6], predicted=NOW - 5 * 3600)])  # long gone
        self.assertEqual(fake.calls, [])
        self._fill([_tba("2026wagg_qm9", [1, 2, 3], [4, 5, 6], predicted=NOW - 200)])
        self.assertEqual([c[2] for c in fake.calls], ["Qualification"])

    def test_repeat_checks_send_if_modified_since_and_reuse_the_saved_answer(self):
        fake = _FakeFirst({"Playoff": [FIRST_SF6]})
        self._use_first(fake)
        waiting = [_tba("2026wagg_sf6m1", [5937, 3786, 9036], [1983, 1899, 949])]
        self._fill(waiting)
        self._fill(waiting)
        self.assertEqual(len(fake.calls), 1)  # still fresh: no second request
        with mock.patch.object(flr.time, "time", return_value=NOW + 10):
            filled = self._fill(waiting)
        self.assertIsNone(fake.calls[0][3])
        self.assertEqual(fake.calls[1][3], fake.last_modified)
        self.assertIn("2026wagg_sf6m1", filled)

    def test_event_unknown_to_first_events_is_remembered(self):
        self.tba.event.return_value = {"key": "2026cacg", "year": 2026, "first_event_code": None}
        fake = _FakeFirst({})
        self._use_first(fake)
        waiting = [_tba("2026wagg_qm1", [1, 2, 3], [4, 5, 6])]
        self.assertEqual(self._fill(waiting), {})
        self.assertEqual(self._fill(waiting), {})
        self.assertEqual(self.tba.event.call_count, 1)
        self.assertEqual(fake.calls, [])

    def test_first_events_failure_keeps_tba_results(self):
        class _Down:
            def event_matches(self, *args, **kwargs):
                raise RuntimeError("FIRST API returned 503")

        down = _Down()
        down.event_matches = mock.Mock(side_effect=RuntimeError("FIRST API returned 503"))
        self._use_first(down)
        self.tba.event_matches.return_value = [
            _tba("2026wagg_qm1", [1, 2, 3], [4, 5, 6], score=(10, 20)),
            _tba("2026wagg_qm2", [1, 2, 3], [7, 8, 9]),
        ]
        results = routes_matches._cached_live_results_for_event(self.tba, "2026wagg")
        self.assertEqual(results["2026wagg_qm1"]["blue_score"], 20)
        self.assertFalse(results["2026wagg_qm2"]["is_completed"])
        down.event_matches.assert_called_once()

    def test_unexpected_overlay_error_keeps_tba_results(self):
        self.tba.event_matches.return_value = [_tba("2026wagg_qm1", [1, 2, 3], [4, 5, 6], score=(10, 20))]
        with mock.patch.object(routes_matches, "first_results_for_waiting_matches", side_effect=KeyError("boom")):
            results = routes_matches._cached_live_results_for_event(self.tba, "2026wagg")
        self.assertEqual(results["2026wagg_qm1"]["red_score"], 10)

    def test_switched_off_does_nothing(self):
        settings.first_live_results_enabled = False
        fake = _FakeFirst({"Playoff": [FIRST_SF6]})
        self._use_first(fake)
        self.assertEqual(self._fill([_tba("2026wagg_sf6m1", [5937, 3786, 9036], [1983, 1899, 949])]), {})
        self.assertEqual(fake.calls, [])

    def test_page_never_waits_on_first_events(self):
        # A real background thread: the first answer comes from the store, which is empty.
        started = []
        with mock.patch.object(flr, "_spawn", side_effect=lambda fn, name: started.append(name)):
            self._use_first(_FakeFirst({"Playoff": [FIRST_SF6]}))
            filled = self._fill([_tba("2026wagg_sf6m1", [5937, 3786, 9036], [1983, 1899, 949])])
        self.assertEqual(filled, {})
        self.assertEqual(started, ["first-live-wamap-playoff"])

    def test_saved_answer_is_json(self):
        self._use_first(_FakeFirst({"Playoff": [FIRST_SF6]}))
        self._fill([_tba("2026wagg_sf6m1", [5937, 3786, 9036], [1983, 1899, 949])])
        saved = json.loads(self.cache.values["first:live_results:v1:2026:wamap:Playoff"])
        self.assertEqual(saved["matches"][0]["matchNumber"], 6)


if __name__ == "__main__":
    unittest.main()
