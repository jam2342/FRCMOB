# REBUILT score-breakdown helpers: which hub opens first, and station ordering.
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.utils import _as_float as _as_number
from app.tba.client import TBAClient


def _sum_numeric_keys(payload: dict[str, Any], keys: list[str]) -> float | None:
    total = 0.0
    seen = False
    for key in keys:
        value = _as_number(payload.get(key))
        if value is None:
            continue
        total += float(value)
        seen = True
    return total if seen else None

def _season_year_from_event_key(event_key: object) -> int | None:
    token = str(event_key or "").strip()[:4]
    if token.isdigit():
        year = int(token)
        if 1992 <= year <= 2100:
            return year
    return None


def _extract_rebuilt_auto_fuel_count(breakdown: dict[str, Any]) -> float | None:
    hub = breakdown.get("hubScore") if isinstance(breakdown.get("hubScore"), dict) else {}
    hub_auto_count = _as_number(hub.get("autoCount"))
    if hub_auto_count is not None and hub_auto_count >= 0.0:
        return float(hub_auto_count)

    total_auto_points = _as_number(breakdown.get("totalAutoPoints"))
    auto_tower_points = _as_number(breakdown.get("autoTowerPoints"))
    hub_auto_points = _as_number(hub.get("autoPoints"))
    if hub_auto_points is None:
        if total_auto_points is not None and auto_tower_points is not None:
            hub_auto_points = max(0.0, float(total_auto_points) - float(auto_tower_points))
        else:
            hub_auto_points = total_auto_points
    if hub_auto_points is None:
        return None
    return max(0.0, float(hub_auto_points))

def _extract_rebuilt_shift_bias(breakdown: dict[str, Any]) -> float | None:
    hub = breakdown.get("hubScore") if isinstance(breakdown.get("hubScore"), dict) else {}
    odd_counts = _sum_numeric_keys(hub, ["shift1Count", "shift3Count"])
    even_counts = _sum_numeric_keys(hub, ["shift2Count", "shift4Count"])
    if odd_counts is not None and even_counts is not None:
        return float(odd_counts) - float(even_counts)

    odd_points = _sum_numeric_keys(hub, ["shift1Points", "shift3Points"])
    even_points = _sum_numeric_keys(hub, ["shift2Points", "shift4Points"])
    if odd_points is not None and even_points is not None:
        return float(odd_points) - float(even_points)
    return None

def _infer_rebuilt_shift1_active_alliance(
    match_payload: dict[str, Any],
) -> tuple[str | None, str]:
    score_breakdown = match_payload.get("score_breakdown")
    if not isinstance(score_breakdown, dict):
        return None, "missing_score_breakdown"

    red_breakdown = score_breakdown.get("red")
    blue_breakdown = score_breakdown.get("blue")
    if not isinstance(red_breakdown, dict) or not isinstance(blue_breakdown, dict):
        return None, "missing_alliance_breakdown"

    red_auto = _extract_rebuilt_auto_fuel_count(red_breakdown)
    blue_auto = _extract_rebuilt_auto_fuel_count(blue_breakdown)
    if red_auto is not None and blue_auto is not None and abs(red_auto - blue_auto) > 1e-6:
        # 2026 Manual 6.4.1: alliance scoring more AUTO fuel is inactive in SHIFT 1.
        return ("blue" if red_auto > blue_auto else "red"), "auto_fuel_count"

    red_bias = _extract_rebuilt_shift_bias(red_breakdown)
    blue_bias = _extract_rebuilt_shift_bias(blue_breakdown)
    if (
        red_bias is not None
        and blue_bias is not None
        and abs(red_bias) > 1e-6
        and abs(blue_bias) > 1e-6
        and (red_bias * blue_bias) < 0.0
    ):
        return ("red" if red_bias > 0.0 else "blue"), "shift_distribution"
    if red_bias is not None and abs(red_bias) > 1e-6:
        return ("red" if red_bias > 0.0 else "blue"), "shift_distribution_partial"
    if blue_bias is not None and abs(blue_bias) > 1e-6:
        return ("blue" if blue_bias > 0.0 else "red"), "shift_distribution_partial"

    return None, "tie_or_missing_auto_fuel"


def _fetch_match_payload_from_tba(match_key: str) -> dict[str, Any] | None:
    if not str(settings.tba_auth_key or "").strip():
        return None
    try:
        tba = TBAClient()
        payload = tba.match(match_key)
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


def _station_sort_key(station: str | None) -> tuple[str, int]:
    if not station:
        return ("z", 99)
    prefix = station[0].lower()
    suffix = station[1:]
    try:
        number = int(suffix)
    except ValueError:
        number = 99
    return (prefix, number)

