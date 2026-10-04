"""Golden outputs captured from the pre-cleanup implementation, with external ML/HTTP off."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.db import models
from app.services.ratings import data_loader, feature_extraction, model
from tests.test_pipeline_smoke import _seed_pipeline_rows


def rating_outputs(db, scenario, feature_module=feature_extraction, model_module=model):
    _seed_pipeline_rows(db)
    if scenario == "mixed_events":
        run = db.query(models.AnalysisRun).one()
        for event_type, time_sec, meta in [
            ("teleop_fuel_score_success", 40.0, {"count_estimate": 7}),
            ("teleop_fuel_score_success", 50.0, {}),
            ("major_foul", 55.0, {"count_estimate": 2}),
            ("protected_zone_interference", 60.0, {}),
            ("zone_dwell", 65.0, {"zone_key": "protected_zone"}),
            ("robot_disabled", 70.0, {}),
            ("climb_success", 15.0, {"tower_dwell_sec": 12}),
            ("climb_attempt", 140.0, {}),
            ("climb_success", 150.0, {"official_points": 30}),
        ]:
            db.add(models.MatchEvent(
                analysis_run_id=run.id, match_key=run.match_key, event_key="2026txhou",
                team_key="frc118", event_type=event_type, time_sec=time_sec,
                confidence=0.9, meta=meta,
            ))
        db.commit()
    with (
        patch.object(data_loader, "_load_statbotics_epa_by_team", return_value={}),
        patch.object(model_module.settings, "ml_shadow_enabled", False),
        patch.object(model_module.settings, "ml_role_blend", 0.0),
    ):
        context = model_module._manual_game_context()
        features = feature_module.extract_team_features(data_loader.load_event_rating_data(db, "2026txhou", context), context)
        result = model_module.recompute_event_ratings(db, "2026txhou")
    # Timing and write timestamps intentionally vary; all numeric features and API
    # ratings, subscores, pros/cons, and evidence remain under comparison.
    for rating in result["ratings"]:
        rating.pop("updated_at", None)
    return {"features": features.feature_raw, "confidence": features.confidence_support, "ratings": result["ratings"]}


@pytest.mark.parametrize("scenario", ["sparse", "mixed_events"])
def test_ratings_preserve_pre_cleanup_outputs(db_session, scenario):
    expected = json.loads((Path(__file__).parent / "fixtures/rating_refactor.json").read_text())
    assert rating_outputs(db_session, scenario) == expected[scenario]
