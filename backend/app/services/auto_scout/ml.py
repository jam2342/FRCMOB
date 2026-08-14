from __future__ import annotations

import math
from typing import Any

from app.db import models
from app.services.game_config import load_game_config


AUTO_SCOUT_FIELD_MODEL_PREFIX = "auto_scout_field"

AUTO_SCOUT_ROUND2_ML_FIELDS: tuple[str, ...] = (
    "teleop_under_defense_scored",
    "offense_level_1_5",
    "defense_level_1_5",
    "field_awareness_1_5",
    "decision_quality_1_5",
    "intake_failures",
)

AUTO_SCOUT_FIELD_FEATURE_ORDER: list[str] = [
    "season_year",
    "track_coverage_0_1",
    "throughput_coverage_0_1",
    "tracking_quality_0_1",
    "overall_quality_0_1",
    "event_rows",
    "track_rows",
    "auto_mobility_count",
    "auto_score_success_count",
    "auto_score_attempt_count",
    "teleop_score_success_count",
    "teleop_score_attempt_count",
    "depot_intake_count",
    "protected_zone_interference_count",
    "climb_attempt_count",
    "climb_success_count",
    "auto_contribution",
    "cycle_time_sec",
    "climb_success_prob",
    "defensive_engagement_sec",
    "reliability_score",
    "balls_shot_total",
    "shooting_time_total_seconds",
    "bps_total",
    "balls_shot_active",
    "shooting_time_active_seconds",
    "active_bps",
    "zone_entropy_0_1",
    "disabled_seconds",
    "teleop_window_sec",
    "auto_window_sec",
    "teleop_score_estimate",
    "under_defense_pressure_0_1",
]

AUTO_SCOUT_FIELD_TARGET_BOUNDS: dict[str, tuple[float, float]] = {
    "teleop_under_defense_scored": (0.0, 25.0),
    "offense_level_1_5": (1.0, 5.0),
    "defense_level_1_5": (1.0, 5.0),
    "field_awareness_1_5": (1.0, 5.0),
    "decision_quality_1_5": (1.0, 5.0),
    "intake_failures": (0.0, 20.0),
}


def normalize_auto_scout_field_name(field_name: object) -> str:
    return str(field_name or "").strip().lower()


def auto_scout_field_model_key(field_name: object) -> str:
    return f"{AUTO_SCOUT_FIELD_MODEL_PREFIX}:{normalize_auto_scout_field_name(field_name)}"


def auto_scout_field_scope(field_name: object) -> str:
    return auto_scout_field_model_key(field_name)


def _safe_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _match_total_sec() -> float:
    try:
        return float(load_game_config().phases.total_sec)
    except Exception:
        return 160.0


def _match_auto_sec() -> float:
    try:
        return float(load_game_config().phases.auto_sec)
    except Exception:
        return 20.0


def _event_type(event: models.MatchEvent) -> str:
    return str(event.event_type or "").strip().lower()


def _event_count_value(event: models.MatchEvent) -> int:
    count_from_meta = _safe_float((event.meta or {}).get("count_estimate"))
    if count_from_meta is None or count_from_meta <= 0:
        return 1
    return max(1, int(round(count_from_meta)))


def _event_count(
    events: list[models.MatchEvent],
    accepted: set[str],
    *,
    before_or_at_sec: float | None = None,
    after_sec: float | None = None,
) -> int:
    total = 0
    for event in events:
        if _event_type(event) not in accepted:
            continue
        event_time = float(event.time_sec)
        if before_or_at_sec is not None and event_time > float(before_or_at_sec):
            continue
        if after_sec is not None and event_time <= float(after_sec):
            continue
        total += _event_count_value(event)
    return int(total)


def _zone_entropy(tracks: list[models.RobotTrack]) -> float:
    if len(tracks) < 2:
        return 0.0
    rows = sorted(tracks, key=lambda row: (float(row.time_sec), int(row.id)))
    dwell: dict[str, float] = {}
    for index in range(1, len(rows)):
        previous = rows[index - 1]
        current = rows[index]
        zone = str(previous.zone_key or "").strip().lower()
        if not zone:
            continue
        dt = max(0.0, min(1.0, float(current.time_sec) - float(previous.time_sec)))
        if dt <= 0:
            continue
        dwell[zone] = float(dwell.get(zone, 0.0)) + dt
    total = sum(dwell.values())
    if total <= 1e-9 or len(dwell) <= 1:
        return 0.0
    entropy = 0.0
    for duration in dwell.values():
        probability = float(duration) / float(total)
        if probability <= 0.0:
            continue
        entropy += -(probability * math.log(probability + 1e-12))
    max_entropy = math.log(float(len(dwell)))
    if max_entropy <= 1e-9:
        return 0.0
    return _clamp(entropy / max_entropy, 0.0, 1.0)


def _disabled_seconds(tracks: list[models.RobotTrack], *, threshold_speed: float = 0.18) -> float:
    if len(tracks) < 2:
        return 0.0
    rows = sorted(tracks, key=lambda row: (float(row.time_sec), int(row.id)))
    total = 0.0
    start_time: float | None = None
    last_time: float | None = None
    for row in rows:
        speed = _safe_float(row.speed_mps)
        now = float(row.time_sec)
        slow = speed is not None and speed <= threshold_speed
        if slow and start_time is None:
            start_time = now
        if (not slow) and start_time is not None and last_time is not None:
            total += max(0.0, last_time - start_time)
            start_time = None
        last_time = now
    if start_time is not None and last_time is not None:
        total += max(0.0, last_time - start_time)
    return max(0.0, total)


def build_auto_scout_field_feature_vector(
    *,
    season_year: int,
    coverage: float,
    throughput_coverage: float | None,
    events: list[models.MatchEvent],
    tracks: list[models.RobotTrack],
    finding: models.TeamMatchFinding | None,
    throughput: models.TeamMatchThroughput | None,
    quality: models.AnalysisQuality | None,
) -> dict[str, float]:
    auto_sec = _match_auto_sec()
    total_sec = _match_total_sec()
    teleop_window = max(1.0, total_sec - auto_sec)

    auto_mobility_count = _event_count(events, {"auto_mobility"}, before_or_at_sec=auto_sec)
    auto_success_count = _event_count(events, {"auto_fuel_score_success"}, before_or_at_sec=auto_sec)
    auto_attempt_count = _event_count(events, {"auto_fuel_score_attempt"}, before_or_at_sec=auto_sec)
    teleop_success_count = _event_count(events, {"teleop_fuel_score_success"}, after_sec=auto_sec)
    teleop_attempt_count = _event_count(events, {"teleop_fuel_score_attempt"}, after_sec=auto_sec)
    intake_count = _event_count(events, {"depot_intake"}, after_sec=auto_sec)
    protected_count = _event_count(events, {"protected_zone_interference"})
    climb_attempt_count = _event_count(events, {"climb_attempt"})
    climb_success_count = _event_count(events, {"climb_success"})

    auto_contribution = _safe_float(getattr(finding, "auto_contribution", None)) or 0.0
    cycle_time_sec = _safe_float(getattr(finding, "cycle_time_sec", None)) or 0.0
    climb_success_prob = _safe_float(getattr(finding, "climb_success_prob", None)) or 0.0
    defensive_engagement_sec = _safe_float(getattr(finding, "defensive_engagement_sec", None)) or 0.0
    reliability_score = _safe_float(getattr(finding, "reliability_score", None)) or 0.0

    balls_shot_total = _safe_float(getattr(throughput, "balls_shot_total", None)) or 0.0
    shooting_time_total_seconds = _safe_float(getattr(throughput, "shooting_time_total_seconds", None)) or 0.0
    bps_total = _safe_float(getattr(throughput, "bps_total", None)) or 0.0
    balls_shot_active = _safe_float(getattr(throughput, "balls_shot_active", None)) or 0.0
    shooting_time_active_seconds = _safe_float(getattr(throughput, "shooting_time_active_seconds", None)) or 0.0
    active_bps = _safe_float(getattr(throughput, "active_bps", None)) or 0.0

    tracking_quality = _safe_float(getattr(quality, "tracking_quality_score", None)) or 0.0
    overall_quality = _safe_float(getattr(quality, "overall_quality_score", None)) or 0.0

    coverage_value = _clamp(float(coverage), 0.0, 1.0)
    throughput_coverage_value = (
        _clamp(float(throughput_coverage), 0.0, 1.0)
        if throughput_coverage is not None
        else 0.0
    )
    under_defense_pressure = _clamp(defensive_engagement_sec / max(1.0, teleop_window), 0.0, 1.0)

    feature_vector = {
        "season_year": float(int(season_year)),
        "track_coverage_0_1": coverage_value,
        "throughput_coverage_0_1": throughput_coverage_value,
        "tracking_quality_0_1": _clamp(tracking_quality, 0.0, 1.0),
        "overall_quality_0_1": _clamp(overall_quality, 0.0, 1.0),
        "event_rows": float(len(events)),
        "track_rows": float(len(tracks)),
        "auto_mobility_count": float(auto_mobility_count),
        "auto_score_success_count": float(auto_success_count),
        "auto_score_attempt_count": float(auto_attempt_count),
        "teleop_score_success_count": float(teleop_success_count),
        "teleop_score_attempt_count": float(teleop_attempt_count),
        "depot_intake_count": float(intake_count),
        "protected_zone_interference_count": float(protected_count),
        "climb_attempt_count": float(climb_attempt_count),
        "climb_success_count": float(climb_success_count),
        "auto_contribution": float(auto_contribution),
        "cycle_time_sec": float(max(0.0, cycle_time_sec)),
        "climb_success_prob": _clamp(float(climb_success_prob), 0.0, 1.0),
        "defensive_engagement_sec": float(max(0.0, defensive_engagement_sec)),
        "reliability_score": _clamp(float(reliability_score), 0.0, 1.0),
        "balls_shot_total": float(max(0.0, balls_shot_total)),
        "shooting_time_total_seconds": float(max(0.0, shooting_time_total_seconds)),
        "bps_total": float(max(0.0, bps_total)),
        "balls_shot_active": float(max(0.0, balls_shot_active)),
        "shooting_time_active_seconds": float(max(0.0, shooting_time_active_seconds)),
        "active_bps": float(max(0.0, active_bps)),
        "zone_entropy_0_1": float(_zone_entropy(tracks)),
        "disabled_seconds": float(_disabled_seconds(tracks)),
        "teleop_window_sec": float(teleop_window),
        "auto_window_sec": float(auto_sec),
        "teleop_score_estimate": float(max(0.0, balls_shot_total - auto_success_count)),
        "under_defense_pressure_0_1": float(under_defense_pressure),
    }

    return {
        name: float(_safe_float(feature_vector.get(name)) or 0.0)
        for name in AUTO_SCOUT_FIELD_FEATURE_ORDER
    }


def auto_scout_field_target_to_float(field_name: object, value: object) -> float | None:
    normalized = normalize_auto_scout_field_name(field_name)
    if normalized == "teleop_under_defense_scored":
        numeric = _safe_float(value)
        return float(max(0.0, numeric)) if numeric is not None else None
    if normalized in {"offense_level_1_5", "defense_level_1_5", "field_awareness_1_5", "decision_quality_1_5"}:
        numeric = _safe_float(value)
        if numeric is None:
            return None
        return float(int(round(_clamp(numeric, 1.0, 5.0))))
    if normalized == "intake_failures":
        numeric = _safe_float(value)
        return float(max(0.0, numeric)) if numeric is not None else None
    return _safe_float(value)


def clamp_auto_scout_field_prediction(field_name: object, value: float | None) -> float | None:
    if value is None:
        return None
    normalized = normalize_auto_scout_field_name(field_name)
    bounds = AUTO_SCOUT_FIELD_TARGET_BOUNDS.get(normalized)
    if bounds is None:
        return float(value)
    return _clamp(float(value), float(bounds[0]), float(bounds[1]))


def present_auto_scout_field_prediction(field_name: object, value: float | None) -> Any:
    clipped = clamp_auto_scout_field_prediction(field_name, value)
    if clipped is None:
        return None
    normalized = normalize_auto_scout_field_name(field_name)
    if normalized in {"offense_level_1_5", "defense_level_1_5", "field_awareness_1_5", "decision_quality_1_5"}:
        return int(round(_clamp(float(clipped), 1.0, 5.0)))
    return int(round(max(0.0, float(clipped))))
