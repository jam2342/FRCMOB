from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.api import routes_teams


def _fake_rating_row() -> SimpleNamespace:
    return SimpleNamespace(
        rating_0_100=61.2,
        confidence_0_1=0.48,
        robot_level_0_100=58.1,
        driver_skill_0_100=55.0,
        results_anchor=52.0,
        throughput=60.0,
        shift_productivity=59.0,
        capacity_utilization=57.0,
        endgame=56.0,
        consistency=54.0,
        details_json={"subscores": {"auto_contribution": 62.0, "anti_defense": 51.0}},
        pros_json=[{"label": "Strong auto"}],
        cons_json=[{"label": "Weak endgame"}],
        model_version="rating_v5_configured",
        updated_at=None,
    )


class TeamIntelUtilsTests(unittest.TestCase):
    def test_cache_key_normalizes(self):
        key = routes_teams._cache_key("TEAM", " FrC118|2026TXHOU ")
        self.assertEqual(key, "intel:v1:team:frc118|2026txhou")

    def test_event_intel_cache_token_includes_rating_flags(self):
        token = routes_teams._event_intel_cache_token(
            event_key=" 2026TXHOU ",
            include_tba=True,
            include_statbotics=True,
            auto_heal_ratings=True,
            include_season_fallback=True,
            include_rating_details=False,
            include_rating_signals=False,
        )
        self.assertEqual(token, "2026txhou|tba=1|sb=1|heal=1|fallback=1|rd=0|rs=0")

    def test_team_intel_cache_token_normalizes_keys(self):
        token = routes_teams._team_intel_cache_token(
            team_key=" 118 ",
            event_key=" 2026TXHOU ",
            preferred_year=2026,
            fallback_year=2025,
            include_tba=True,
            include_statbotics=False,
            allow_season_fallback=True,
            auto_heal_ratings=False,
        )
        self.assertEqual(token, "frc118|2026txhou|2026|2025|tba=1|sb=0|fallback=1|heal=0")

    def test_extract_tba_rank_record(self):
        payload = {
            "qual": {
                "rank": 4,
                "record": {"wins": 7, "losses": 2, "ties": 0},
                "ranking": {"sort_orders": [2.3, 1.0]},
            },
            "playoff": {"level": "sf"},
        }
        parsed = routes_teams._extract_tba_rank_record(payload)
        self.assertEqual(parsed["rank"], 4)
        self.assertEqual(parsed["record"]["wins"], 7)
        self.assertEqual(parsed["sort_orders"], [2.3, 1.0])
        self.assertEqual(parsed["playoff"], {"level": "sf"})

    def test_breakdown_fallback_uses_season_when_event_empty(self):
        primary = {"matches_analyzed": 0, "averages": None, "season_scope": {"season_year": 2026}}
        fallback = {"matches_analyzed": 5, "averages": {"fuel_scoring_rate": 0.7}, "season_scope": {"season_year": 2025}}
        with patch("app.api.routes_teams._load_team_breakdown_payload", side_effect=[primary, fallback]):
            selected, fallbacks, warnings = routes_teams._load_team_breakdown_with_fallback(
                db=None,  # type: ignore[arg-type]
                team_key="frc118",
                event_key="2026txhou",
                allow_season_fallback=True,
            )
        self.assertEqual(selected, fallback)
        self.assertEqual(len(fallbacks), 1)
        self.assertTrue(any("using season-wide data" in message for message in warnings))

    def test_breakdown_fallback_is_not_used_when_disabled(self):
        primary = {"matches_analyzed": 0, "averages": None, "season_scope": {"season_year": 2026}}
        with patch("app.api.routes_teams._load_team_breakdown_payload", return_value=primary):
            selected, fallbacks, warnings = routes_teams._load_team_breakdown_with_fallback(
                db=None,  # type: ignore[arg-type]
                team_key="frc118",
                event_key="2026txhou",
                allow_season_fallback=False,
            )
        self.assertEqual(selected, primary)
        self.assertEqual(fallbacks, [])
        self.assertEqual(warnings, [])

    def test_resolve_team_rating_uses_latest_fallback(self):
        fake_rating = _fake_rating_row()
        with patch("app.api.routes_teams._event_team_rating_row", return_value=None), patch(
            "app.api.routes_teams._latest_team_rating_row",
            return_value=(fake_rating, "2025txhou"),
        ):
            rating, fallbacks, warnings = routes_teams._resolve_team_rating_context(
                db=None,  # type: ignore[arg-type]
                team_key="frc118",
                event_key="2026txhou",
                auto_heal_ratings=False,
            )
        self.assertTrue(rating["available"])
        self.assertEqual(rating["source"], "latest_event_fallback")
        self.assertEqual(rating["context_event_key"], "2025txhou")
        self.assertEqual(len(fallbacks), 1)
        self.assertTrue(any("latest available" in message.lower() for message in warnings))

    def test_rating_preview_backfills_subscores_when_missing(self):
        preview = routes_teams._rating_preview(
            _fake_rating_row(), source="event_rating", context_event_key="2026txhou"
        )
        subscores = preview["subscores"]
        self.assertIsInstance(subscores["manual_points_impact"], float)
        self.assertIsInstance(subscores["rp_contribution"], float)
        self.assertIsInstance(subscores["defense_presence"], float)
        self.assertIsInstance(subscores["penalty_discipline"], float)

    def _empty_analysis(self):
        keys = (
            "fuel_scoring_rate",
            "cycle_time_sec",
            "auto_contribution",
            "climb_success_prob",
            "defensive_engagement_sec",
            "reliability_score",
        )
        return {
            "averages": {key: None for key in keys},
            "metric_coverage": {key: {"observed_matches": 0} for key in keys},
            "climb_sources": {"level_capability": {"matches_considered": 0}},
        }

    def _official_stats(self):
        return {
            "available": True,
            "matches": 71,
            "copr_matches": 67,
            "events": [{"event_key": "2026casj", "matches": 12, "copr": True}, {"event_key": "2026cur", "matches": 55, "copr": True}],
            "fuel_per_match": 350.6,
            "fuel_per_active_minute": 131.5,
            "auto_points_per_match": 68.1,
            "climb": {
                "matches": 71,
                "climbs": 0,
                "rate": 0.0,
                "level_capability": {"best_level": None, "best_level_label": "No Climbs", "matches_considered": 71},
            },
        }

    def test_official_stats_fill_fuel_auto_and_climb_but_never_invent_the_rest(self):
        merged, fields = routes_teams._enrich_analysis_with_official_stats(self._empty_analysis(), self._official_stats())

        self.assertEqual(fields, ["fuel_scoring_rate", "auto_contribution"])
        self.assertEqual(merged["averages"]["fuel_scoring_rate"], 131.5)
        self.assertEqual(merged["averages"]["auto_contribution"], 68.1)
        self.assertEqual(merged["averages"]["climb_success_prob"], 0.0)
        for key in ("cycle_time_sec", "defensive_engagement_sec", "reliability_score"):
            self.assertIsNone(merged["averages"][key])
        coverage = merged["metric_coverage"]
        self.assertTrue(coverage["fuel_scoring_rate"]["estimated_from_model"])
        self.assertEqual(coverage["fuel_scoring_rate"]["estimate_source"], "tba_copr")
        self.assertIn("67 official matches at 2 events", coverage["fuel_scoring_rate"]["missing_reason"])
        # The tower result is recorded per robot, so the climb is a record, not an estimate.
        self.assertTrue(coverage["climb_success_prob"]["official_record"])
        self.assertFalse(coverage["climb_success_prob"].get("estimated_from_model", False))
        self.assertEqual(coverage["climb_success_prob"]["official_matches"], 71)
        self.assertEqual(merged["climb_sources"]["level_capability"]["best_level_label"], "No Climbs")
        self.assertEqual(merged["estimated_averages"], {"applied": True, "source": "official_tba", "fields": fields})

    def test_observed_values_win_over_official_estimates(self):
        analysis = self._empty_analysis()
        analysis["averages"]["fuel_scoring_rate"] = 44.0
        analysis["averages"]["climb_success_prob"] = 0.9
        analysis["climb_sources"]["level_capability"] = {"matches_considered": 6, "best_level_label": "Level 2"}

        merged, fields = routes_teams._enrich_analysis_with_official_stats(analysis, self._official_stats())

        self.assertEqual(fields, ["auto_contribution"])
        self.assertEqual(merged["averages"]["fuel_scoring_rate"], 44.0)
        self.assertEqual(merged["averages"]["climb_success_prob"], 0.9)
        self.assertEqual(merged["climb_sources"]["level_capability"]["best_level_label"], "Level 2")

    def test_no_official_data_leaves_metrics_empty(self):
        merged, fields = routes_teams._enrich_analysis_with_official_stats(self._empty_analysis(), {"available": False})

        self.assertEqual(fields, [])
        self.assertFalse(merged["estimated_averages"]["applied"])
        self.assertTrue(all(value is None for value in merged["averages"].values()))

    def test_sparse_signal_rating_synthesis_uses_statbotics_and_analysis(self):
        analysis_payload = {
            "averages": {
                "fuel_scoring_rate": 0.8,
                "auto_contribution": 4.2,
                "climb_success_prob": 0.55,
                "defensive_engagement_sec": 18.0,
                "reliability_score": 0.72,
            }
        }
        statbotics_context = {"team": {"norm_epa": {"current": 1775.0}}}
        synthesized = routes_teams._synthesize_rating_from_sparse_signals(
            event_key="2026txhou",
            analysis_payload=analysis_payload,
            statbotics_context=statbotics_context,
        )
        self.assertIsNotNone(synthesized)
        assert synthesized is not None
        self.assertTrue(synthesized["available"])
        self.assertEqual(synthesized["source"], "sparse_external_fallback")
        self.assertIsInstance(synthesized["rating_0_100"], float)
        self.assertGreaterEqual(float(synthesized["confidence_0_1"]), 0.12)

    def test_build_team_intel_payload_returns_official_stats_to_the_page(self):
        class _FakeDB:
            def get(self, *_args, **_kwargs):
                return None

        breakdown = {**self._empty_analysis(), "team": {"team_key": "frc254"}, "matches_analyzed": 0}
        fake_rating = {"available": False, "source": "none", "context_event_key": None}
        with patch("app.api.routes_teams._team_registered_events", return_value=(2026, [], "none")), patch(
            "app.api.routes_teams._load_team_breakdown_with_fallback",
            return_value=(breakdown, [], []),
        ), patch(
            "app.api.routes_teams._resolve_team_rating_context",
            return_value=(fake_rating, [], []),
        ), patch(
            "app.api.routes_teams.official_team_stats",
            return_value=self._official_stats(),
        ), patch(
            "app.api.routes_teams._synthesize_rating_from_sparse_signals",
            return_value=None,
        ):
            payload = asyncio.run(
                routes_teams._build_team_intel_payload(
                    db=_FakeDB(),  # type: ignore[arg-type]
                    team_key="frc254",
                    event_key="2026cur",
                    preferred_year=2026,
                    fallback_year=2025,
                    include_tba=False,
                    include_statbotics=False,
                    allow_season_fallback=True,
                    auto_heal_ratings=False,
                )
            )
        analysis = payload["analysis"]
        self.assertEqual(analysis["official_stats"]["fuel_per_match"], 350.6)
        self.assertEqual(analysis["averages"]["fuel_scoring_rate"], 131.5)
        self.assertTrue(analysis["metric_coverage"]["climb_success_prob"]["official_record"])

    def test_build_team_intel_payload_falls_back_when_team_not_in_local_db(self):
        class _FakeDB:
            def get(self, *_args, **_kwargs):
                return None

        fake_breakdown = {
            "team": {"team_key": "frc5417", "team_number": 5417, "nickname": None},
            "event_key": None,
            "season_scope": {"season_year": 2026},
            "data_freshness": {},
            "analysis_coverage": {},
            "matches_analyzed": 0,
            "averages": {},
            "metric_coverage": {},
        }
        fake_rating = {"available": False, "source": "none", "context_event_key": None}
        with patch("app.api.routes_teams._team_registered_events", return_value=(2026, [], "none")), patch(
            "app.api.routes_teams._load_team_breakdown_with_fallback",
            return_value=(fake_breakdown, [], []),
        ), patch(
            "app.api.routes_teams._resolve_team_rating_context",
            return_value=(fake_rating, [], []),
        ), patch(
            "app.api.routes_teams.official_team_stats",
            return_value={"available": False},
        ), patch(
            "app.api.routes_teams._synthesize_rating_from_sparse_signals",
            return_value=None,
        ):
            payload = asyncio.run(
                routes_teams._build_team_intel_payload(
                    db=_FakeDB(),  # type: ignore[arg-type]
                    team_key="frc5417",
                    event_key=None,
                    preferred_year=2026,
                    fallback_year=2025,
                    include_tba=False,
                    include_statbotics=False,
                    allow_season_fallback=True,
                    auto_heal_ratings=False,
                )
            )
        self.assertEqual(payload["team"]["team_key"], "frc5417")
        self.assertEqual(payload["team"]["team_number"], 5417)
        self.assertTrue(
            any(
                "not in local ingest yet" in str(message).lower()
                for message in payload.get("warnings", [])
            )
        )

    def test_team_data_coverage_payload_reports_missing_reasons(self):
        analysis_payload = {
            "matches_analyzed": 0,
            "averages": {},
            "metric_coverage": {},
            "analysis_coverage": {"fallback_used": True},
            "data_freshness": {"is_outdated": True},
        }
        rating_payload = {"available": False}
        tba_context = {"enabled": True, "event_status_summary": {}}
        statbotics_context = {"enabled": True, "team": None, "team_event": None, "team_year": None}
        payload = routes_teams._build_team_data_coverage_payload(
            analysis_payload=analysis_payload,
            rating_payload=rating_payload,
            tba_context=tba_context,
            statbotics_context=statbotics_context,
            fallbacks=[],
        )
        self.assertLess(payload["score_0_1"], 0.45)
        reasons = payload.get("missing_reasons", [])
        self.assertIn("no_analyzed_matches", reasons)
        self.assertIn("data_outdated", reasons)
        self.assertIn("rating_unavailable", reasons)

    def test_event_team_data_coverage_payload_high_when_data_present(self):
        payload = routes_teams._build_event_team_data_coverage_payload(
            event_analysis_count=7,
            season_analysis_count=9,
            uses_season_fallback=False,
            data_freshness={"is_outdated": False},
            rating_payload={"available": True},
            tba_status_summary={"rank": 3},
            statbotics_available=True,
        )
        self.assertGreaterEqual(payload["score_0_1"], 0.75)
        self.assertEqual(payload["tier"], "high")


if __name__ == "__main__":
    unittest.main()
