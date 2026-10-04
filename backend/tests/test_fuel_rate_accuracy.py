from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.api import routes_scouting


def _finding(
    *,
    match_key: str,
    fuel_scoring_rate: float | None,
    cycle_time_sec: float | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        match_key=match_key,
        event_key="2026week0",
        alliance="red",
        station="r1",
        source="tba_score_breakdown",
        fuel_scoring_rate=fuel_scoring_rate,
        cycle_time_sec=cycle_time_sec,
        auto_contribution=None,
        climb_success_prob=None,
        defensive_engagement_sec=None,
        reliability_score=None,
        summary={},
    )


class FuelRateAccuracyTests(unittest.TestCase):
    def test_compute_averages_recovers_legacy_capped_rates_from_cycle_time(self):
        finding = _finding(match_key="qm1", fuel_scoring_rate=16.0, cycle_time_sec=(120.0 / 90.0))

        averages = routes_scouting._compute_averages([finding])
        self.assertIsNotNone(averages)
        assert averages is not None

        # 90 teleop scores in 120s => 45.0 scores/min.
        self.assertAlmostEqual(float(averages.get("fuel_scoring_rate") or 0.0), 45.0, places=3)

    def test_serialize_finding_returns_uncapped_rate(self):
        finding = _finding(match_key="qm2", fuel_scoring_rate=16.0, cycle_time_sec=(120.0 / 90.0))

        payload = routes_scouting._serialize_finding(finding, match_time=0)
        self.assertAlmostEqual(float(payload.get("fuel_scoring_rate") or 0.0), 45.0, places=3)
        self.assertIsNone(payload["cycle_time_sec"])

    def test_legacy_official_auto_is_not_shown_as_robot_output(self):
        finding = _finding(match_key="qm3", fuel_scoring_rate=45.0, cycle_time_sec=1.5)
        finding.auto_contribution = 30.0
        averages = routes_scouting._compute_averages([finding])
        self.assertIsNone(averages["cycle_time_sec"])
        self.assertIsNone(averages["auto_contribution"])
        self.assertIsNone(routes_scouting._serialize_finding(finding, match_time=0)["auto_contribution"])


if __name__ == "__main__":
    unittest.main()
