from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.services.ratings import model as rating_model


class RatingModelGameContextTests(unittest.TestCase):
    def test_rebuilt_active_hub_duration_formula_matches_manual_schedule(self):
        duration = rating_model._rebuilt_active_hub_duration_sec(
            auto_sec=20.0,
            teleop_sec=140.0,
            endgame_sec=30.0,
            include_post_deactivate_grace=True,
        )
        self.assertAlmostEqual(float(duration), 99.0, places=3)

    def test_manual_context_exposes_active_hub_duration(self):
        context = rating_model._manual_game_context()
        phases = context.get("phases") if isinstance(context.get("phases"), dict) else {}
        self.assertIn("teleop_active_hub_sec", phases)
        if int(context.get("season_year") or 0) >= 2026:
            self.assertAlmostEqual(float(phases.get("teleop_active_hub_sec") or 0.0), 99.0, places=3)

    def test_active_hub_attempt_event_filter(self):
        active_event = SimpleNamespace(
            event_type="teleop_fuel_score_attempt",
            time_sec=42.0,
            meta={"hub_active_window": True},
        )
        inactive_event = SimpleNamespace(
            event_type="teleop_fuel_score_attempt",
            time_sec=55.0,
            meta={"hub_active_window": False},
        )
        legacy_event = SimpleNamespace(
            event_type="teleop_fuel_score_attempt",
            time_sec=61.0,
            meta={},
        )
        other_event = SimpleNamespace(
            event_type="depot_intake",
            time_sec=33.0,
            meta={"hub_active_window": True},
        )

        self.assertTrue(rating_model._is_active_hub_attempt_event(active_event))
        self.assertFalse(rating_model._is_active_hub_attempt_event(inactive_event))
        self.assertTrue(rating_model._is_active_hub_attempt_event(legacy_event))
        self.assertFalse(rating_model._is_active_hub_attempt_event(other_event))


if __name__ == "__main__":
    unittest.main()
