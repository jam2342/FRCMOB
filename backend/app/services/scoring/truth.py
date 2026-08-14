# Score-breakdown truth extraction — pure functions, no HTTP / DB dependencies.
#
# Extracted from ``routes_events`` so that service-layer code
# (``climb_official_backfill``, etc.) can use them without importing from the
# API layer.

from __future__ import annotations

from contextlib import suppress

from app.services.game_config import load_game_config
from app.services.season_config import (
    REBUILT_HUB_SCORE_GRACE_SEC as _SEASON_REBUILT_HUB_SCORE_GRACE_SEC,
    rebuilt_active_hub_duration_from_phases as _rebuilt_active_hub_duration_from_phases,
)
from app.services.utils import _as_float

# Backward-compatible re-export for existing callers/tests.
REBUILT_HUB_SCORE_GRACE_SEC = _SEASON_REBUILT_HUB_SCORE_GRACE_SEC

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_TBA_PHASES: dict[str, float] = {
    "total_sec": 160.0,
    "auto_sec": 20.0,
    "teleop_sec": 140.0,
    "endgame_sec": 30.0,
}

DEFAULT_TBA_SCORING_POINTS: dict[str, float] = {
    "auto_level1_climb": 15.0,
    "low_climb": 10.0,
    "mid_climb": 20.0,
    "high_climb": 30.0,
}

# ── Small helpers ────────────────────────────────────────────────────────────

def _coalesce_float(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return float(value)
    return None

def _sum_optional(*values: float | None) -> float | None:
    total = 0.0
    seen = False
    for value in values:
        if value is None:
            continue
        total += float(value)
        seen = True
    return total if seen else None

def _sum_dict_keys(data: dict, keys: list[str]) -> float | None:
    total = 0.0
    seen = False
    for key in keys:
        value = _as_float(data.get(key))
        if value is None:
            continue
        total += float(value)
        seen = True
    return total if seen else None

def _share_evenly(value: float | None, slots: int) -> list[float]:
    if value is None or slots <= 0:
        return [0.0] * max(0, slots)
    return [float(value) / float(slots)] * slots

def _share_weighted(
    value: float | None,
    keys: list[str],
    team_weight_by_key: dict[str, float] | None = None,
) -> list[float]:
    slots = len(keys)
    if value is None or slots <= 0:
        return [0.0] * max(0, slots)
    weights: list[float] = []
    for key in keys:
        raw_weight = _as_float((team_weight_by_key or {}).get(str(key)))
        weights.append(max(0.08, float(raw_weight)) if raw_weight is not None and raw_weight > 0.0 else 1.0)
    total_weight = sum(weights)
    if total_weight <= 0.0:
        return _share_evenly(value, slots)
    total_value = float(value)
    return [total_value * (weight / total_weight) for weight in weights]

def _first_non_null(mapping: dict, keys: list[str]) -> object:
    for key in keys:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return None

def _is_yes(value: object) -> bool:
    token = str(value or "").strip().lower()
    return token in {"yes", "true", "1"}

def _tower_points_from_status(
    status: object,
    *,
    auto_phase: bool,
    scoring_points: dict[str, float],
) -> float:
    token = str(status or "").strip().lower()
    if not token or token in {"none", "no", "unknown"}:
        return 0.0
    if "level3" in token or "deep" in token or "travers" in token or token == "high":  # nosec B105
        return float(scoring_points["high_climb"])
    if "level2" in token or "shallow" in token or token == "mid":  # nosec B105
        return float(scoring_points["mid_climb"])
    if "level1" in token:
        return (
            float(scoring_points["auto_level1_climb"])
            if auto_phase
            else float(scoring_points["low_climb"])
        )
    if "park" in token or token == "low":  # nosec B105
        return float(scoring_points["low_climb"]) * 0.25
    return 0.0

def _reefscape_endgame_points_2025(status: object) -> float:
    token = str(status or "").strip().lower()
    if token == "deepcage":  # nosec B105
        return 12.0
    if token == "shallowcage":  # nosec B105
        return 6.0
    if token == "parked":  # nosec B105
        return 2.0
    return 0.0

# ── Main truth functions ─────────────────────────────────────────────────────

def _rebuilt_active_hub_duration_sec(
    phases: dict[str, float],
    *,
    include_post_deactivate_grace: bool = True,
) -> float:
    return _rebuilt_active_hub_duration_from_phases(
        phases,
        include_post_deactivate_grace=include_post_deactivate_grace,
    )

def _truth_context() -> dict[str, dict[str, float]]:
    phases = dict(DEFAULT_TBA_PHASES)
    scoring_points = dict(DEFAULT_TBA_SCORING_POINTS)
    with suppress(Exception):
        config = load_game_config()
        phases = {
            "total_sec": float(config.phases.total_sec),
            "auto_sec": float(config.phases.auto_sec),
            "teleop_sec": float(config.phases.teleop_sec),
            "endgame_sec": float(config.phases.endgame_sec),
        }
        for action in config.scoring_actions:
            if action.points is None:
                continue
            key = str(action.key)
            if key in scoring_points:
                scoring_points[key] = float(action.points)
    phases["teleop_active_hub_sec"] = _rebuilt_active_hub_duration_sec(
        phases,
        include_post_deactivate_grace=True,
    )
    return {
        "phases": phases,
        "scoring_points": scoring_points,
    }

def _parse_2026_alliance_truth(
    *,
    team_keys: list[str],
    alliance: str,
    breakdown: dict,
    context: dict[str, dict[str, float]],
    team_weight_by_key: dict[str, float] | None = None,
) -> list[dict]:
    count = len(team_keys)
    if count == 0:
        return []
    hub_score = breakdown.get("hubScore")
    hub = hub_score if isinstance(hub_score, dict) else {}
    total_auto_points_raw = _as_float(breakdown.get("totalAutoPoints"))
    auto_tower_alliance_points = _as_float(breakdown.get("autoTowerPoints"))
    hub_auto_points = _coalesce_float(
        _as_float(hub.get("autoPoints")),
        (
            total_auto_points_raw - auto_tower_alliance_points
            if total_auto_points_raw is not None and auto_tower_alliance_points is not None
            else total_auto_points_raw
        ),
    )
    hub_shift_points = _sum_dict_keys(
        hub,
        [
            "shift1Points",
            "shift2Points",
            "shift3Points",
            "shift4Points",
            "transitionPoints",
            "endgamePoints",
        ],
    )
    hub_shift_counts = _sum_dict_keys(
        hub,
        [
            "shift1Count",
            "shift2Count",
            "shift3Count",
            "shift4Count",
            "transitionCount",
            "endgameCount",
        ],
    )
    total_teleop_points = _coalesce_float(
        _as_float(hub.get("teleopPoints")),
        hub_shift_points,
        (
            (_as_float(breakdown.get("totalTeleopPoints")) - _as_float(breakdown.get("endGameTowerPoints")))
            if _as_float(breakdown.get("totalTeleopPoints")) is not None
            and _as_float(breakdown.get("endGameTowerPoints")) is not None
            else _as_float(breakdown.get("totalTeleopPoints"))
        ),
    )
    total_teleop_count = _coalesce_float(
        _as_float(hub.get("teleopCount")),
        hub_shift_counts,
        _as_float(hub.get("totalCount")),
    )
    teleop_count_capped_to_points = False
    if (
        total_teleop_count is not None
        and total_teleop_points is not None
        and total_teleop_points > 0.0
        and total_teleop_count > (total_teleop_points * 1.05)
    ):
        total_teleop_count = float(total_teleop_points)
        teleop_count_capped_to_points = True

    auto_points_by_team = _share_evenly(hub_auto_points, count)
    teleop_points_by_team = _share_weighted(total_teleop_points, team_keys, team_weight_by_key)
    teleop_count_by_team = (
        _share_weighted(total_teleop_count, team_keys, team_weight_by_key)
        if total_teleop_count is not None
        else []
    )
    scoring_points = context["scoring_points"]

    rows: list[dict] = []
    for idx, team_key in enumerate(team_keys, start=1):
        auto_status = _first_non_null(
            breakdown,
            [
                f"autoTowerRobot{idx}",
                f"autoRobot{idx}",
                f"autoLineRobot{idx}",
                f"autoMobilityRobot{idx}",
            ],
        )
        endgame_status = _first_non_null(
            breakdown,
            [
                f"endGameTowerRobot{idx}",
                f"endGameRobot{idx}",
                f"endgameRobot{idx}",
                f"endGameCageRobot{idx}",
                f"endGameHangarRobot{idx}",
            ],
        )
        auto_tower_points = _tower_points_from_status(
            auto_status,
            auto_phase=True,
            scoring_points=scoring_points,
        )
        endgame_points = _tower_points_from_status(
            endgame_status,
            auto_phase=False,
            scoring_points=scoring_points,
        )
        endgame_token = str(endgame_status or "").strip().lower()
        has_endgame_result = endgame_token not in {"", "none", "no", "unknown", "null"}
        auto_points_value = max(0.0, float(auto_points_by_team[idx - 1] or 0.0) + float(auto_tower_points))
        teleop_points_value = max(0.0, float(teleop_points_by_team[idx - 1] or 0.0))
        rows.append(
            {
                "team_key": team_key,
                "alliance": alliance,
                "station": f"{alliance[0]}{idx}",
                "auto_points": auto_points_value,
                "teleop_points": teleop_points_value,
                "teleop_score_count": (
                    float(teleop_count_by_team[idx - 1]) if teleop_count_by_team and len(teleop_count_by_team) >= idx else None
                ),
                "climb_points": float(endgame_points),
                "climb_success": has_endgame_result,
                "status": {
                    "auto_tower": auto_status,
                    "endgame_tower": endgame_status,
                    "hub_shift_points": hub_shift_points,
                    "hub_shift_counts": hub_shift_counts,
                    "teleop_count_capped_to_points": teleop_count_capped_to_points,
                    "teleop_allocation": (
                        "weighted_team_prior"
                        if isinstance(team_weight_by_key, dict) and any(
                            _as_float(team_weight_by_key.get(team_key)) not in (None, 0.0)
                            for team_key in team_keys
                        )
                        else "equal_split"
                    ),
                },
            }
        )
    return rows

def _parse_2025_alliance_truth(
    *,
    team_keys: list[str],
    alliance: str,
    breakdown: dict,
    team_weight_by_key: dict[str, float] | None = None,
) -> list[dict]:
    count = len(team_keys)
    if count == 0:
        return []

    auto_mobility_points = _as_float(breakdown.get("autoMobilityPoints")) or 0.0
    auto_coral_points = _as_float(breakdown.get("autoCoralPoints")) or 0.0
    teleop_points = _as_float(breakdown.get("teleopPoints")) or 0.0
    teleop_count = _sum_optional(
        _as_float(breakdown.get("teleopCoralCount")),
        _as_float(breakdown.get("netAlgaeCount")),
        _as_float(breakdown.get("wallAlgaeCount")),
    )

    auto_line_flags: list[bool] = []
    for idx in range(1, count + 1):
        auto_line_flags.append(_is_yes(breakdown.get(f"autoLineRobot{idx}")))
    auto_line_yes = max(1, sum(1 for value in auto_line_flags if value))
    mobility_points_if_yes = auto_mobility_points / float(auto_line_yes)
    auto_coral_share = auto_coral_points / float(count)
    teleop_points_by_team = _share_weighted(teleop_points, team_keys, team_weight_by_key)
    teleop_count_by_team = (
        _share_weighted(float(teleop_count), team_keys, team_weight_by_key)
        if teleop_count is not None
        else []
    )

    rows: list[dict] = []
    for idx, team_key in enumerate(team_keys, start=1):
        endgame_status = breakdown.get(f"endGameRobot{idx}")
        endgame_points = _reefscape_endgame_points_2025(endgame_status)
        status_token = str(endgame_status or "").strip().lower()
        climb_success = status_token in {"deepcage", "shallowcage"}
        rows.append(
            {
                "team_key": team_key,
                "alliance": alliance,
                "station": f"{alliance[0]}{idx}",
                "auto_points": max(0.0, auto_coral_share + (mobility_points_if_yes if auto_line_flags[idx - 1] else 0.0)),
                "teleop_points": max(0.0, float(teleop_points_by_team[idx - 1] or 0.0)),
                "teleop_score_count": (
                    float(teleop_count_by_team[idx - 1])
                    if teleop_count_by_team and len(teleop_count_by_team) >= idx
                    else None
                ),
                "climb_points": endgame_points,
                "climb_success": climb_success,
                "status": {
                    "auto_line": "Yes" if auto_line_flags[idx - 1] else "No",
                    "endgame": endgame_status,
                },
            }
        )
    return rows

def _parse_generic_alliance_truth(
    *,
    team_keys: list[str],
    alliance: str,
    breakdown: dict,
    context: dict[str, dict[str, float]],
    team_weight_by_key: dict[str, float] | None = None,
) -> list[dict]:
    count = len(team_keys)
    if count == 0:
        return []
    auto_points = _coalesce_float(
        _as_float(breakdown.get("totalAutoPoints")),
        _as_float(breakdown.get("autoPoints")),
    )
    teleop_points = _coalesce_float(
        _as_float(breakdown.get("totalTeleopPoints")),
        _as_float(breakdown.get("teleopPoints")),
    )
    auto_points_by_team = _share_evenly(auto_points, count)
    teleop_points_by_team = _share_weighted(teleop_points, team_keys, team_weight_by_key)

    rows: list[dict] = []
    for idx, team_key in enumerate(team_keys, start=1):
        raw_endgame_status = breakdown.get(f"endGameRobot{idx}")
        if raw_endgame_status is None:
            raw_endgame_status = breakdown.get(f"endgameRobot{idx}")
        climb_points = _tower_points_from_status(
            raw_endgame_status,
            auto_phase=False,
            scoring_points=context["scoring_points"],
        )
        token = str(raw_endgame_status or "").strip().lower()
        climb_success = climb_points > 0.0 and "park" not in token
        rows.append(
            {
                "team_key": team_key,
                "alliance": alliance,
                "station": f"{alliance[0]}{idx}",
                "auto_points": max(0.0, float(auto_points_by_team[idx - 1] or 0.0)),
                "teleop_points": max(0.0, float(teleop_points_by_team[idx - 1] or 0.0)),
                "teleop_score_count": None,
                "climb_points": float(climb_points),
                "climb_success": climb_success,
                "status": {
                    "endgame": raw_endgame_status,
                },
            }
        )
    return rows

def _extract_score_breakdown_truth_rows(
    *,
    match: dict,
    season_year: int,
    context: dict[str, dict[str, float]],
    team_weight_by_key: dict[str, float] | None = None,
) -> list[dict]:
    alliances = match.get("alliances")
    score_breakdown = match.get("score_breakdown")
    if not isinstance(alliances, dict) or not isinstance(score_breakdown, dict):
        return []

    rows: list[dict] = []
    for alliance in ("red", "blue"):
        alliance_payload = alliances.get(alliance)
        if not isinstance(alliance_payload, dict):
            continue
        team_keys = [
            str(team_key)
            for team_key in (alliance_payload.get("team_keys") or [])
            if isinstance(team_key, str) and team_key
        ]
        breakdown = score_breakdown.get(alliance)
        if not team_keys or not isinstance(breakdown, dict):
            continue

        if season_year >= 2026:
            rows.extend(
                _parse_2026_alliance_truth(
                    team_keys=team_keys,
                    alliance=alliance,
                    breakdown=breakdown,
                    context=context,
                    team_weight_by_key=team_weight_by_key,
                )
            )
        elif season_year == 2025:
            rows.extend(
                _parse_2025_alliance_truth(
                    team_keys=team_keys,
                    alliance=alliance,
                    breakdown=breakdown,
                    team_weight_by_key=team_weight_by_key,
                )
            )
        else:
            rows.extend(
                _parse_generic_alliance_truth(
                    team_keys=team_keys,
                    alliance=alliance,
                    breakdown=breakdown,
                    context=context,
                    team_weight_by_key=team_weight_by_key,
                )
            )
    return rows
