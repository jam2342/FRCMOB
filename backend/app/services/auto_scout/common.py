# Conversions and track coverage shared by inference and training.
#
# Keep these permissive conversions compatible with the existing predictors;
# validation of finite values belongs at their input boundaries.
from __future__ import annotations

from app.db import models
from app.services.game_config import load_game_config


def _safe_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _match_total_sec() -> float:
    try:
        return float(load_game_config().phases.total_sec)
    except Exception:
        return 160.0


def _approx_track_coverage(tracks: list[models.RobotTrack]) -> tuple[float, float]:
    if not tracks:
        return 0.0, 0.0
    total_sec = max(_match_total_sec(), 1.0)
    min_time = min(float(track.time_sec) for track in tracks)
    max_time = max(float(track.time_sec) for track in tracks)
    tracked_sec = max(0.0, max_time - min_time)
    coverage = _clamp(tracked_sec / total_sec, 0.0, 1.0)
    return coverage, tracked_sec
