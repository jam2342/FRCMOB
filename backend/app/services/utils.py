# Shared utility functions across services.

import math
from pathlib import Path
from typing import Any

def pg_sqlstate_code(exc: Exception) -> str | None:
    # Extract the PostgreSQL SQLSTATE code from a SQLAlchemy or psycopg exception.
    candidate = getattr(exc, "orig", None) or exc
    code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
    return str(code).strip().upper() if code else None

# ── Canonical project paths ────────────────────────────────────────────────
BACKEND_ROOT = Path(__file__).resolve().parents[2]
MEDIA_ROOT = BACKEND_ROOT / "media"
VIDEOS_ROOT = MEDIA_ROOT / "videos"
ANALYSIS_FRAMES_ROOT = MEDIA_ROOT / "analysis_frames"
FRAMES_ROOT = MEDIA_ROOT / "frames"

# ── FRC comp-level ordering (shared across routes/services) ────────────────
COMP_LEVEL_ORDER: dict[str, int] = {
    "qm": 0,
    "ef": 1,
    "qf": 2,
    "playoff": 2,
    "sf": 3,
    "f": 4,
}

def _as_float(value: Any) -> float | None:
    # Convert *value* to float, returning ``None`` on failure.
    #
    # Booleans are rejected (``True`` / ``False`` are not treated as 1/0).
    # Non-finite values (NaN, Inf) also return ``None``.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        try:
            result = float(token)
        except ValueError:
            return None
        return result if math.isfinite(result) else None
    return None

def _safe_float(value: Any, default: float = 0.0) -> float:
    # Like :func:`_as_float` but returns *default* instead of ``None``.
    result = _as_float(value)
    return result if result is not None else default

def _round(value: float, digits: int = 3) -> float:
    # Round a floating point value to the specified number of digits.
    return round(value, digits)

def _clamp(value: float, low: float, high: float) -> float:
    # Clamp value between low and high bounds.
    return max(low, min(high, value))

def _mean(values: list[float]) -> float | None:
    # Calculate arithmetic mean, return None if empty.
    if not values:
        return None
    return sum(values) / len(values)

def _std(values: list[float]) -> float | None:
    # Calculate standard deviation, return None if insufficient data.
    if len(values) < 2:
        return 0.0 if values else None
    avg = sum(values) / len(values)
    variance = sum((value - avg) ** 2 for value in values) / len(values)
    return math.sqrt(max(0.0, variance))

def _weighted_mean(pairs: list[tuple[float, float]]) -> float | None:
    # Calculate weighted mean from (value, weight) pairs.
    numerator = 0.0
    denominator = 0.0
    for value, weight in pairs:
        if weight <= 0:
            continue
        numerator += float(value) * float(weight)
        denominator += float(weight)
    if denominator <= 1e-9:
        return None
    return numerator / denominator

def _weighted_median(pairs: list[tuple[float, float]]) -> float | None:
    # Calculate weighted median from (value, weight) pairs.
    valid = [(float(value), float(weight)) for value, weight in pairs if weight > 0]
    if not valid:
        return None
    valid.sort(key=lambda item: item[0])
    total_weight = sum(weight for _, weight in valid)
    midpoint = total_weight / 2.0
    running = 0.0
    for value, weight in valid:
        running += weight
        if running >= midpoint:
            return value
    return valid[-1][0]

def _weighted_std(pairs: list[tuple[float, float]]) -> float | None:
    # Calculate weighted standard deviation from (value, weight) pairs.
    avg = _weighted_mean(pairs)
    if avg is None:
        return None
    numerator = 0.0
    denominator = 0.0
    for value, weight in pairs:
        if weight <= 0:
            continue
        numerator += float(weight) * ((float(value) - avg) ** 2)
        denominator += float(weight)
    if denominator <= 1e-9:
        return None
    return math.sqrt(max(0.0, numerator / denominator))

def automation_redis_key(scope: str, season: int, key_type: str) -> str:
    return f"automation:{scope}:season:{season}:{key_type}"

def decode_redis_float(raw: bytes | str | None) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    token = str(raw).strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        return None
