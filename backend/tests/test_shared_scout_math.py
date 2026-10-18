import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.auto_scout.common import _approx_track_coverage, _safe_float


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
