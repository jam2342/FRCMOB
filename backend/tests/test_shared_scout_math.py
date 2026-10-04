import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.auto_scout.common import _approx_track_coverage, _safe_float
from app.services.ml.model_eval import expected_calibration_error, r2_score
from app.services.ratings.base_score import base_rating_score


@pytest.mark.parametrize("value, expected", [(None, None), ("", None), ("bad", None), ("1.25", 1.25), (0, 0.0)])
def test_shared_conversion_keeps_existing_missing_value_semantics(value, expected):
    assert _safe_float(value) == expected


def test_shared_conversion_keeps_nonfinite_input_behavior():
    # This refactor is not a new validation policy for existing predictors.
    assert math.isnan(_safe_float("nan"))
    assert _safe_float("inf") == math.inf


def test_coverage_uses_season_duration_and_preserves_sparse_endpoint_semantics():
    with patch("app.services.auto_scout.common.load_game_config", return_value=SimpleNamespace(phases=SimpleNamespace(total_sec=100))):
        assert _approx_track_coverage([]) == (0.0, 0.0)
        assert _approx_track_coverage([SimpleNamespace(time_sec=30)]) == (0.0, 0.0)
        assert _approx_track_coverage([SimpleNamespace(time_sec=90), SimpleNamespace(time_sec=10)]) == (0.8, 80.0)
        assert _approx_track_coverage([SimpleNamespace(time_sec=0), SimpleNamespace(time_sec=150)]) == (1.0, 150.0)


def test_shared_metrics_known_values_and_degenerate_inputs():
    assert r2_score([1.0, 2.0, 3.0], [1.0, 2.0, 4.0]) == 0.5
    assert r2_score([1.0, 1.0], [1.0, 2.0]) is None
    assert r2_score([], []) is None
    assert expected_calibration_error([0.0, 1.0], [0.2, 0.8]) == pytest.approx(0.2)
    assert expected_calibration_error([], []) is None


def test_base_rating_preserves_relative_weights_and_antidefense_override():
    values = dict(results_anchor=10, performance=20, driver_skill=30, robot_level=40,
                  manual_points=50, rp_contribution=60, auto_contribution=70, anti_defense=80)
    from app.services.ratings.constants import BASE_AUTO_WEIGHT
    for anti_weight in (0.0, 0.04, 0.1):
        expected = (2.1 + 5.8 + 4.8 + 4.4 + 4.5 + 3.0 + BASE_AUTO_WEIGHT * 70 + anti_weight * 80)
        expected /= 0.91 + BASE_AUTO_WEIGHT + anti_weight
        assert base_rating_score(**values, anti_defense_weight=anti_weight) == pytest.approx(expected)
