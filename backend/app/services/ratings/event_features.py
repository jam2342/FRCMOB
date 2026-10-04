# Weighted scoring and penalty evidence collected from match events.
from __future__ import annotations

from dataclasses import dataclass
from app.db import models
from app.services.ratings.constants import PENALTY_EVENT_POINT_WEIGHTS
from app.services.ratings.helpers import _event_meta_number, _recent_weight_for_index, _trend_delta_ratio


@dataclass(frozen=True, slots=True)
class ScoringEvents:
    match_climb_points: list[tuple[float, float]]
    match_teleop_score_counts: list[tuple[float, float]]
    explicit_penalty_points_per_match: float
    major_like_penalty_events_per_match: float
    protected_zone_hits_per_match: float
    disabled_events_per_match: float
    penalty_trend_delta: float | None


def summarize_scoring_events(
    ordered_match_keys: list[str],
    team_match_events: dict[str, list[models.MatchEvent]],
    observed_match_keys: set[str],
    match_weight_by_key: dict[str, float],
    scoring_points: dict[str, float],
    phase_auto_sec: float,
) -> ScoringEvents:
    low_climb_points = float(scoring_points.get("low_climb", 10.0))
    mid_climb_points = float(scoring_points.get("mid_climb", 20.0))
    high_climb_points = float(scoring_points.get("high_climb", 30.0))
    auto_l1_points = float(scoring_points.get("auto_level1_climb", 15.0))
    # Match-by-match scoring projections.
    match_climb_points: list[tuple[float, float]] = []
    match_teleop_score_counts: list[tuple[float, float]] = []
    major_like_penalty_events = 0.0
    explicit_penalty_points = 0.0
    protected_zone_hits = 0.0
    disabled_events = 0.0
    explicit_penalty_points_by_match: list[float] = []
    weighted_observed_matches = 0.0

    for index, match_key in enumerate(ordered_match_keys):
        grouped_events = team_match_events.get(match_key, [])
        if not grouped_events and match_key not in observed_match_keys:
            continue
        weight = float(match_weight_by_key.get(match_key, _recent_weight_for_index(index)))
        weighted_observed_matches += weight
        match_climb_point = 0.0
        match_success_count = 0.0
        match_explicit_penalty_points = 0.0
        match_major_like_penalty_events = 0.0
        match_protected_zone_hits = 0.0
        match_disabled_events = 0.0
        for event in grouped_events:
            event_type = str(event.event_type or "").strip().lower()
            event_count_estimate = _event_meta_number(event, "count_estimate")
            event_count = max(0.0, event_count_estimate) if event_count_estimate is not None else 1.0
            event_penalty_points = PENALTY_EVENT_POINT_WEIGHTS.get(event_type, 0.0)
            match_explicit_penalty_points += event_penalty_points * event_count
            if event_type == "robot_disabled":
                match_disabled_events += 1.0
            if event_type == "protected_zone_interference":
                match_protected_zone_hits += event_count
            elif event_type == "zone_dwell":
                meta = event.meta or {}
                zone_key = str(meta.get("zone_key") or "").lower()
                if "protected" in zone_key:
                    match_protected_zone_hits += 0.5
            if event_type == "teleop_fuel_score_success":
                meta = event.meta or {}
                count_estimate = meta.get("count_estimate") if isinstance(meta, dict) else None
                if isinstance(count_estimate, (int, float)):
                    match_success_count += max(0.0, float(count_estimate))
                else:
                    match_success_count += 1.0
            if event_type in {"major_foul", "foul_major", "tech_foul", "robot_rule_violation", "safety_violation"}:
                match_major_like_penalty_events += event_count
            if event_type == "climb_success":
                meta = event.meta or {}
                official_points = _event_meta_number(event, "official_points")
                if official_points is not None and official_points >= 0.0:
                    inferred_points = float(official_points)
                else:
                    dwell = meta.get("tower_dwell_sec") if isinstance(meta, dict) else None
                    dwell_sec = float(dwell) if isinstance(dwell, (int, float)) else 0.0
                    inferred_points = low_climb_points
                    if dwell_sec >= 16.0:
                        inferred_points = high_climb_points
                    elif dwell_sec >= 10.0:
                        inferred_points = mid_climb_points
                    if float(event.time_sec) <= (phase_auto_sec + 1.5):
                        inferred_points = max(inferred_points, auto_l1_points)
                match_climb_point = max(match_climb_point, inferred_points)
            elif event_type == "climb_attempt":
                official_points = _event_meta_number(event, "official_points")
                if official_points is not None and official_points > 0.0:
                    match_climb_point = max(match_climb_point, float(official_points))
                else:
                    match_climb_point = max(match_climb_point, low_climb_points * 0.2)
        explicit_penalty_points += match_explicit_penalty_points * weight
        major_like_penalty_events += match_major_like_penalty_events * weight
        protected_zone_hits += match_protected_zone_hits * weight
        disabled_events += match_disabled_events * weight
        explicit_penalty_points_by_match.append(match_explicit_penalty_points)
        match_climb_points.append((match_climb_point, weight))
        match_teleop_score_counts.append((match_success_count, weight))

    matches_observed = max(1.0, weighted_observed_matches)
    explicit_penalty_points_per_match = explicit_penalty_points / matches_observed
    major_like_penalty_events_per_match = major_like_penalty_events / matches_observed
    protected_zone_hits_per_match = protected_zone_hits / matches_observed
    disabled_events_per_match = disabled_events / matches_observed

    return ScoringEvents(
        match_climb_points=match_climb_points,
        match_teleop_score_counts=match_teleop_score_counts,
        explicit_penalty_points_per_match=explicit_penalty_points_per_match,
        major_like_penalty_events_per_match=major_like_penalty_events_per_match,
        protected_zone_hits_per_match=protected_zone_hits_per_match,
        disabled_events_per_match=disabled_events_per_match,
        penalty_trend_delta=_trend_delta_ratio(explicit_penalty_points_by_match),
    )
