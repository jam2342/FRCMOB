# The live rating trend (sparkline, delta, direction) and the old snapshot table.
#
# The trend reads the team model's rating after each match here
# (details.rating_history, ratings/model.py), so a model change never shows up as a
# move. (The old per-recompute rating_snapshots table was dropped in migration 0024.)
from __future__ import annotations

from typing import Any


# How many recent points the sparkline draws, and what counts as a meaningful move
# (smaller deltas render flat).
TREND_SPARKLINE_POINTS = 12
TREND_FLAT_EPSILON = 0.25


def _direction(delta: float) -> str:
    if delta > TREND_FLAT_EPSILON:
        return "up"
    if delta < -TREND_FLAT_EPSILON:
        return "down"
    return "flat"


def rating_trend(history: list[float] | None, *, points: int = TREND_SPARKLINE_POINTS) -> dict[str, Any] | None:
    # history: the rating before this event's first counted match, then after each
    # match (oldest first). None until there is at least one value.
    values = [float(v) for v in history or [] if v is not None]
    if not values:
        return None
    latest = values[-1]
    previous = values[-2] if len(values) >= 2 else latest
    delta = round(latest - previous, 2)
    spark = values[-points:]
    return {
        "latest_0_100": round(latest, 2),
        "previous_0_100": round(previous, 2),
        "delta": delta,
        "direction": _direction(delta),
        "snapshot_count": len(values),
        "sparkline": [round(v, 2) for v in spark],
        "min_0_100": round(min(spark), 2),
        "max_0_100": round(max(spark), 2),
    }
