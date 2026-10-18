# The live rating trend, built from the team model's per-match rating history.
from __future__ import annotations


from app.services.ratings.snapshots import TREND_FLAT_EPSILON, rating_trend


def test_trend_direction_and_delta():
    t = rating_trend([50.0, 53.0, 57.0])
    assert t["latest_0_100"] == 57.0
    assert t["previous_0_100"] == 53.0
    assert t["delta"] == 4.0
    assert t["direction"] == "up"
    assert t["snapshot_count"] == 3
    assert t["sparkline"] == [50.0, 53.0, 57.0]
    assert rating_trend([60.0, 52.0])["direction"] == "down"


def test_trend_flat_within_epsilon():
    assert rating_trend([50.0, 50.0 + TREND_FLAT_EPSILON / 2])["direction"] == "flat"


def test_one_value_is_flat_and_no_history_is_none():
    t = rating_trend([42.0])
    assert t["delta"] == 0.0 and t["direction"] == "flat" and t["sparkline"] == [42.0]
    assert rating_trend(None) is None
    assert rating_trend([]) is None


def test_sparkline_capped_to_points():
    t = rating_trend([float(v) for v in range(20)], points=12)
    assert t["sparkline"] == [float(v) for v in range(8, 20)]
    assert t["snapshot_count"] == 20
    assert (t["min_0_100"], t["max_0_100"]) == (8.0, 19.0)


