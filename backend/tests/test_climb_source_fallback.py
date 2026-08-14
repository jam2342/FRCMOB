from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.api import routes_scouting


def _finding(
    *,
    match_key: str,
    source: str = "video_v3_tracks",
    climb_success_prob: float | None = None,
    summary: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        match_key=match_key,
        source=source,
        climb_success_prob=climb_success_prob,
        summary=summary or {},
        fuel_scoring_rate=None,
        cycle_time_sec=None,
        auto_contribution=None,
        defensive_engagement_sec=None,
        reliability_score=None,
    )


class ClimbSourceFallbackTests(unittest.TestCase):
    def test_compute_averages_prefers_fallback_when_climb_missing(self):
        findings = [
            _finding(match_key="m1", climb_success_prob=None),
            _finding(match_key="m2", climb_success_prob=0.2),
        ]
        averages = routes_scouting._compute_averages(
            findings,
            climb_fallback_by_match={"m1": 1.0, "m2": 0.35},
        )
        self.assertIsNotNone(averages)
        assert averages is not None
        self.assertGreater(float(averages["climb_success_prob"] or 0.0), 0.35)

    def test_compute_climb_sources_includes_fallback_for_overall(self):
        findings = [
            _finding(match_key="m1", source="video_v3_tracks", climb_success_prob=None),
        ]
        sources = routes_scouting._compute_climb_sources(
            findings,
            climb_fallback_by_match={"m1": 1.0},
        )
        self.assertEqual(sources.get("overall_climb_success_prob"), 1.0)
        capability = sources.get("level_capability") if isinstance(sources, dict) else None
        self.assertTrue(isinstance(capability, dict))
        assert isinstance(capability, dict)
        self.assertEqual(capability.get("matches_with_success_no_level"), 1)


if __name__ == "__main__":
    unittest.main()
