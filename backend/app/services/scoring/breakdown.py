# Score-breakdown truth extraction and cycle-time fallback helpers.
#
# Extracted from jobs.py to reduce module size.
from __future__ import annotations

import math
from collections import defaultdict
from contextlib import suppress
from typing import Any

from app.core.config import settings
from app.db import models
from app.services.events.classifier import ENDGAME_ZONE_KEYWORDS
from app.services.season_config import (
    REBUILT_ALLIANCE_SHIFT_COUNT,
    REBUILT_ALLIANCE_SHIFT_SEC,
    REBUILT_ENDGAME_WINDOW_SEC,
    REBUILT_HUB_SCORE_GRACE_SEC,
    REBUILT_TRANSITION_SHIFT_SEC,
)
from app.services.utils import _as_float as _as_number, _clamp, _round
from app.tba.client import TBAClient

DEFAULT_MAX_SAMPLE_FRAMES = 120
DEFAULT_MAX_ARTIFACT_SAMPLE_FRAMES = 30
MAX_TRACK_MATCH_DISTANCE_PX = 110.0
MAX_TRACK_GAP_FRAMES = 2
MIN_TRACK_OBS_FOR_ASSIGNMENT = 4
DEFAULT_ANALYSIS_VERSION = "video_v3_tracks"
RUN_ACTIVE_OR_DONE_STATUSES = {"running", "completed"}
SCORE_BREAKDOWN_SOURCE = "tba_score_breakdown"
DEFAULT_GAME_PIECE_POINTS_PER_SCORE = 1.0  # REBUILT 2026: each fuel scored = 1 point (Manual Table 6-4)

def _is_endgame_zone(zone_key: object, zone_kind: object) -> bool:
    key = str(zone_key or "").strip().lower()
    kind = str(zone_kind or "").strip().lower()
    if kind == "endgame":
        return True
    return any(keyword in key for keyword in ENDGAME_ZONE_KEYWORDS)

def _first_positive_number(*values: object) -> float | None:
    for value in values:
        parsed = _as_number(value)
        if parsed is not None and parsed > 0.0:
            return float(parsed)
    return None

def _sum_positive_keys(payload: dict[str, Any], keys: list[str]) -> float | None:
    total = 0.0
    seen = False
    for key in keys:
        value = _as_number(payload.get(key))
        if value is None or value <= 0.0:
            continue
        total += float(value)
        seen = True
    return total if seen else None

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

def _merge_time_windows(
    windows: list[tuple[float, float]],
    *,
    floor_sec: float = 0.0,
    ceil_sec: float | None = None,
) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []
    for start_raw, end_raw in windows:
        start = max(float(floor_sec), float(start_raw))
        end = float(end_raw)
        if ceil_sec is not None:
            end = min(float(ceil_sec), end)
        if end <= start:
            continue
        normalized.append((start, end))
    if not normalized:
        return []
    normalized.sort(key=lambda item: item[0])
    merged: list[tuple[float, float]] = [normalized[0]]
    for start, end in normalized[1:]:
        prev_start, prev_end = merged[-1]
        if start <= (prev_end + 1e-6):
            merged[-1] = (prev_start, max(prev_end, end))
            continue
        merged.append((start, end))
    return merged

def _window_duration_sec(windows: list[tuple[float, float]] | None) -> float:
    if not windows:
        return 0.0
    return max(0.0, float(sum(max(0.0, end - start) for start, end in windows)))

def _rebuilt_expected_active_hub_duration_sec(
    *,
    auto_end_sec: float,
    duration_sec: float,
    include_post_deactivate_grace: bool = True,
) -> float:
    schedule = _rebuilt_hub_activity_windows_by_alliance(
        auto_end_sec=auto_end_sec,
        duration_sec=duration_sec,
        shift1_active_alliance="red",
    )
    duration_key = (
        "attributed_duration_by_alliance"
        if include_post_deactivate_grace
        else "nominal_duration_by_alliance"
    )
    duration_by_alliance = (
        schedule.get(duration_key)
        if isinstance(schedule.get(duration_key), dict)
        else {}
    )
    values = [
        float(value)
        for value in duration_by_alliance.values()
        if isinstance(value, (int, float)) and float(value) > 0.0
    ]
    if values:
        return max(1e-6, float(sum(values) / len(values)))
    return max(1e-6, float(max(0.0, duration_sec - auto_end_sec)))

def _time_in_windows(time_sec: float, windows: list[tuple[float, float]] | None) -> bool:
    if not windows:
        return True
    stamp = float(time_sec)
    for start_sec, end_sec in windows:
        if start_sec <= stamp <= end_sec:
            return True
    return False

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

def _rebuilt_hub_activity_windows_by_alliance(
    *,
    auto_end_sec: float,
    duration_sec: float,
    shift1_active_alliance: str | None,
) -> dict[str, Any]:
    auto_end = max(0.0, float(auto_end_sec))
    match_end = max(auto_end, float(duration_sec))
    endgame_start = _clamp(match_end - REBUILT_ENDGAME_WINDOW_SEC, auto_end, match_end)
    transition_end = _clamp(auto_end + REBUILT_TRANSITION_SHIFT_SEC, auto_end, endgame_start)

    shift_windows: list[tuple[int, float, float]] = []
    shift_cursor = transition_end
    for shift_idx in range(1, REBUILT_ALLIANCE_SHIFT_COUNT + 1):
        shift_end = _clamp(shift_cursor + REBUILT_ALLIANCE_SHIFT_SEC, shift_cursor, endgame_start)
        shift_windows.append((shift_idx, shift_cursor, shift_end))
        shift_cursor = shift_end

    segments: list[dict[str, Any]] = [
        {"key": "transition", "start": auto_end, "end": transition_end},
    ]
    for shift_idx, start_sec, end_sec in shift_windows:
        segments.append(
            {
                "key": f"shift_{shift_idx}",
                "start": start_sec,
                "end": end_sec,
                "shift_index": shift_idx,
            }
        )
    segments.append({"key": "endgame", "start": endgame_start, "end": match_end})

    def _shift_is_active_for(alliance: str, shift_index: int) -> bool:
        if shift1_active_alliance not in {"red", "blue"}:
            return True
        if shift1_active_alliance == alliance:
            return shift_index % 2 == 1
        return shift_index % 2 == 0

    windows_nominal_by_alliance: dict[str, list[tuple[float, float]]] = {"red": [], "blue": []}
    windows_attributed_by_alliance: dict[str, list[tuple[float, float]]] = {"red": [], "blue": []}
    for alliance in ("red", "blue"):
        active_flags: list[bool] = []
        for segment in segments:
            key = str(segment.get("key") or "")
            if key in {"transition", "endgame"}:
                active_flags.append(True)
                continue
            shift_index = int(segment.get("shift_index") or 0)
            active_flags.append(_shift_is_active_for(alliance, shift_index))

        for idx, segment in enumerate(segments):
            if not active_flags[idx]:
                continue
            start = float(segment["start"])
            end = float(segment["end"])
            if end <= start:
                continue
            windows_nominal_by_alliance[alliance].append((start, end))

            next_active = active_flags[idx + 1] if idx + 1 < len(active_flags) else False
            extended_end = end
            if not next_active:
                extended_end += REBUILT_HUB_SCORE_GRACE_SEC
            windows_attributed_by_alliance[alliance].append((start, extended_end))

    max_attr_end = match_end + REBUILT_HUB_SCORE_GRACE_SEC
    for alliance in ("red", "blue"):
        windows_nominal_by_alliance[alliance] = _merge_time_windows(
            windows_nominal_by_alliance[alliance],
            floor_sec=auto_end,
            ceil_sec=match_end,
        )
        windows_attributed_by_alliance[alliance] = _merge_time_windows(
            windows_attributed_by_alliance[alliance],
            floor_sec=auto_end,
            ceil_sec=max_attr_end,
        )

    nominal_durations = {
        alliance: _round(_window_duration_sec(windows_nominal_by_alliance[alliance]), 3)
        for alliance in ("red", "blue")
    }
    attributed_durations = {
        alliance: _round(_window_duration_sec(windows_attributed_by_alliance[alliance]), 3)
        for alliance in ("red", "blue")
    }

    return {
        "shift1_active_alliance": shift1_active_alliance if shift1_active_alliance in {"red", "blue"} else None,
        "windows_nominal_by_alliance": windows_nominal_by_alliance,
        "windows_attributed_by_alliance": windows_attributed_by_alliance,
        "nominal_duration_by_alliance": nominal_durations,
        "attributed_duration_by_alliance": attributed_durations,
    }

def _resolve_rebuilt_hub_activity_payload(
    *,
    match_key: str,
    auto_end_sec: float,
    duration_sec: float,
    match_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = "manual_schedule_default"
    shift1_active_alliance: str | None = None
    score_breakdown_payload = match_payload if isinstance(match_payload, dict) else None

    if score_breakdown_payload is None and str(settings.tba_auth_key or "").strip():
        try:
            tba = TBAClient()
            payload = tba.match(match_key)
            if isinstance(payload, dict):
                score_breakdown_payload = payload
                source = "tba_score_breakdown_api"
        except Exception:
            source = "manual_schedule_tba_unavailable"
    elif score_breakdown_payload is None:
        source = "manual_schedule_no_tba_key"
    else:
        source = "tba_score_breakdown_preloaded"

    if isinstance(score_breakdown_payload, dict):
        inferred_alliance, inferred_source = _infer_rebuilt_shift1_active_alliance(score_breakdown_payload)
        if inferred_alliance in {"red", "blue"}:
            shift1_active_alliance = inferred_alliance
            source = f"{source}:{inferred_source}"
        else:
            source = f"{source}:{inferred_source}"

    payload = _rebuilt_hub_activity_windows_by_alliance(
        auto_end_sec=auto_end_sec,
        duration_sec=duration_sec,
        shift1_active_alliance=shift1_active_alliance,
    )
    payload["source"] = source
    return payload

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

def _teleop_count_and_points_from_breakdown(breakdown: dict[str, Any]) -> tuple[float | None, float | None, str]:
    hub = breakdown.get("hubScore") if isinstance(breakdown.get("hubScore"), dict) else {}

    hub_shift_count_sum = _sum_positive_keys(
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
    teleop_count = _first_positive_number(
        hub.get("teleopCount"),
        hub_shift_count_sum,
        hub.get("totalCount"),
        _sum_positive_keys(breakdown, ["teleopCoralCount", "netAlgaeCount", "wallAlgaeCount"]),
        breakdown.get("teleopCount"),
    )

    total_teleop_points = _as_number(breakdown.get("totalTeleopPoints"))
    endgame_tower_points = _as_number(breakdown.get("endGameTowerPoints"))
    teleop_minus_endgame = (
        max(0.0, float(total_teleop_points) - float(endgame_tower_points))
        if total_teleop_points is not None and endgame_tower_points is not None
        else None
    )
    hub_shift_points_sum = _sum_positive_keys(
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
    teleop_points = _first_positive_number(
        hub.get("teleopPoints"),
        teleop_minus_endgame,
        breakdown.get("teleopPoints"),
        hub_shift_points_sum,
        total_teleop_points,
    )

    if teleop_count is not None:
        count_source = "official_count"
        if teleop_points is not None and teleop_points > 0.0 and teleop_count > (teleop_points * 1.05):
            # 2026 Manual 6.5.3: active-HUB fuel is 1 point. If count exceeds points,
            # the count likely includes inactive-HUB attempts and should not drive rate/cycle.
            teleop_count = float(teleop_points)
            count_source = "official_count_capped_to_points"
        return teleop_count, teleop_points, count_source
    if teleop_points is not None:
        return None, teleop_points, "official_points"
    return None, None, "none"

def _score_breakdown_cycle_fallbacks_from_match_payload(
    *,
    match_payload: dict[str, Any],
    teleop_duration_sec: float,
    team_weight_by_key: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    alliances = match_payload.get("alliances")
    score_breakdown = match_payload.get("score_breakdown")
    if not isinstance(alliances, dict) or not isinstance(score_breakdown, dict):
        return {}

    fallbacks: dict[str, dict[str, Any]] = {}
    points_per_score = max(0.5, float(DEFAULT_GAME_PIECE_POINTS_PER_SCORE))
    teleop_duration_safe = max(1e-6, float(teleop_duration_sec))

    for alliance in ("red", "blue"):
        alliance_payload = alliances.get(alliance)
        breakdown = score_breakdown.get(alliance)
        if not isinstance(alliance_payload, dict) or not isinstance(breakdown, dict):
            continue
        team_keys = [
            str(team_key)
            for team_key in (alliance_payload.get("team_keys") or [])
            if isinstance(team_key, str) and team_key
        ]
        if not team_keys:
            continue

        teleop_count_alliance, teleop_points_alliance, count_source = _teleop_count_and_points_from_breakdown(breakdown)
        team_weights: list[float] = []
        for team_key in team_keys:
            candidate = _as_number((team_weight_by_key or {}).get(team_key))
            team_weights.append(max(0.05, float(candidate)) if candidate is not None and candidate > 0.0 else 1.0)
        weight_sum = max(1e-6, float(sum(team_weights)))

        count_shares: list[float | None]
        points_shares: list[float | None]
        if teleop_count_alliance is not None and teleop_count_alliance > 0.0:
            count_shares = [
                max(0.0, (float(teleop_count_alliance) * (weight / weight_sum)))
                for weight in team_weights
            ]
        else:
            count_shares = [None for _ in team_keys]

        if teleop_points_alliance is not None and teleop_points_alliance > 0.0:
            points_shares = [
                max(0.0, (float(teleop_points_alliance) * (weight / weight_sum)))
                for weight in team_weights
            ]
        else:
            points_shares = [None for _ in team_keys]

        if all((share is None or share <= 0.0) for share in count_shares):
            if any((share is not None and share > 0.0) for share in points_shares):
                count_shares = [
                    (
                        max(0.0, float(share) / points_per_score)
                        if share is not None and share > 0.0
                        else None
                    )
                    for share in points_shares
                ]
                count_source = "official_points_proxy"
        if all((share is None or share <= 0.0) for share in count_shares):
            continue

        confidence = 0.9 if count_source == "official_count" else 0.72
        allocation_method = (
            "weighted_team_prior"
            if any((_as_number((team_weight_by_key or {}).get(team_key)) or 0.0) > 0.0 for team_key in team_keys)
            else "equal_split"
        )
        for idx, team_key in enumerate(team_keys):
            count_share = count_shares[idx] if idx < len(count_shares) else None
            points_share = points_shares[idx] if idx < len(points_shares) else None
            if count_share is None or count_share <= 0.0:
                continue
            cycle_time_sec = teleop_duration_safe / max(1e-6, float(count_share))
            fuel_rate = (float(count_share) / teleop_duration_safe) * 60.0
            fallbacks[team_key] = {
                "cycle_time_sec": _round(_clamp(float(cycle_time_sec), 1.0, 160.0), 3),
                "fuel_scoring_rate": _round(max(0.0, float(fuel_rate)), 3),
                "score_count_estimate": _round(max(0.0, float(count_share)), 3),
                "teleop_points_estimate": _round(max(0.0, float(points_share)), 3) if points_share is not None else None,
                "source": "tba_score_breakdown_api",
                "count_source": count_source,
                "allocation_method": allocation_method,
                "allocation_weight": _round(team_weights[idx], 3) if idx < len(team_weights) else None,
                "confidence_0_1": _round(confidence, 3),
                "excludes": ["climb", "penalties"],
            }
    return fallbacks

def _load_local_score_breakdown_cycle_fallbacks(
    db,
    *,
    match_key: str,
    teleop_duration_sec: float,
) -> dict[str, dict[str, Any]]:
    rows = (
        db.query(models.TeamMatchFinding)
        .filter(
            models.TeamMatchFinding.match_key == match_key,
            models.TeamMatchFinding.source == SCORE_BREAKDOWN_SOURCE,
        )
        .all()
    )
    if not rows:
        return {}

    fallbacks: dict[str, dict[str, Any]] = {}
    teleop_duration_safe = max(1e-6, float(teleop_duration_sec))
    for row in rows:
        cycle_time = _as_number(row.cycle_time_sec)
        fuel_rate = _as_number(row.fuel_scoring_rate)
        if cycle_time is None and fuel_rate is not None and fuel_rate > 0.0:
            cycle_time = 60.0 / max(1e-6, float(fuel_rate))
        if cycle_time is None or cycle_time <= 0.0:
            continue
        if fuel_rate is None and cycle_time > 0.0:
            fuel_rate = 60.0 / float(cycle_time)
        score_count = teleop_duration_safe / max(1e-6, float(cycle_time))
        fallbacks[str(row.team_key)] = {
            "cycle_time_sec": _round(_clamp(float(cycle_time), 1.0, 160.0), 3),
            "fuel_scoring_rate": _round(max(0.0, float(fuel_rate or 0.0)), 3),
            "score_count_estimate": _round(max(0.0, float(score_count)), 3),
            "teleop_points_estimate": None,
            "source": "score_breakdown_local_cache",
            "count_source": "local_cycle_or_rate",
            "confidence_0_1": 0.84,
            "excludes": ["climb", "penalties"],
        }
    return fallbacks

def _effective_fuel_rate_from_row(
    *,
    fuel_scoring_rate: object,
    cycle_time_sec: object,
) -> float | None:
    rate = _as_number(fuel_scoring_rate)
    cycle_time = _as_number(cycle_time_sec)
    cycle_implied = (
        (60.0 / max(1e-6, float(cycle_time)))
        if cycle_time is not None and cycle_time > 0.0
        else None
    )
    if rate is None and cycle_implied is None:
        return None
    if rate is None:
        return float(cycle_implied)
    if cycle_implied is None:
        return max(0.0, float(rate))
    return max(0.0, float(rate), float(cycle_implied))

def _team_fuel_strength_weights(
    db,
    *,
    match: models.Match,
    expected_team_keys: set[str],
) -> dict[str, float]:
    if not expected_team_keys:
        return {}

    rate_samples_by_team: dict[str, list[float]] = defaultdict(list)
    finding_rows_query = (
        db.query(
            models.TeamMatchFinding.team_key,
            models.TeamMatchFinding.fuel_scoring_rate,
            models.TeamMatchFinding.cycle_time_sec,
            models.Match.time,
            models.TeamMatchFinding.id,
        )
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .filter(
            models.TeamMatchFinding.team_key.in_(expected_team_keys),
            models.TeamMatchFinding.source != SCORE_BREAKDOWN_SOURCE,
            models.Match.event_key == match.event_key,
        )
    )
    if isinstance(match.time, int) and match.time > 0:
        finding_rows_query = finding_rows_query.filter(models.Match.time < int(match.time))
    finding_rows = (
        finding_rows_query
        .order_by(models.Match.time.desc().nullslast(), models.TeamMatchFinding.id.desc())
        .limit(max(60, len(expected_team_keys) * 12))
        .all()
    )
    for team_key, fuel_rate, cycle_time_sec, _match_time, _id in finding_rows:
        key = str(team_key or "")
        if not key:
            continue
        effective = _effective_fuel_rate_from_row(
            fuel_scoring_rate=fuel_rate,
            cycle_time_sec=cycle_time_sec,
        )
        if effective is None or effective <= 0.0:
            continue
        rate_samples_by_team[key].append(float(effective))

    priors: dict[str, float] = {}
    for team_key, samples in rate_samples_by_team.items():
        if not samples:
            continue
        weighted_sum = 0.0
        total_weight = 0.0
        for index, value in enumerate(samples[:8]):
            weight = max(0.35, 1.0 - (index * 0.12))
            weighted_sum += float(value) * weight
            total_weight += weight
        if total_weight > 0.0:
            priors[team_key] = weighted_sum / total_weight

    missing = [team_key for team_key in expected_team_keys if team_key not in priors]
    if missing:
        stat_rows = (
            db.query(models.EventTeamStat.team_key, models.EventTeamStat.opr)
            .filter(
                models.EventTeamStat.event_key == match.event_key,
                models.EventTeamStat.team_key.in_(missing),
            )
            .all()
        )
        for team_key, opr in stat_rows:
            key = str(team_key or "")
            numeric = _as_number(opr)
            if not key or numeric is None or numeric <= 0.0:
                continue
            priors[key] = max(0.2, min(20.0, float(numeric) * 0.08))

    weights: dict[str, float] = {}
    for team_key in expected_team_keys:
        prior = priors.get(team_key)
        if prior is None or prior <= 0.0:
            continue
        weights[team_key] = _round(_clamp(1.0 + math.log1p(float(prior)), 1.0, 4.5), 4)
    return weights

def _rebalance_cycle_fallbacks_by_alliance(
    *,
    match_teams: list[models.MatchTeam],
    fallback_by_team: dict[str, dict[str, Any]],
    teleop_duration_sec: float,
    team_weight_by_key: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    if not fallback_by_team:
        return {}

    adjusted: dict[str, dict[str, Any]] = {
        str(team_key): dict(payload)
        for team_key, payload in fallback_by_team.items()
        if isinstance(team_key, str) and isinstance(payload, dict)
    }
    alliance_team_keys: dict[str, list[str]] = defaultdict(list)
    for match_team in match_teams:
        team_key = str(match_team.team_key or "")
        alliance = str(match_team.alliance or "").strip().lower()
        if team_key and alliance in {"red", "blue"}:
            alliance_team_keys[alliance].append(team_key)

    teleop_duration_safe = max(1e-6, float(teleop_duration_sec))
    for alliance, team_keys in alliance_team_keys.items():
        present_team_keys = [team_key for team_key in team_keys if team_key in adjusted]
        if len(present_team_keys) <= 1:
            continue
        total_count = 0.0
        for team_key in present_team_keys:
            count_est = _as_number(adjusted[team_key].get("score_count_estimate"))
            if count_est is not None and count_est > 0.0:
                total_count += float(count_est)
        if total_count <= 0.0:
            continue

        total_points = 0.0
        has_points_total = False
        for team_key in present_team_keys:
            points_est = _as_number(adjusted[team_key].get("teleop_points_estimate"))
            if points_est is None or points_est <= 0.0:
                continue
            total_points += float(points_est)
            has_points_total = True

        weights: list[float] = []
        for team_key in present_team_keys:
            configured = _as_number((team_weight_by_key or {}).get(team_key))
            current_share = (
                max(0.0, float(_as_number(adjusted[team_key].get("score_count_estimate")) or 0.0)) / total_count
            )
            prior_weight = configured if configured is not None and configured > 0.0 else 1.0
            shape = _clamp(0.8 + (current_share * float(len(present_team_keys))), 0.7, 1.45)
            weights.append(max(0.08, float(prior_weight) * float(shape)))

        weight_sum = max(1e-6, float(sum(weights)))
        for idx, team_key in enumerate(present_team_keys):
            payload = adjusted[team_key]
            share = weights[idx] / weight_sum
            count_share = max(0.0, total_count * share)
            points_share = max(0.0, total_points * share) if has_points_total else None
            payload["score_count_estimate"] = _round(count_share, 3)
            payload["fuel_scoring_rate"] = _round(max(0.0, (count_share / teleop_duration_safe) * 60.0), 3)
            payload["cycle_time_sec"] = (
                _round(_clamp(teleop_duration_safe / max(1e-6, count_share), 1.0, 160.0), 3)
                if count_share > 0.0
                else None
            )
            payload["teleop_points_estimate"] = _round(points_share, 3) if points_share is not None else None
            payload["allocation_method"] = (
                "weighted_team_prior"
                if _as_number((team_weight_by_key or {}).get(team_key)) not in (None, 0.0)
                else str(payload.get("allocation_method") or "equal_split")
            )
            payload["allocation_weight"] = _round(weights[idx], 3)
            payload["alliance"] = alliance
    return adjusted

def _resolve_sparse_cycle_fallbacks(
    db,
    *,
    match: models.Match,
    match_teams: list[models.MatchTeam],
    teleop_duration_sec: float,
    match_payload: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    expected_team_keys = {str(match_team.team_key) for match_team in match_teams if match_team.team_key}
    if not expected_team_keys:
        return {}
    team_weight_by_key = _team_fuel_strength_weights(
        db,
        match=match,
        expected_team_keys=expected_team_keys,
    )

    fallback_by_team = _load_local_score_breakdown_cycle_fallbacks(
        db,
        match_key=match.match_key,
        teleop_duration_sec=teleop_duration_sec,
    )
    fallback_by_team = _rebalance_cycle_fallbacks_by_alliance(
        match_teams=match_teams,
        fallback_by_team=fallback_by_team,
        teleop_duration_sec=teleop_duration_sec,
        team_weight_by_key=team_weight_by_key,
    )
    missing = [team_key for team_key in expected_team_keys if team_key not in fallback_by_team]
    if not missing:
        return {team_key: payload for team_key, payload in fallback_by_team.items() if team_key in expected_team_keys}

    if not str(settings.tba_auth_key or "").strip() and not isinstance(match_payload, dict):
        return {team_key: payload for team_key, payload in fallback_by_team.items() if team_key in expected_team_keys}

    with suppress(Exception):
        payload = match_payload if isinstance(match_payload, dict) else _fetch_match_payload_from_tba(match.match_key)
        if isinstance(payload, dict):
            remote = _score_breakdown_cycle_fallbacks_from_match_payload(
                match_payload=payload,
                teleop_duration_sec=teleop_duration_sec,
                team_weight_by_key=team_weight_by_key,
            )
            for team_key in missing:
                if team_key in remote:
                    fallback_by_team[team_key] = remote[team_key]

    return {team_key: payload for team_key, payload in fallback_by_team.items() if team_key in expected_team_keys}

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

