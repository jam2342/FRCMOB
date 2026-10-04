from __future__ import annotations

from unittest.mock import patch

from app.db import models
from app.services.scoring import official_team_stats as stats_module
from app.services.scoring.official_team_stats import official_team_stats
from tests.conftest import DBTestCase


class _MemoryCache:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ttl=3600):
        self.values[key] = value
        return True


class _FakeTBA:
    def __init__(self, events, coprs):
        self._events = events
        self._coprs = coprs
        self.copr_calls: list[str] = []

    def team_events(self, team_key, year):
        return self._events

    def event_coprs(self, event_key):
        self.copr_calls.append(event_key)
        return self._coprs.get(event_key, {})


def _coprs(total, auto_fuel, teleop, auto_points, team="frc254"):
    return {
        "Hub Total Fuel Count": {team: total, "frc1": 10.0},
        "Hub Auto Fuel Count": {team: auto_fuel},
        "Hub Teleop Fuel Count": {team: teleop},
        "totalAutoPoints": {team: auto_points},
        "rp": {team: 3.0},
    }


class OfficialTeamStatsTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self.cache = _MemoryCache()
        patcher = patch.object(stats_module, "get_cache", return_value=self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db.add(models.Team(team_key="frc254", team_number=254, nickname="Cheesy Poofs"))

    def _add_matches(self, event_key, count, *, climbs=(), source="tba_score_breakdown"):
        if self.db.get(models.Event, event_key) is None:
            self.db.add(models.Event(event_key=event_key, name=event_key, year=2026))
        for number in range(1, count + 1):
            match_key = f"{event_key}_qm{number}"
            if self.db.get(models.Match, match_key) is None:
                self.db.add(
                    models.Match(match_key=match_key, event_key=event_key, comp_level="qm", set_number=1, match_number=number)
                )
            run = models.AnalysisRun(match_key=match_key, run_kind="official_truth", status="completed")
            self.db.add(run)
            self.db.flush()
            level = climbs[number - 1] if number <= len(climbs) else "None"
            self.db.add(
                models.TeamMatchFinding(
                    analysis_run_id=run.id,
                    match_key=match_key,
                    event_key=event_key,
                    team_key="frc254",
                    alliance="red",
                    source=source,
                    climb_success_prob=0.0 if level == "None" else 1.0,
                    summary={"status": {"endgame_tower": level}},
                )
            )
        self.db.commit()

    def test_fuel_and_auto_are_match_weighted_copr_and_climb_is_the_record(self):
        self._add_matches("2026casj", 10, climbs=("Level1", "Level3"))
        self._add_matches("2026cur", 30)
        tba = _FakeTBA(
            events=[
                {"key": "2026casj", "event_type": 0, "end_date": "2026-03-10"},
                {"key": "2026cur", "event_type": 3, "end_date": "2026-04-20"},
            ],
            coprs={"2026casj": _coprs(300.0, 60.0, 240.0, 61.0), "2026cur": _coprs(340.0, 80.0, 260.0, 80.0)},
        )

        result = official_team_stats(self.db, team_key="frc254", season_year=2026, tba=tba)

        self.assertTrue(result["available"])
        self.assertEqual(result["matches"], 40)
        self.assertEqual(result["copr_matches"], 40)
        # Teleop (240*10 + 260*30) / 40 = 255 fuel over 99 active-hub seconds = 154.5/min,
        # the same unit as the official ingest's fuel_scoring_rate.
        self.assertEqual(result["fuel_per_match"], 330.0)
        self.assertEqual(result["fuel_per_active_minute"], 154.5)
        self.assertEqual(result["auto_points_per_match"], 75.2)
        self.assertEqual(result["climb"]["climbs"], 2)
        self.assertEqual(result["climb"]["rate"], 0.05)
        capability = result["climb"]["level_capability"]
        self.assertEqual(capability["best_level_label"], "Level 3")
        self.assertEqual(capability["level_counts"], {"level1": 1, "level2": 0, "level3": 1})

    def test_offseason_events_and_duplicate_rows_do_not_count(self):
        self._add_matches("2026casj", 4)
        self._add_matches("2026casj", 4, source="tba_score_breakdown_backfill")
        self._add_matches("2026cc", 6)
        tba = _FakeTBA(
            events=[
                {"key": "2026casj", "event_type": 0, "end_date": "2026-03-10"},
                {"key": "2026cc", "event_type": 99, "end_date": "2026-10-01"},
            ],
            coprs={"2026casj": _coprs(200.0, 40.0, 160.0, 40.0), "2026cc": _coprs(900.0, 99.0, 800.0, 99.0)},
        )

        result = official_team_stats(self.db, team_key="frc254", season_year=2026, tba=tba)

        self.assertEqual(result["matches"], 4)
        self.assertEqual([event["event_key"] for event in result["events"]], ["2026casj"])
        self.assertEqual(result["fuel_per_match"], 200.0)
        self.assertEqual(tba.copr_calls, ["2026casj"])

    def test_negative_copr_reads_as_zero_and_coprs_are_cached(self):
        self._add_matches("2026casj", 5)
        tba = _FakeTBA(
            events=[{"key": "2026casj", "event_type": 0, "end_date": "2026-03-10"}],
            coprs={"2026casj": _coprs(-3.5, -1.0, -2.5, -1.0)},
        )

        first = official_team_stats(self.db, team_key="frc254", season_year=2026, tba=tba)
        second = official_team_stats(self.db, team_key="frc254", season_year=2026, tba=tba)

        self.assertEqual(first["fuel_per_match"], 0.0)
        self.assertEqual(second["auto_points_per_match"], 0.0)
        self.assertEqual(tba.copr_calls, ["2026casj"])

    def test_without_tba_the_climb_record_still_comes_through(self):
        self._add_matches("2026casj", 8, climbs=("Level2",))

        result = official_team_stats(self.db, team_key="frc254", season_year=2026, tba=None)

        self.assertTrue(result["available"])
        self.assertFalse(result["events_verified_official"])
        self.assertIsNone(result["fuel_per_match"])
        self.assertEqual(result["climb"]["climbs"], 1)
        self.assertEqual(result["climb"]["level_capability"]["best_level"], "level2")

    def test_team_with_no_official_matches_is_unavailable(self):
        result = official_team_stats(self.db, team_key="frc254", season_year=2026, tba=None)

        self.assertFalse(result["available"])
        self.assertIsNone(result["climb"]["rate"])
