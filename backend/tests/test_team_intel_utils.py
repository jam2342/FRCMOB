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
        self.assertTrue(any("temporarily using season-wide data" in message for message in warnings))

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

    def test_analysis_enrichment_uses_rating_estimates_for_missing_averages(self):
        preview = routes_teams._rating_preview(
            _fake_rating_row(), source="event_rating", context_event_key="2026txhou"
        )
        preview["details"] = {
            "raw_features": {
                "bps_median": 0.74,
                "auto_points_est": 5.8,
                "climb_success": 0.62,
                "defense_presence": 21.0,
                "uptime": 0.81,
            }
        }
        analysis_payload = {
            "averages": {
                "fuel_scoring_rate": None,
                "cycle_time_sec": None,
                "auto_contribution": None,
                "climb_success_prob": None,
                "defensive_engagement_sec": None,
                "reliability_score": None,
            },
            "metric_coverage": {
                "fuel_scoring_rate": {"observed_matches": 0},
                "cycle_time_sec": {"observed_matches": 0},
                "auto_contribution": {"observed_matches": 0},
                "climb_success_prob": {"observed_matches": 0},
                "defensive_engagement_sec": {"observed_matches": 0},
                "reliability_score": {"observed_matches": 0},
            },
        }
        merged, fields = routes_teams._enrich_analysis_with_rating_estimates(
            analysis_payload,
            preview,
        )
        self.assertTrue(fields)
        self.assertTrue(merged["estimated_averages"]["applied"])
        self.assertTrue(all(metric in merged["averages"] for metric in fields))
        self.assertAlmostEqual(float(merged["averages"]["fuel_scoring_rate"]), 44.4, places=3)
        self.assertTrue(
            all(
                merged["metric_coverage"][metric].get("estimated_from_model")
                for metric in fields
            )
        )

    def test_analysis_enrichment_skips_estimates_without_observed_or_raw_signals(self):
        analysis_payload = {
            "averages": {
                "fuel_scoring_rate": None,
                "cycle_time_sec": None,
                "auto_contribution": None,
                "climb_success_prob": None,
                "defensive_engagement_sec": None,
                "reliability_score": None,
            },
            "metric_coverage": {
                "fuel_scoring_rate": {"observed_matches": 0},
                "cycle_time_sec": {"observed_matches": 0},
            },
        }
        rating_payload = {
            "available": True,
            "source": "event_rating",
            "model_version": "rating_v13_forgiving_scale",
            "subscores": {
                "throughput": 66.0,
                "auto_contribution": 54.0,
                "endgame": 51.0,
                "defense_presence": 44.0,
                "consistency": 68.0,
            },
            "details": {"raw_features": {}},
        }

        merged, fields = routes_teams._enrich_analysis_with_rating_estimates(
            analysis_payload,
            rating_payload,
        )
        self.assertEqual(fields, [])
        self.assertFalse(merged["estimated_averages"]["applied"])
        self.assertIsNone(merged["averages"]["fuel_scoring_rate"])

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

    def test_sparse_external_rating_does_not_inject_constant_metric_defaults(self):
        analysis_payload = {
            "averages": {
                "fuel_scoring_rate": None,
                "cycle_time_sec": None,
                "auto_contribution": None,
                "climb_success_prob": None,
                "defensive_engagement_sec": None,
                "reliability_score": None,
            },
            "metric_coverage": {
                "fuel_scoring_rate": {"observed_matches": 0},
            },
        }
        rating_payload = {
            "available": True,
            "source": "sparse_external_fallback",
            "model_version": "rating_sparse_external_fallback_v1",
            "subscores": {
                "throughput": 65.2,
                "auto_contribution": 32.0,
                "endgame": 62.0,
                "defense_presence": 56.0,
                "consistency": 66.1,
            },
            "details": {
                "fallback_model": {"active": True, "label": "sparse_external_fallback"},
                "raw_features": {
                    "bps_median": None,
                    "auto_points_est": None,
                    "climb_success": None,
                    "defense_presence": None,
                    "uptime": None,
                    "statbotics_norm_epa": 1899.0,
                },
            },
        }

        merged, fields = routes_teams._enrich_analysis_with_rating_estimates(
            analysis_payload,
            rating_payload,
        )
        self.assertEqual(fields, [])
        self.assertFalse(merged["estimated_averages"]["applied"])
        self.assertIsNone(merged["averages"]["fuel_scoring_rate"])
        self.assertIsNone(merged["averages"]["cycle_time_sec"])

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
            "app.api.routes_teams._enrich_analysis_with_rating_estimates",
            return_value=(fake_breakdown, []),
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
