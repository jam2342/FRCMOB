from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.api import routes_scouting
from app.services import jobs


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

    def test_score_breakdown_fallbacks_do_not_cap_high_fuel_rates(self):
        payload = {
            "alliances": {
                "red": {"team_keys": ["frc1", "frc2", "frc3"]},
                "blue": {"team_keys": ["frc4", "frc5", "frc6"]},
            },
            "score_breakdown": {
                "red": {
                    "teleopCount": 279,
                    "teleopPoints": 279,
                },
                "blue": {
                    "teleopCount": 0,
                    "teleopPoints": 0,
                },
            },
        }

        fallbacks = jobs._score_breakdown_cycle_fallbacks_from_match_payload(
            match_payload=payload,
            teleop_duration_sec=120.0,
        )

        for team_key in ("frc1", "frc2", "frc3"):
            row = fallbacks.get(team_key)
            self.assertIsNotNone(row)
            assert row is not None
            self.assertAlmostEqual(float(row.get("fuel_scoring_rate") or 0.0), 46.5, places=3)


if __name__ == "__main__":
    unittest.main()
