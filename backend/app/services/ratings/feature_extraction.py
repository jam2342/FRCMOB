# Per-team feature extraction for the rating pipeline.
#
# Extracts raw feature values, confidence support, and findings counts
# for every team at an event.

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from app.core.config import settings
from app.db import models
from app.services.ratings.anti_defense import _anti_defense_stage_multiplier
from app.services.ratings.constants import (
    PENALTY_EVENT_POINT_WEIGHTS,
    PENALTY_IMPACT_NET_POINTS,
    RECENT_MATCH_WINDOW,
    RECENT_PRIORITY_WINDOW,
    MINOR_FOUL_POINTS,
)
from app.services.ratings.data_loader import EventRatingData
from app.services.ratings.helpers import (
    _dedupe_findings_by_match,
    _event_meta_number,
    _is_active_hub_attempt_event,
    _match_stage,
    _recent_weight_for_index,
    _sort_findings_newest_first,
    _trend_delta_ratio,
)
from app.services.utils import _as_float, _clamp, _mean, _weighted_mean, _weighted_median, _weighted_std

class FeatureExtractionResult:
    # Container for the outputs of per-team feature extraction.

    __slots__ = (
        "feature_raw",
        "confidence_support",
        "findings_count",
        "resolved_findings_by_team",
        "resolved_finding_by_team_match",
    )

    def __init__(self) -> None:
        self.feature_raw: dict[str, dict[str, float | None]] = {}
        self.confidence_support: dict[str, dict[str, float]] = {}
        self.findings_count: dict[str, int] = {}
        self.resolved_findings_by_team: dict[str, list[models.TeamMatchFinding]] = {}
        self.resolved_finding_by_team_match: dict[str, dict[str, models.TeamMatchFinding]] = defaultdict(dict)

def extract_team_features(
    data: EventRatingData,
    manual_context: dict[str, Any],
) -> FeatureExtractionResult:
    # Compute raw feature vectors for each team from loaded data.

    result = FeatureExtractionResult()

    phase_auto_sec = float(manual_context["phases"]["auto_sec"])
    phase_teleop_sec = float(manual_context["phases"]["teleop_sec"])
    phase_teleop_active_hub_sec = float(
        manual_context["phases"].get("teleop_active_hub_sec") or phase_teleop_sec
    )
    scoring_points = manual_context["scoring_points"]
    rp_thresholds = manual_context["rp_thresholds"]
    auto_fuel_points = float(scoring_points.get("auto_fuel_score", 1.0))
    teleop_fuel_points = float(scoring_points.get("teleop_fuel_score", 1.0))
    auto_l1_points = float(scoring_points.get("auto_level1_climb", 15.0))
    low_climb_points = float(scoring_points.get("low_climb", 10.0))
    mid_climb_points = float(scoring_points.get("mid_climb", 20.0))
    high_climb_points = float(scoring_points.get("high_climb", 30.0))
    energized_threshold = max(1.0, float(rp_thresholds.get("energized", 100.0)))
    supercharged_threshold = max(1.0, float(rp_thresholds.get("supercharged", 360.0)))
    traversal_threshold = max(1.0, float(rp_thresholds.get("traversal", 50.0)))

    # ── Resolve findings ──────────────────────────────────────────────
    for team_key in data.team_keys:
        resolved = _dedupe_findings_by_match(
            _sort_findings_newest_first(data.findings_by_team.get(team_key, []), data.match_time_by_key),
            data.match_time_by_key,
        )
        result.resolved_findings_by_team[team_key] = resolved
        for finding in resolved:
            result.resolved_finding_by_team_match[team_key][finding.match_key] = finding

    # ── Per-team feature loop ─────────────────────────────────────────
    for team_key in data.team_keys:
        findings = list(result.resolved_findings_by_team.get(team_key, []))
        recent_findings = findings[:RECENT_MATCH_WINDOW]
        throughput_rows_team = data.throughputs_by_team.get(team_key, [])
        team_match_events = data.events_by_team_match.get(team_key, {})
        result.findings_count[team_key] = len(findings)

        throughput_bps_values_by_match: dict[str, list[float]] = defaultdict(list)
        for row in throughput_rows_team:
            if row.active_bps is None or row.active_bps < 0:
                continue
            throughput_bps_values_by_match[row.match_key].append(float(row.active_bps))
        throughput_bps_by_match = {
            match_key: median(values)
            for match_key, values in throughput_bps_values_by_match.items()
            if values
        }
        throughput_coverage_values_by_match: dict[str, list[float]] = defaultdict(list)
        throughput_coverage_by_run_match: dict[tuple[int, str], float] = {}
        for row in throughput_rows_team:
            coverage_payload = row.metric_coverage if isinstance(row.metric_coverage, dict) else {}
            coverage_score_raw = (
                coverage_payload.get("coverage_score") if isinstance(coverage_payload, dict) else None
            )
            if not isinstance(coverage_score_raw, (int, float)):
                continue
            coverage_score = _clamp(float(coverage_score_raw), 0.0, 1.0)
            throughput_coverage_values_by_match[row.match_key].append(coverage_score)
            throughput_coverage_by_run_match[(int(row.analysis_run_id), row.match_key)] = coverage_score
        throughput_coverage_by_match = {
            match_key: _clamp(float(median(values)), 0.0, 1.0)
            for match_key, values in throughput_coverage_values_by_match.items()
            if values
        }

        def _quality_support_for_match_finding(
            finding: models.TeamMatchFinding | None,
        ) -> tuple[float, float, float]:
            default_quality = 0.55
            if finding is None:
                return default_quality, default_quality, default_quality

            analysis_quality: float | None = None
            identity_quality: float | None = None
            quality_row = data.quality_by_run.get(finding.analysis_run_id)
            if quality_row is not None:
                analysis_quality = _clamp(float(quality_row.overall_quality_score), 0.0, 1.0)
                identity_quality = _clamp(float(quality_row.identity_quality_score), 0.0, 1.0)
            else:
                summary = finding.summary or {}
                sampling = summary.get("sampling", {}) if isinstance(summary, dict) else {}
                track_summary = summary.get("track_summary", {}) if isinstance(summary, dict) else {}
                detections = sampling.get("detections") if isinstance(sampling, dict) else None
                frames = sampling.get("frames_extracted") if isinstance(sampling, dict) else None
                team_tracks = track_summary.get("team_tracks") if isinstance(track_summary, dict) else None
                detection_factor = (
                    _clamp(float(detections) / max(1.0, float(frames)), 0.0, 1.0)
                    if isinstance(detections, (int, float)) and isinstance(frames, (int, float))
                    else 0.45
                )
                team_track_factor = (
                    _clamp(float(team_tracks) / 2.0, 0.0, 1.0)
                    if isinstance(team_tracks, (int, float))
                    else 0.45
                )
                analysis_quality = _clamp(0.2 + (0.4 * detection_factor) + (0.4 * team_track_factor), 0.0, 1.0)
                identity_quality = _clamp(0.25 + (0.75 * team_track_factor), 0.0, 1.0)

            throughput_coverage = throughput_coverage_by_run_match.get(
                (int(finding.analysis_run_id), finding.match_key)
            )
            if throughput_coverage is None:
                throughput_coverage = throughput_coverage_by_match.get(finding.match_key)
            if throughput_coverage is None:
                summary = finding.summary or {}
                throughput_summary = summary.get("throughput_metrics", {}) if isinstance(summary, dict) else {}
                throughput_raw = (
                    throughput_summary.get("coverage_score")
                    if isinstance(throughput_summary, dict)
                    else None
                )
                if isinstance(throughput_raw, (int, float)):
                    throughput_coverage = _clamp(float(throughput_raw), 0.0, 1.0)

            return (
                _clamp(float(analysis_quality if analysis_quality is not None else default_quality), 0.0, 1.0),
                _clamp(float(identity_quality if identity_quality is not None else default_quality), 0.0, 1.0),
                _clamp(float(throughput_coverage if throughput_coverage is not None else default_quality), 0.0, 1.0),
            )

        bps_values_from_finding_by_match: dict[str, list[float]] = defaultdict(list)
        legacy_fuel_rate_cap_per_min = max(1.0, float(getattr(settings, "fuel_scoring_rate_max_per_min", 16.0) or 16.0))
        cycle_values_by_match: dict[str, list[float]] = defaultdict(list)
        climb_values_by_match: dict[str, list[float]] = defaultdict(list)
        reliability_values_by_match: dict[str, list[float]] = defaultdict(list)
        auto_values_by_match: dict[str, list[float]] = defaultdict(list)
        defense_values_by_match: dict[str, list[float]] = defaultdict(list)
        for finding in recent_findings:
            fuel_rate_value = (
                float(finding.fuel_scoring_rate)
                if finding.fuel_scoring_rate is not None and finding.fuel_scoring_rate >= 0
                else None
            )
            if finding.cycle_time_sec is not None and finding.cycle_time_sec > 0:
                cycle_implied_rate = 60.0 / max(1e-6, float(finding.cycle_time_sec))
                if fuel_rate_value is None:
                    fuel_rate_value = cycle_implied_rate
                elif (
                    fuel_rate_value >= (legacy_fuel_rate_cap_per_min - 1e-6)
                    and cycle_implied_rate > (fuel_rate_value * 1.01)
                ):
                    fuel_rate_value = cycle_implied_rate
            if fuel_rate_value is not None and fuel_rate_value >= 0.0:
                bps_values_from_finding_by_match[finding.match_key].append(fuel_rate_value / 60.0)
            if finding.cycle_time_sec is not None and finding.cycle_time_sec > 0:
                cycle_values_by_match[finding.match_key].append(float(finding.cycle_time_sec))
            if finding.climb_success_prob is not None:
                climb_values_by_match[finding.match_key].append(float(finding.climb_success_prob))
            if finding.reliability_score is not None:
                reliability_values_by_match[finding.match_key].append(float(finding.reliability_score))
            if finding.auto_contribution is not None and finding.auto_contribution >= 0:
                auto_values_by_match[finding.match_key].append(float(finding.auto_contribution))
            if finding.defensive_engagement_sec is not None and finding.defensive_engagement_sec >= 0:
                defense_values_by_match[finding.match_key].append(float(finding.defensive_engagement_sec))

        ordered_match_keys = sorted(
            {
                *throughput_bps_by_match.keys(),
                *bps_values_from_finding_by_match.keys(),
                *cycle_values_by_match.keys(),
                *climb_values_by_match.keys(),
                *reliability_values_by_match.keys(),
                *auto_values_by_match.keys(),
                *defense_values_by_match.keys(),
                *team_match_events.keys(),
            },
            key=lambda match_key: (
                int(data.match_time_by_key.get(match_key) or -1),
                match_key,
            ),
            reverse=True,
        )[:RECENT_MATCH_WINDOW]

        bps_pairs: list[tuple[float, float]] = []
        bps_series: list[float] = []
        cycle_pairs: list[tuple[float, float]] = []
        cycle_series: list[float] = []
        climb_pairs: list[tuple[float, float]] = []
        reliability_pairs: list[tuple[float, float]] = []
        auto_pairs: list[tuple[float, float]] = []
        defense_pairs: list[tuple[float, float]] = []
        open_bps_pairs: list[tuple[float, float]] = []
        pressured_bps_pairs: list[tuple[float, float]] = []
        open_cycle_pairs: list[tuple[float, float]] = []
        pressured_cycle_pairs: list[tuple[float, float]] = []
        open_reliability_pairs: list[tuple[float, float]] = []
        pressured_reliability_pairs: list[tuple[float, float]] = []
        anti_escape_time_pairs: list[tuple[float, float]] = []
        anti_obstacle_eff_pairs: list[tuple[float, float]] = []
        hub_awareness_pairs: list[tuple[float, float]] = []
        terrain_mobility_pairs: list[tuple[float, float]] = []
        pressure_score_by_match: dict[str, float] = {}
        pressured_match_count = 0
        open_match_count = 0
        stage_weight_totals: dict[str, float] = {
            "early_quals": 0.0,
            "late_quals": 0.0,
            "elims": 0.0,
        }
        stage_match_count = 0
        match_weight_by_key: dict[str, float] = {}
        match_quality_weight_values: list[float] = []

        for index, match_key in enumerate(ordered_match_keys):
            recency_weight = _recent_weight_for_index(index)
            # Elims recency boost: weight elims matches higher than quals
            match_stage = _match_stage(
                data.match_comp_level_by_key.get(match_key),
                data.match_number_by_key.get(match_key),
                data.max_qm_match_number,
            )
            if match_stage == "elims":
                recency_weight *= 1.25
            elif match_stage == "late_quals":
                recency_weight *= 1.15
            match_finding = result.resolved_finding_by_team_match.get(team_key, {}).get(match_key)
            match_analysis_quality, match_identity_quality, match_throughput_coverage = (
                _quality_support_for_match_finding(match_finding)
            )
            match_quality_weight = _clamp(
                0.55
                + (0.20 * match_analysis_quality)
                + (0.12 * match_identity_quality)
                + (0.13 * match_throughput_coverage),
                0.55,
                1.0,
            )
            match_quality_weight_values.append(match_quality_weight)
            weight = recency_weight * match_quality_weight
            match_weight_by_key[match_key] = weight
            stage_weight_totals[match_stage] += weight
            stage_match_count += 1
            grouped_events = team_match_events.get(match_key, [])
            opponent_keys = data.opponent_keys_by_team_match.get(team_key, {}).get(match_key, [])
            opponent_defense_values = []
            for opponent_key in opponent_keys:
                opponent_finding = result.resolved_finding_by_team_match.get(opponent_key, {}).get(match_key)
                if opponent_finding is None:
                    continue
                opponent_defense = opponent_finding.defensive_engagement_sec
                if isinstance(opponent_defense, (int, float)) and opponent_defense >= 0:
                    opponent_defense_values.append(float(opponent_defense))

            protected_dwell_sec = 0.0
            obstacle_dwell_sec = 0.0
            obstacle_entries = 0.0
            protected_hits = 0.0
            match_active_hub_fuel = 0.0
            match_total_fuel = 0.0
            for event in grouped_events:
                event_type = str(event.event_type or "").strip().lower()
                if event_type == "protected_zone_interference":
                    count_estimate = _event_meta_number(event, "count_estimate")
                    protected_hits += max(0.0, float(count_estimate)) if count_estimate is not None else 1.0
                if event_type == "zone_dwell":
                    meta = event.meta or {}
                    zone_key = str(meta.get("zone_key") or "").lower()
                    duration = _event_meta_number(event, "duration_sec")
                    duration_sec = max(0.0, float(duration or 0.0))
                    if "protected" in zone_key or "neutral" in zone_key:
                        protected_dwell_sec += duration_sec
                    if "trench" in zone_key or "bump" in zone_key:
                        obstacle_dwell_sec += duration_sec
                if event_type == "zone_entry":
                    meta = event.meta or {}
                    zone_key = str(meta.get("zone_key") or "").lower()
                    if "trench" in zone_key or "bump" in zone_key:
                        obstacle_entries += 1.0
                # Hub awareness: track active vs total fuel scoring
                if event_type in {"teleop_fuel_score_success", "teleop_fuel_score_attempt"}:
                    count_est = _event_meta_number(event, "count_estimate")
                    fuel_count = max(1.0, float(count_est)) if count_est is not None else 1.0
                    match_total_fuel += fuel_count
                    if _is_active_hub_attempt_event(event):
                        match_active_hub_fuel += fuel_count

            opponent_pressure = _clamp(
                (median(opponent_defense_values) / max(1e-6, phase_teleop_sec * 0.45))
                if opponent_defense_values
                else 0.0,
                0.0,
                1.0,
            )
            explicit_pressure = _clamp(((protected_hits * 8.0) + protected_dwell_sec) / 90.0, 0.0, 1.0)
            pressure_score = _clamp((0.62 * opponent_pressure) + (0.38 * explicit_pressure), 0.0, 1.0)
            pressure_score_by_match[match_key] = pressure_score
            if pressure_score >= 0.45:
                pressured_match_count += 1
            elif pressure_score <= 0.25:
                open_match_count += 1

            bps_value = throughput_bps_by_match.get(match_key)
            if bps_value is None:
                finding_bps_values = bps_values_from_finding_by_match.get(match_key) or []
                if finding_bps_values:
                    bps_value = median(finding_bps_values)
            if bps_value is None and grouped_events:
                official_teleop_counts = [
                    _event_meta_number(event, "count_estimate")
                    for event in grouped_events
                    if event.event_type == "teleop_fuel_score_success"
                ]
                official_teleop_counts = [
                    float(value)
                    for value in official_teleop_counts
                    if value is not None and value > 0.0
                ]
                if official_teleop_counts:
                    bps_value = float(median(official_teleop_counts)) / max(1e-6, phase_teleop_active_hub_sec)
            if bps_value is not None:
                bps_pairs.append((float(bps_value), weight))
                bps_series.append(float(bps_value))
                if pressure_score >= 0.45:
                    pressured_bps_pairs.append((float(bps_value), weight))
                elif pressure_score <= 0.25:
                    open_bps_pairs.append((float(bps_value), weight))

            cycle_values = cycle_values_by_match.get(match_key) or []
            if cycle_values:
                cycle_value = float(median(cycle_values))
                cycle_pairs.append((cycle_value, weight))
                cycle_series.append(cycle_value)
                if pressure_score >= 0.45:
                    pressured_cycle_pairs.append((cycle_value, weight))
                elif pressure_score <= 0.25:
                    open_cycle_pairs.append((cycle_value, weight))

            climb_values = climb_values_by_match.get(match_key) or []
            if climb_values:
                climb_pairs.append((float(median(climb_values)), weight))

            reliability_values = reliability_values_by_match.get(match_key) or []
            if reliability_values:
                reliability_value = float(median(reliability_values))
                reliability_pairs.append((reliability_value, weight))
                if pressure_score >= 0.45:
                    pressured_reliability_pairs.append((reliability_value, weight))
                elif pressure_score <= 0.25:
                    open_reliability_pairs.append((reliability_value, weight))

            auto_values = auto_values_by_match.get(match_key) or []
            if auto_values:
                auto_pairs.append((float(median(auto_values)), weight))
            elif grouped_events:
                official_auto_points = [
                    _event_meta_number(event, "official_points") or _event_meta_number(event, "points")
                    for event in grouped_events
                    if event.event_type == "auto_points_scored"
                ]
                official_auto_points = [
                    float(value)
                    for value in official_auto_points
                    if value is not None and value >= 0.0
                ]
                if official_auto_points:
                    auto_pairs.append((float(median(official_auto_points)), weight))

            defense_values = defense_values_by_match.get(match_key) or []
            if defense_values:
                defense_pairs.append((float(median(defense_values)), weight))

            if pressure_score >= 0.35 and protected_dwell_sec > 0.0:
                anti_escape_time_pairs.append((float(protected_dwell_sec), weight))
            if obstacle_entries > 0.0:
                anti_obstacle_eff_pairs.append((float(obstacle_entries / max(1e-6, obstacle_dwell_sec + 0.6)), weight))

            # Hub awareness ratio: fraction of fuel scored into active hub
            if match_total_fuel > 0.0:
                hub_awareness_pairs.append((match_active_hub_fuel / match_total_fuel, weight))

            # Terrain mobility: obstacle traversal efficiency as standalone signal
            if obstacle_entries > 0.0:
                terrain_mob = _clamp(obstacle_entries / max(1e-6, obstacle_dwell_sec + 0.6), 0.0, 5.0)
                terrain_mobility_pairs.append((terrain_mob, weight))

        bps_median = _weighted_median(bps_pairs)
        cycle_median = _weighted_median(cycle_pairs)
        shift_productivity_raw = (
            _weighted_median([(1.0 / max(1e-6, value), weight) for value, weight in cycle_pairs])
            if cycle_pairs
            else bps_median
        )
        observed_balls_per_cycle = (
            bps_median * cycle_median
            if bps_median is not None and cycle_median is not None
            else None
        )
        capability = data.capabilities_by_team.get(team_key)
        capacity_value = (
            float(capability.ball_capacity)
            if capability is not None and capability.ball_capacity is not None and capability.ball_capacity > 0
            else (
                (observed_balls_per_cycle * 1.25)
                if observed_balls_per_cycle is not None and observed_balls_per_cycle > 0
                else None
            )
        )
        capacity_util_raw = (
            observed_balls_per_cycle / capacity_value
            if observed_balls_per_cycle is not None and capacity_value is not None and capacity_value > 0
            else None
        )
        climb_success_raw = _weighted_mean(climb_pairs)
        uptime_raw = _weighted_mean(reliability_pairs)
        bps_std = _weighted_std(bps_pairs)
        defense_median = _weighted_median(defense_pairs)
        auto_raw = _weighted_median(auto_pairs)
        climb_values_list = [value for value, _ in climb_pairs]
        open_bps = _weighted_mean(open_bps_pairs)
        pressured_bps = _weighted_mean(pressured_bps_pairs)
        open_cycle = _weighted_mean(open_cycle_pairs)
        pressured_cycle = _weighted_mean(pressured_cycle_pairs)
        open_reliability = _weighted_mean(open_reliability_pairs)
        pressured_reliability = _weighted_mean(pressured_reliability_pairs)
        anti_escape_time_sec = _weighted_median(anti_escape_time_pairs)
        anti_obstacle_eff = _weighted_median(anti_obstacle_eff_pairs)
        hub_awareness_ratio = _weighted_mean(hub_awareness_pairs) if hub_awareness_pairs else None
        terrain_mobility_raw = _weighted_median(terrain_mobility_pairs) if terrain_mobility_pairs else None
        scoring_drop_under_pressure = (
            _clamp((open_bps - pressured_bps) / max(1e-6, open_bps), -1.0, 1.0)
            if open_bps is not None and pressured_bps is not None and open_bps > 0
            else None
        )
        cycle_increase_under_pressure = (
            _clamp((pressured_cycle - open_cycle) / max(1e-6, open_cycle), -1.0, 1.0)
            if open_cycle is not None and pressured_cycle is not None and open_cycle > 0
            else None
        )
        reliability_drop_under_pressure = (
            _clamp((open_reliability - pressured_reliability) / max(1e-6, open_reliability), -1.0, 1.0)
            if open_reliability is not None and pressured_reliability is not None and open_reliability > 0
            else None
        )
        anti_defense_drop_pairs: list[tuple[float, float]] = []
        if scoring_drop_under_pressure is not None:
            anti_defense_drop_pairs.append((max(0.0, float(scoring_drop_under_pressure)), 0.52))
        if cycle_increase_under_pressure is not None:
            anti_defense_drop_pairs.append((max(0.0, float(cycle_increase_under_pressure)), 0.30))
        if reliability_drop_under_pressure is not None:
            anti_defense_drop_pairs.append((max(0.0, float(reliability_drop_under_pressure)), 0.18))
        anti_defense_drop_index = _weighted_mean(anti_defense_drop_pairs) if anti_defense_drop_pairs else None
        anti_defense_pressure_coverage = _clamp(
            (pressured_match_count + open_match_count) / float(max(1, len(ordered_match_keys))),
            0.0,
            1.0,
        )
        anti_defense_stage_multiplier, anti_defense_stage_shares = _anti_defense_stage_multiplier(
            stage_weight_totals,
            stage_match_count,
        )

        throughput_coverage_values = []
        for row in throughput_rows_team:
            coverage = row.metric_coverage or {}
            value = coverage.get("coverage_score") if isinstance(coverage, dict) else None
            if isinstance(value, (int, float)):
                throughput_coverage_values.append(float(value))
        for finding in recent_findings:
            summary = finding.summary or {}
            throughput_summary = summary.get("throughput_metrics", {}) if isinstance(summary, dict) else {}
            value = throughput_summary.get("coverage_score") if isinstance(throughput_summary, dict) else None
            if isinstance(value, (int, float)):
                throughput_coverage_values.append(float(value))
        avg_throughput_coverage = _mean(throughput_coverage_values) or 0.0
        avg_match_quality_weight = _mean(match_quality_weight_values) or 0.55
        min_match_quality_weight = min(match_quality_weight_values) if match_quality_weight_values else 0.55

        attempt_rates: list[tuple[float, float]] = []
        for index, match_key in enumerate(ordered_match_keys):
            grouped_events = team_match_events.get(match_key, [])
            attempt_times = sorted(
                [
                    float(event.time_sec)
                    for event in grouped_events
                    if _is_active_hub_attempt_event(event)
                    and isinstance(event.time_sec, (int, float))
                ]
            )
            if not attempt_times:
                continue
            left = 0
            best_rate = 0.0
            window_sec = 8.0
            for right in range(len(attempt_times)):
                while left <= right and attempt_times[right] - attempt_times[left] > window_sec:
                    left += 1
                count = right - left + 1
                best_rate = max(best_rate, count / window_sec)
            attempt_rates.append((best_rate, float(match_weight_by_key.get(match_key, _recent_weight_for_index(index)))))
        max_burst_raw = _weighted_median(attempt_rates) if attempt_rates else bps_median

        speed_values = sorted(data.speeds_by_team.get(team_key, []))
        top_speed_raw = None
        if speed_values:
            idx = int(0.9 * (len(speed_values) - 1))
            top_speed_raw = speed_values[idx]

        climb_max_raw = max(climb_values_list) if climb_values_list else 0.0
        shot_stability_raw = bps_std if bps_std is not None else None

        track_quality_values: list[float] = []
        identity_quality_values: list[float] = []
        good_quality_matches = 0
        for finding in recent_findings:
            analysis_quality, identity_quality, _ = _quality_support_for_match_finding(finding)
            track_quality_values.append(analysis_quality)
            identity_quality_values.append(identity_quality)
            if analysis_quality >= 0.70 and identity_quality >= 0.70:
                good_quality_matches += 1

        avg_analysis_quality = _mean(track_quality_values) or 0.0
        avg_identity_quality = _mean(identity_quality_values) or 0.0
        good_match_factor = _clamp(good_quality_matches / float(max(1, RECENT_PRIORITY_WINDOW)), 0.0, 1.0)
        coverage_count = sum(
            1
            for value in (
                bps_median,
                shift_productivity_raw,
                capacity_util_raw,
                auto_raw,
                climb_success_raw,
                uptime_raw,
                defense_median,
            )
            if value is not None
        )
        coverage_factor = coverage_count / 7.0

        cycle_eff = (
            _clamp((12.0 - cycle_median) / 12.0, 0.0, 1.0)
            if cycle_median is not None
            else 0.35
        )
        stability_raw = (
            _clamp(1.0 / (1.0 + max(0.0, (bps_std or 0.0) / max(1e-6, bps_median))), 0.0, 1.0)
            if bps_median is not None and bps_median > 0 and bps_std is not None
            else (0.42 if bps_median is not None else 0.30)
        )
        hub_aware_factor = _clamp(hub_awareness_ratio, 0.0, 1.0) if hub_awareness_ratio is not None else 0.5
        decision_quality_raw = (
            (
                (0.37 * (uptime_raw or 0.0))
                + (0.34 * cycle_eff)
                + (0.14 * stability_raw)
                + (0.15 * hub_aware_factor)
            )
            if uptime_raw is not None
            else ((0.58 * cycle_eff) + (0.22 * stability_raw) + (0.20 * hub_aware_factor))
        )
        throughput_trend_delta = _trend_delta_ratio(bps_series)
        reliability_trend_delta = _trend_delta_ratio([value for value, _ in reliability_pairs])
        cycle_trend_delta = _trend_delta_ratio(cycle_series)

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
            if not grouped_events and match_key not in throughput_bps_by_match and match_key not in bps_values_from_finding_by_match:
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

        defensive_pressure = (
            max(0.0, ((defense_median or 0.0) - 60.0) / 45.0)
            if defense_median is not None
            else 0.0
        )
        reliability_proxy = max(0.0, (0.55 - (uptime_raw or 0.0)) * 2.0)
        inferred_penalty_points = MINOR_FOUL_POINTS * (
            (0.6 * protected_zone_hits_per_match)
            + (0.3 * disabled_events_per_match)
            + (0.35 * defensive_pressure)
            + (0.25 * reliability_proxy)
        )

        blended_penalty_points = explicit_penalty_points_per_match + (
            (0.35 * inferred_penalty_points) if explicit_penalty_points_per_match > 0 else inferred_penalty_points
        )
        penalty_points_per_match = blended_penalty_points
        severe_penalty_rate = major_like_penalty_events_per_match
        penalty_trend_delta = _trend_delta_ratio(explicit_penalty_points_by_match)

        auto_points_est = (
            (auto_raw * max(0.5, auto_fuel_points))
            if auto_raw is not None
            else None
        )
        teleop_score_count_est = _weighted_median(match_teleop_score_counts) if match_teleop_score_counts else None
        teleop_points_est: float | None
        if teleop_score_count_est is not None:
            teleop_points_est = teleop_score_count_est * teleop_fuel_points
        elif bps_median is not None:
            teleop_points_est = float(bps_median * phase_teleop_active_hub_sec) * teleop_fuel_points
        else:
            teleop_points_est = None
        climb_points_est: float | None
        if match_climb_points:
            climb_points_est = _weighted_mean(match_climb_points)
        elif climb_success_raw is not None:
            climb_points_est = float(climb_success_raw) * ((0.45 * mid_climb_points) + (0.55 * high_climb_points))
        else:
            climb_points_est = None
        peak_climb_points = max((points for points, _ in match_climb_points), default=0.0)
        peak_climb_level_signal = (
            _clamp(float(peak_climb_points) / max(1e-6, float(high_climb_points)), 0.0, 1.0)
            if peak_climb_points > 0.0
            else 0.0
        )
        if peak_climb_level_signal > 0.0:
            if climb_max_raw > 0.0:
                climb_max_raw = _clamp((0.55 * climb_max_raw) + (0.45 * peak_climb_level_signal), 0.0, 1.0)
            else:
                climb_max_raw = peak_climb_level_signal
        expected_net_points: float | None
        if auto_points_est is not None and teleop_points_est is not None:
            expected_net_points = max(
                0.0,
                auto_points_est
                + teleop_points_est
                + (climb_points_est or 0.0)
                - (PENALTY_IMPACT_NET_POINTS * penalty_points_per_match),
            )
        else:
            expected_net_points = None

        rp_contribution_raw: float | None
        if expected_net_points is not None and teleop_points_est is not None:
            energized_share = _clamp(expected_net_points / max(1e-6, energized_threshold / 3.0), 0.0, 1.35)
            supercharged_share = _clamp(teleop_points_est / max(1e-6, supercharged_threshold / 3.0), 0.0, 1.35)
            traversal_share = _clamp((climb_points_est or 0.0) / max(1e-6, traversal_threshold / 3.0), 0.0, 1.35)
            rp_contribution_raw = 100.0 * _clamp(
                (0.35 * energized_share) + (0.15 * supercharged_share) + (0.50 * traversal_share),
                0.0,
                1.20,
            )
        else:
            rp_contribution_raw = None
        event_strength_row = data.event_strength_by_team.get(team_key)
        season_strength_row = data.season_strength_by_team.get(team_key)
        event_strength_bps = (
            float(event_strength_row.strength_active_bps)
            if event_strength_row and event_strength_row.strength_active_bps is not None
            else None
        )
        season_strength_bps = (
            float(season_strength_row.strength_active_bps)
            if season_strength_row and season_strength_row.strength_active_bps is not None
            else None
        )
        event_strength_conf = (
            float(event_strength_row.confidence_0_1)
            if event_strength_row and event_strength_row.confidence_0_1 is not None
            else None
        )
        season_strength_conf = (
            float(season_strength_row.confidence_0_1)
            if season_strength_row and season_strength_row.confidence_0_1 is not None
            else None
        )

        result.feature_raw[team_key] = {
            "capacity": capacity_value,
            "max_burst_bps": max_burst_raw,
            "top_speed": top_speed_raw,
            "climb_max": climb_max_raw,
            "shot_stability_var": shot_stability_raw,
            "bps_median": bps_median,
            "shift_productivity": shift_productivity_raw,
            "capacity_utilization": capacity_util_raw,
            "climb_success": climb_success_raw,
            "climb_output": climb_points_est,
            "uptime": uptime_raw,
            "consistency_var": bps_std,
            "decision_quality": decision_quality_raw,
            "stability_raw": stability_raw,
            "throughput_trend_delta": throughput_trend_delta,
            "reliability_trend_delta": reliability_trend_delta,
            "cycle_trend_delta": cycle_trend_delta,
            "penalty_trend_delta": penalty_trend_delta,
            "auto_points_est": auto_points_est,
            "teleop_points_est": teleop_points_est,
            "expected_net_points": expected_net_points,
            "rp_contribution_raw": rp_contribution_raw,
            "defense_presence": defense_median,
            "anti_defense_scoring_drop": scoring_drop_under_pressure,
            "anti_defense_cycle_increase": cycle_increase_under_pressure,
            "anti_defense_reliability_drop": reliability_drop_under_pressure,
            "anti_defense_escape_time_sec": anti_escape_time_sec,
            "hub_awareness_ratio": hub_awareness_ratio,
            "terrain_mobility": terrain_mobility_raw,
            "anti_defense_obstacle_eff": anti_obstacle_eff,
            "anti_defense_drop_index": anti_defense_drop_index,
            "anti_defense_pressure_coverage": anti_defense_pressure_coverage,
            "anti_defense_pressured_matches": float(pressured_match_count),
            "anti_defense_open_matches": float(open_match_count),
            "anti_defense_stage_multiplier": anti_defense_stage_multiplier,
            "anti_defense_stage_early_quals_share": anti_defense_stage_shares["early_quals"],
            "anti_defense_stage_late_quals_share": anti_defense_stage_shares["late_quals"],
            "anti_defense_stage_elims_share": anti_defense_stage_shares["elims"],
            "anti_defense_stage_match_count": float(stage_match_count),
            "throughput_coverage": avg_throughput_coverage,
            "severe_penalty_rate": severe_penalty_rate,
            "penalty_points_per_match": penalty_points_per_match,
            "explicit_penalty_points_per_match": explicit_penalty_points_per_match,
            "inferred_penalty_points_per_match": inferred_penalty_points,
            "protected_zone_pressure": protected_zone_hits_per_match,
            "recent_matches_used": float(len(ordered_match_keys)),
            "statbotics_norm_epa": _as_float((data.epa_context_by_team.get(team_key) or {}).get("raw_value")),
            "event_strength_bps": event_strength_bps,
            "event_strength_confidence": event_strength_conf,
            "season_strength_bps": season_strength_bps,
            "season_strength_confidence": season_strength_conf,
            "video_findings_count": float(data.video_findings_count_by_team.get(team_key, 0)),
            "match_quality_weight_avg": avg_match_quality_weight,
            "match_quality_weight_min": min_match_quality_weight,
            "opr": (
                float(data.stats_by_team[team_key].opr)
                if team_key in data.stats_by_team and data.stats_by_team[team_key].opr is not None
                else None
            ),
            "dpr": (
                float(data.stats_by_team[team_key].dpr)
                if team_key in data.stats_by_team and data.stats_by_team[team_key].dpr is not None
                else None
            ),
            "ccwm": (
                float(data.stats_by_team[team_key].ccwm)
                if team_key in data.stats_by_team and data.stats_by_team[team_key].ccwm is not None
                else None
            ),
        }
        result.confidence_support[team_key] = {
            "good_match_factor": good_match_factor,
            "avg_analysis_quality": avg_analysis_quality,
            "avg_identity_quality": avg_identity_quality,
            "coverage_factor": coverage_factor,
            "throughput_coverage": _clamp(avg_throughput_coverage, 0.0, 1.0),
            "match_quality_weight_factor": _clamp(avg_match_quality_weight, 0.0, 1.0),
            "stability_factor": _clamp(stability_raw, 0.0, 1.0),
            "recent_match_factor": _clamp(len(ordered_match_keys) / float(max(1, RECENT_MATCH_WINDOW)), 0.0, 1.0),
            "trend_coverage_factor": _clamp(
                sum(
                    1
                    for value in (
                        throughput_trend_delta,
                        reliability_trend_delta,
                        cycle_trend_delta,
                        penalty_trend_delta,
                    )
                    if value is not None
                )
                / 4.0,
                0.0,
                1.0,
            ),
        }

    return result
