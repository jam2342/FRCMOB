# Percentile mapping and composite score computation for the rating pipeline.
#
# Takes raw features produced by ``rating_feature_extraction`` and produces
# percentile maps, robot-level, performance, driver-skill, confidence, and
# results-anchor scores for every team.

from __future__ import annotations

from app.services.ratings.anti_defense import _anti_defense_drop_band_score
from app.services.ratings.constants import (
    BASE_ANTIDEFENSE_WEIGHT,
    PERFORMANCE_ANTIDEFENSE_WEIGHT,
    PERFORMANCE_AUTO_WEIGHT,
    PERFORMANCE_EPA_BLEND,
    PENALTY_IMPACT_DRIVER,
    PENALTY_IMPACT_SUBSCORES,
    RESULTS_ANCHOR_EPA_WEIGHT,
)
from app.services.ratings.helpers import (
    _fit_linear_model,
    _percentile_map,
)
from app.services.utils import _clamp

class ScoringResult:
    # Container for all computed percentile maps and composite scores.

    __slots__ = (
        "feature_maps",
        "capacity_pct",
        "max_burst_pct",
        "speed_pct",
        "climb_max_pct",
        "shot_stability_pct",
        "throughput_pct",
        "shift_pct",
        "cap_util_pct",
        "endgame_pct",
        "auto_pct",
        "manual_points_pct",
        "rp_contribution_pct",
        "defense_presence_pct",
        "anti_defense_scoring_pct",
        "anti_defense_cycle_pct",
        "anti_defense_reliability_pct",
        "anti_defense_escape_pct",
        "anti_defense_obstacle_pct",
        "anti_defense_drop_pct",
        "anti_defense_pressure_coverage_pct",
        "uptime_pct",
        "consistency_pct",
        "penalty_discipline_pct",
        "throughput_trend_pct",
        "reliability_trend_pct",
        "cycle_trend_pct",
        "penalty_trend_pct",
        "statbotics_epa_pct",
        "event_strength_pct",
        "season_strength_pct",
        "event_strength_conf_pct",
        "season_strength_conf_pct",
        "opr_pct",
        "ccwm_pct",
        "dpr_defense_pct",
        "hub_awareness_pct",
        "terrain_mobility_pct",
        "anti_defense_pct",
        "anti_defense_performance_weight_by_team",
        "anti_defense_base_weight_by_team",
        "results_anchor_pct",
        "robot_level_by_team",
        "performance_by_team",
        "driver_skill_pct",
        "confidence_by_team",
        "decision_pct",
    )

    def __init__(self) -> None:
        self.feature_maps: dict[str, dict[str, float | None]] = {}
        self.capacity_pct: dict[str, float] = {}
        self.max_burst_pct: dict[str, float] = {}
        self.speed_pct: dict[str, float] = {}
        self.climb_max_pct: dict[str, float] = {}
        self.shot_stability_pct: dict[str, float] = {}
        self.throughput_pct: dict[str, float] = {}
        self.shift_pct: dict[str, float] = {}
        self.cap_util_pct: dict[str, float] = {}
        self.endgame_pct: dict[str, float] = {}
        self.auto_pct: dict[str, float] = {}
        self.manual_points_pct: dict[str, float] = {}
        self.rp_contribution_pct: dict[str, float] = {}
        self.defense_presence_pct: dict[str, float] = {}
        self.anti_defense_scoring_pct: dict[str, float] = {}
        self.anti_defense_cycle_pct: dict[str, float] = {}
        self.anti_defense_reliability_pct: dict[str, float] = {}
        self.anti_defense_escape_pct: dict[str, float] = {}
        self.anti_defense_obstacle_pct: dict[str, float] = {}
        self.anti_defense_drop_pct: dict[str, float] = {}
        self.anti_defense_pressure_coverage_pct: dict[str, float] = {}
        self.uptime_pct: dict[str, float] = {}
        self.consistency_pct: dict[str, float] = {}
        self.penalty_discipline_pct: dict[str, float] = {}
        self.throughput_trend_pct: dict[str, float] = {}
        self.reliability_trend_pct: dict[str, float] = {}
        self.cycle_trend_pct: dict[str, float] = {}
        self.penalty_trend_pct: dict[str, float] = {}
        self.statbotics_epa_pct: dict[str, float] = {}
        self.event_strength_pct: dict[str, float] = {}
        self.season_strength_pct: dict[str, float] = {}
        self.event_strength_conf_pct: dict[str, float] = {}
        self.season_strength_conf_pct: dict[str, float] = {}
        self.opr_pct: dict[str, float] = {}
        self.ccwm_pct: dict[str, float] = {}
        self.dpr_defense_pct: dict[str, float] = {}
        self.hub_awareness_pct: dict[str, float] = {}
        self.terrain_mobility_pct: dict[str, float] = {}
        self.anti_defense_pct: dict[str, float] = {}
        self.anti_defense_performance_weight_by_team: dict[str, float] = {}
        self.anti_defense_base_weight_by_team: dict[str, float] = {}
        self.results_anchor_pct: dict[str, float] = {}
        self.robot_level_by_team: dict[str, float] = {}
        self.performance_by_team: dict[str, float] = {}
        self.driver_skill_pct: dict[str, float] = {}
        self.confidence_by_team: dict[str, float] = {}
        self.decision_pct: dict[str, float] = {}

def compute_percentile_scores(
    team_keys: list[str],
    feature_raw: dict[str, dict[str, float | None]],
    confidence_support: dict[str, dict[str, float]],
) -> ScoringResult:
    # Build percentile maps and composite scores from raw features.

    s = ScoringResult()

    s.feature_maps = {
        "capacity": {k: v.get("capacity") for k, v in feature_raw.items()},
        "max_burst_bps": {k: v.get("max_burst_bps") for k, v in feature_raw.items()},
        "top_speed": {k: v.get("top_speed") for k, v in feature_raw.items()},
        "climb_max": {k: v.get("climb_max") for k, v in feature_raw.items()},
        "shot_stability_var": {k: v.get("shot_stability_var") for k, v in feature_raw.items()},
        "bps_median": {k: v.get("bps_median") for k, v in feature_raw.items()},
        "shift_productivity": {k: v.get("shift_productivity") for k, v in feature_raw.items()},
        "capacity_utilization": {k: v.get("capacity_utilization") for k, v in feature_raw.items()},
        "climb_success": {k: v.get("climb_success") for k, v in feature_raw.items()},
        "climb_output": {k: v.get("climb_output") for k, v in feature_raw.items()},
        "uptime": {k: v.get("uptime") for k, v in feature_raw.items()},
        "consistency_var": {k: v.get("consistency_var") for k, v in feature_raw.items()},
        "decision_quality": {k: v.get("decision_quality") for k, v in feature_raw.items()},
        "throughput_trend_delta": {k: v.get("throughput_trend_delta") for k, v in feature_raw.items()},
        "reliability_trend_delta": {k: v.get("reliability_trend_delta") for k, v in feature_raw.items()},
        "cycle_trend_delta": {k: v.get("cycle_trend_delta") for k, v in feature_raw.items()},
        "penalty_trend_delta": {k: v.get("penalty_trend_delta") for k, v in feature_raw.items()},
        "auto_points_est": {k: v.get("auto_points_est") for k, v in feature_raw.items()},
        "expected_net_points": {k: v.get("expected_net_points") for k, v in feature_raw.items()},
        "rp_contribution_raw": {k: v.get("rp_contribution_raw") for k, v in feature_raw.items()},
        "defense_presence": {k: v.get("defense_presence") for k, v in feature_raw.items()},
        "anti_defense_scoring_drop": {k: v.get("anti_defense_scoring_drop") for k, v in feature_raw.items()},
        "anti_defense_cycle_increase": {k: v.get("anti_defense_cycle_increase") for k, v in feature_raw.items()},
        "anti_defense_reliability_drop": {k: v.get("anti_defense_reliability_drop") for k, v in feature_raw.items()},
        "anti_defense_escape_time_sec": {k: v.get("anti_defense_escape_time_sec") for k, v in feature_raw.items()},
        "anti_defense_obstacle_eff": {k: v.get("anti_defense_obstacle_eff") for k, v in feature_raw.items()},
        "anti_defense_drop_index": {k: v.get("anti_defense_drop_index") for k, v in feature_raw.items()},
        "anti_defense_pressure_coverage": {k: v.get("anti_defense_pressure_coverage") for k, v in feature_raw.items()},
        "penalty_points_per_match": {k: v.get("penalty_points_per_match") for k, v in feature_raw.items()},
        "statbotics_norm_epa": {k: v.get("statbotics_norm_epa") for k, v in feature_raw.items()},
        "event_strength_bps": {k: v.get("event_strength_bps") for k, v in feature_raw.items()},
        "event_strength_confidence": {k: v.get("event_strength_confidence") for k, v in feature_raw.items()},
        "season_strength_bps": {k: v.get("season_strength_bps") for k, v in feature_raw.items()},
        "season_strength_confidence": {k: v.get("season_strength_confidence") for k, v in feature_raw.items()},
        "hub_awareness_ratio": {k: v.get("hub_awareness_ratio") for k, v in feature_raw.items()},
        "terrain_mobility": {k: v.get("terrain_mobility") for k, v in feature_raw.items()},
        "opr": {k: v.get("opr") for k, v in feature_raw.items()},
        "dpr": {k: v.get("dpr") for k, v in feature_raw.items()},
        "ccwm": {k: v.get("ccwm") for k, v in feature_raw.items()},
    }

    s.capacity_pct = _percentile_map(s.feature_maps["capacity"])
    s.max_burst_pct = _percentile_map(s.feature_maps["max_burst_bps"])
    s.speed_pct = _percentile_map(s.feature_maps["top_speed"])
    s.climb_max_pct = _percentile_map(s.feature_maps["climb_max"])
    s.shot_stability_pct = _percentile_map(s.feature_maps["shot_stability_var"], higher_is_better=False)

    s.throughput_pct = _percentile_map(s.feature_maps["bps_median"])
    s.shift_pct = _percentile_map(s.feature_maps["shift_productivity"])
    s.cap_util_pct = _percentile_map(s.feature_maps["capacity_utilization"])
    s.endgame_pct = _percentile_map(s.feature_maps["climb_output"])
    s.auto_pct = _percentile_map(s.feature_maps["auto_points_est"])
    s.manual_points_pct = _percentile_map(s.feature_maps["expected_net_points"])
    s.rp_contribution_pct = _percentile_map(s.feature_maps["rp_contribution_raw"])
    s.defense_presence_pct = _percentile_map(s.feature_maps["defense_presence"])
    s.anti_defense_scoring_pct = _percentile_map(s.feature_maps["anti_defense_scoring_drop"], higher_is_better=False)
    s.anti_defense_cycle_pct = _percentile_map(s.feature_maps["anti_defense_cycle_increase"], higher_is_better=False)
    s.anti_defense_reliability_pct = _percentile_map(
        s.feature_maps["anti_defense_reliability_drop"],
        higher_is_better=False,
    )
    s.anti_defense_escape_pct = _percentile_map(s.feature_maps["anti_defense_escape_time_sec"], higher_is_better=False)
    s.anti_defense_obstacle_pct = _percentile_map(s.feature_maps["anti_defense_obstacle_eff"])
    s.anti_defense_drop_pct = _percentile_map(s.feature_maps["anti_defense_drop_index"], higher_is_better=False)
    s.anti_defense_pressure_coverage_pct = _percentile_map(s.feature_maps["anti_defense_pressure_coverage"])
    s.uptime_pct = _percentile_map(s.feature_maps["uptime"])
    s.consistency_pct = _percentile_map(s.feature_maps["consistency_var"], higher_is_better=False)
    s.penalty_discipline_pct = _percentile_map(s.feature_maps["penalty_points_per_match"], higher_is_better=False)
    s.throughput_trend_pct = _percentile_map(s.feature_maps["throughput_trend_delta"])
    s.reliability_trend_pct = _percentile_map(s.feature_maps["reliability_trend_delta"])
    s.cycle_trend_pct = _percentile_map(s.feature_maps["cycle_trend_delta"], higher_is_better=False)
    s.penalty_trend_pct = _percentile_map(s.feature_maps["penalty_trend_delta"], higher_is_better=False)
    s.statbotics_epa_pct = _percentile_map(s.feature_maps["statbotics_norm_epa"])
    s.event_strength_pct = _percentile_map(s.feature_maps["event_strength_bps"])
    s.season_strength_pct = _percentile_map(s.feature_maps["season_strength_bps"])
    s.event_strength_conf_pct = _percentile_map(s.feature_maps["event_strength_confidence"])
    s.season_strength_conf_pct = _percentile_map(s.feature_maps["season_strength_confidence"])
    s.opr_pct = _percentile_map(s.feature_maps["opr"])
    s.ccwm_pct = _percentile_map(s.feature_maps["ccwm"])
    s.dpr_defense_pct = _percentile_map(s.feature_maps["dpr"], higher_is_better=False)
    s.hub_awareness_pct = _percentile_map(s.feature_maps["hub_awareness_ratio"])
    s.terrain_mobility_pct = _percentile_map(s.feature_maps["terrain_mobility"])

    # ── Anti-defense composite ────────────────────────────────────────
    for team_key in team_keys:
        context_score = _clamp(
            (0.30 * s.anti_defense_scoring_pct[team_key])
            + (0.18 * s.anti_defense_cycle_pct[team_key])
            + (0.14 * s.anti_defense_reliability_pct[team_key])
            + (0.12 * s.anti_defense_escape_pct[team_key])
            + (0.10 * s.anti_defense_obstacle_pct[team_key])
            + (0.08 * s.anti_defense_drop_pct[team_key])
            + (0.05 * s.speed_pct[team_key])
            + (0.03 * s.anti_defense_pressure_coverage_pct[team_key]),
            0.0,
            100.0,
        )
        drop_band_score = _anti_defense_drop_band_score(s.feature_maps["anti_defense_drop_index"].get(team_key))
        if drop_band_score is not None:
            s.anti_defense_pct[team_key] = _clamp((0.65 * drop_band_score) + (0.35 * context_score), 0.0, 100.0)
        else:
            s.anti_defense_pct[team_key] = context_score

    # ── Anti-defense weight per team ──────────────────────────────────
    for team_key in team_keys:
        stage_multiplier = _clamp(
            float(feature_raw[team_key].get("anti_defense_stage_multiplier") or 1.0),
            0.55,
            1.35,
        )
        pressure_coverage = _clamp(
            float(feature_raw[team_key].get("anti_defense_pressure_coverage") or 0.0),
            0.0,
            1.0,
        )
        stage_confidence_factor = _clamp(0.55 + (0.45 * pressure_coverage), 0.55, 1.0)
        effective_stage_multiplier = 1.0 + ((stage_multiplier - 1.0) * stage_confidence_factor)
        s.anti_defense_performance_weight_by_team[team_key] = _clamp(
            PERFORMANCE_ANTIDEFENSE_WEIGHT * effective_stage_multiplier,
            0.0,
            0.28,
        )
        s.anti_defense_base_weight_by_team[team_key] = _clamp(
            BASE_ANTIDEFENSE_WEIGHT * effective_stage_multiplier,
            0.0,
            0.22,
        )

    # ── Results anchor ────────────────────────────────────────────────
    for team_key in team_keys:
        anchor_components = [
            (0.60, s.opr_pct[team_key]),
            (0.25, s.ccwm_pct[team_key]),
            (0.15, s.dpr_defense_pct[team_key]),
        ]
        if feature_raw[team_key].get("statbotics_norm_epa") is not None and RESULTS_ANCHOR_EPA_WEIGHT > 0.0:
            anchor_components.append((RESULTS_ANCHOR_EPA_WEIGHT, s.statbotics_epa_pct[team_key]))
        total_anchor_weight = sum(weight for weight, _ in anchor_components)
        anchor_score = (
            sum(weight * value for weight, value in anchor_components) / total_anchor_weight
            if total_anchor_weight > 0.0
            else 50.0
        )
        s.results_anchor_pct[team_key] = _clamp(anchor_score, 0.0, 100.0)

    # ── Robot level & performance ─────────────────────────────────────
    for team_key in team_keys:
        robot_level = (
            (0.28 * s.capacity_pct[team_key])
            + (0.22 * s.max_burst_pct[team_key])
            + (0.20 * s.speed_pct[team_key])
            + (0.20 * s.climb_max_pct[team_key])
            + (0.05 * s.shot_stability_pct[team_key])
            + (0.05 * s.terrain_mobility_pct[team_key])
        )
        s.robot_level_by_team[team_key] = _clamp(robot_level, 0.0, 100.0)

        performance = (
            (0.09 * s.throughput_pct[team_key])
            + (0.08 * s.shift_pct[team_key])
            + (0.06 * s.cap_util_pct[team_key])
            + (0.23 * s.endgame_pct[team_key])
            + (PERFORMANCE_AUTO_WEIGHT * s.auto_pct[team_key])
            + (0.08 * s.uptime_pct[team_key])
            + (0.06 * s.consistency_pct[team_key])
            + ((0.04 * PENALTY_IMPACT_SUBSCORES) * s.penalty_discipline_pct[team_key])
            + (0.04 * s.defense_presence_pct[team_key])
            + (s.anti_defense_performance_weight_by_team[team_key] * s.anti_defense_pct[team_key])
            + (0.03 * s.rp_contribution_pct[team_key])
            + (0.03 * s.throughput_trend_pct[team_key])
            + (0.02 * s.reliability_trend_pct[team_key])
            + (0.015 * s.cycle_trend_pct[team_key])
            + ((0.005 * PENALTY_IMPACT_SUBSCORES) * s.penalty_trend_pct[team_key])
        )
        if feature_raw[team_key].get("statbotics_norm_epa") is not None and PERFORMANCE_EPA_BLEND > 0.0:
            performance = ((1.0 - PERFORMANCE_EPA_BLEND) * performance) + (
                PERFORMANCE_EPA_BLEND * s.statbotics_epa_pct[team_key]
            )
        s.performance_by_team[team_key] = _clamp(performance, 0.0, 100.0)

    # ── Driver skill (via regression residuals) ───────────────────────
    regression_keys = [
        team_key
        for team_key in team_keys
        if feature_raw[team_key].get("bps_median") is not None
        and feature_raw[team_key].get("shift_productivity") is not None
        and feature_raw[team_key].get("climb_output") is not None
    ]
    x_values = [s.robot_level_by_team[team_key] for team_key in regression_keys]
    bps_values = [float(feature_raw[team_key]["bps_median"] or 0.0) for team_key in regression_keys]
    shift_values = [float(feature_raw[team_key]["shift_productivity"] or 0.0) for team_key in regression_keys]
    climb_values = [float(feature_raw[team_key]["climb_output"] or 0.0) for team_key in regression_keys]
    bps_a, bps_b = _fit_linear_model(x_values, bps_values)
    shift_a, shift_b = _fit_linear_model(x_values, shift_values)
    climb_a, climb_b = _fit_linear_model(x_values, climb_values)

    residual_bps: dict[str, float | None] = {}
    residual_shift: dict[str, float | None] = {}
    residual_climb: dict[str, float | None] = {}
    for team_key in team_keys:
        robot_level = s.robot_level_by_team[team_key]
        observed_bps = feature_raw[team_key].get("bps_median")
        observed_shift = feature_raw[team_key].get("shift_productivity")
        observed_climb = feature_raw[team_key].get("climb_output")
        residual_bps[team_key] = (
            float(observed_bps) - (bps_a + (bps_b * robot_level))
            if observed_bps is not None
            else None
        )
        residual_shift[team_key] = (
            float(observed_shift) - (shift_a + (shift_b * robot_level))
            if observed_shift is not None
            else None
        )
        residual_climb[team_key] = (
            float(observed_climb) - (climb_a + (climb_b * robot_level))
            if observed_climb is not None
            else None
        )

    residual_bps_pct = _percentile_map(residual_bps)
    residual_shift_pct = _percentile_map(residual_shift)
    residual_climb_pct = _percentile_map(residual_climb)
    s.decision_pct = _percentile_map(s.feature_maps["decision_quality"])

    driver_raw_by_team: dict[str, float] = {}
    for team_key in team_keys:
        driver_raw = (
            (0.34 * residual_bps_pct[team_key])
            + (0.30 * residual_shift_pct[team_key])
            + (0.20 * residual_climb_pct[team_key])
            + (0.10 * s.decision_pct[team_key])
            + ((0.04 * PENALTY_IMPACT_DRIVER) * s.penalty_discipline_pct[team_key])
        )
        driver_raw_by_team[team_key] = _clamp(driver_raw, 0.0, 100.0)
    s.driver_skill_pct = _percentile_map(driver_raw_by_team)

    # ── Confidence ────────────────────────────────────────────────────
    for team_key in team_keys:
        support = confidence_support[team_key]
        s.confidence_by_team[team_key] = _clamp(
            0.06
            + (0.15 * support["good_match_factor"])
            + (0.17 * support["avg_analysis_quality"])
            + (0.17 * support["avg_identity_quality"])
            + (0.12 * support["coverage_factor"])
            + (0.10 * support["throughput_coverage"])
            + (0.08 * support["stability_factor"])
            + (0.09 * support["recent_match_factor"])
            + (0.06 * support["trend_coverage_factor"]),
            0.0,
            1.0,
        )

    return s
