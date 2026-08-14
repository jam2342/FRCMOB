# Rating output row construction and response payload assembly.
#
# Builds ``EventTeamRating`` DB rows and the API response payload from
# computed scores, signals, and metadata.

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.db import models
from app.services.ratings.constants import (
    ANTI_DEFENSE_ELITE_DROP_PCT,
    ANTI_DEFENSE_GOOD_DROP_PCT,
    ANTIDEFENSE_STAGE_EARLY_QUALS_MULTIPLIER,
    ANTIDEFENSE_STAGE_ELIMS_MULTIPLIER,
    ANTIDEFENSE_STAGE_LATE_QUALS_MULTIPLIER,
    ANTIDEFENSE_STAGE_SUPPORT_MATCHES,
    BASE_ANTIDEFENSE_WEIGHT,
    BASE_AUTO_WEIGHT,
    FALLBACK_MODEL_LABEL,
    MAJOR_FOUL_POINTS,
    MINOR_FOUL_POINTS,
    MODEL_VERSION,
    PENALTY_IMPACT_BASE_RATING,
    PENALTY_IMPACT_DRIVER,
    PENALTY_IMPACT_NET_POINTS,
    PENALTY_IMPACT_SUBSCORES,
    PERFORMANCE_ANTIDEFENSE_WEIGHT,
    PERFORMANCE_AUTO_WEIGHT,
    PERFORMANCE_EPA_BLEND,
    RECENT_BASE_WEIGHT,
    RECENT_MATCH_WINDOW,
    RECENT_PRIORITY_WEIGHT,
    RECENT_PRIORITY_WINDOW,
    RESULTS_ANCHOR_EPA_WEIGHT,
    SIGNAL_MIN_CONFIDENCE,
    SIGNAL_MIN_MATCHES,
    SIGNAL_STRONG_CONFIDENCE,
    SIGNAL_STRONG_MATCHES,
    SIGNAL_TREND_COVERAGE,
    SIGNAL_TREND_MATCHES,
    STATBOTICS_EPA_ENABLED,
    THROUGHPUT_TREND_DELTA_THRESHOLD,
    RELIABILITY_TREND_DELTA_THRESHOLD,
    CYCLE_TREND_DELTA_THRESHOLD,
    PENALTY_TREND_DELTA_THRESHOLD,
    _PUBLIC_RATING_CEILING,
    _PUBLIC_RATING_CENTER,
    _PUBLIC_RATING_FLOOR,
    _PUBLIC_RATING_SLOPE,
)

def build_rating_row(
    *,
    event_key: str,
    team_key: str,
    final_rating: float,
    confidence: float,
    robot_level: float,
    driver_skill: float,
    results_anchor: float,
    throughput: float,
    shift_productivity: float,
    capacity_utilization: float,
    endgame: float,
    consistency: float,
    pros: list[dict[str, Any]],
    cons: list[dict[str, Any]],
    combined_evidence: list[dict[str, Any]],
    details_json: dict[str, Any],
    now: Any,
) -> models.EventTeamRating:
    # Construct an ``EventTeamRating`` DB row.

    return models.EventTeamRating(
        event_key=event_key,
        team_key=team_key,
        rating_0_100=round(final_rating, 3),
        confidence_0_1=round(confidence, 4),
        robot_level_0_100=round(robot_level, 3),
        driver_skill_0_100=round(driver_skill, 3),
        results_anchor=round(results_anchor, 3),
        throughput=round(throughput, 3),
        shift_productivity=round(shift_productivity, 3),
        capacity_utilization=round(capacity_utilization, 3),
        endgame=round(endgame, 3),
        consistency=round(consistency, 3),
        pros_json=pros,
        cons_json=cons,
        evidence_json=combined_evidence[:12],
        details_json=details_json,
        model_version=MODEL_VERSION,
        updated_at=now,
    )

def build_details_json(
    *,
    rating_algorithm_mode: str,
    base_rating: float,
    performance_score: float,
    penalty_deduction: float,
    raw_final_rating: float,
    sparse_rating_guard: dict[str, Any],
    results_anchor: float,
    driver_skill: float,
    robot_level: float,
    auto_contribution_score: float,
    anti_defense_score: float,
    anti_defense_perf_weight_applied: float,
    anti_defense_base_weight_applied: float,
    manual_points_impact: float,
    rp_contribution_score: float,
    penalty_discipline: float,
    throughput_trend_score: float,
    reliability_trend_score: float,
    cycle_trend_score: float,
    penalty_trend_score: float,
    statbotics_epa_percentile: float | None,
    ml_predicted_percentile: float | None,
    ml_blend_weight: float,
    ml_blend_applied: bool,
    opr_pct_value: float,
    ccwm_pct_value: float,
    dpr_defense_pct_value: float,
    statbotics_epa_value: float | None,
    feature_raw: dict[str, float | None],
    support: dict[str, float],
    epa_context: dict[str, Any],
    anti_defense_drop_index: float | None,
    anti_defense_pressure_coverage: float,
    anti_defense_stage_multiplier: float,
    anti_defense_stage_early_share: float,
    anti_defense_stage_late_share: float,
    anti_defense_stage_elims_share: float,
    anti_defense_tier: str,
    penalty_points_per_match: float,
    severe_penalty_rate: float,
    throughput_trend_delta: float,
    reliability_trend_delta: float,
    cycle_trend_delta: float,
    penalty_trend_delta: float,
    throughput: float,
    shift_productivity: float,
    capacity_utilization: float,
    endgame: float,
    consistency: float,
    defense_presence_score: float,
    manual_context: dict[str, Any],
    use_fallback_model: bool,
    video_findings_count: int,
    external_signal_count: int,
    fallback_signal_availability: dict[str, bool],
    official_match_support: float,
    ml_shadow_enabled: bool,
    ml_model_key: str,
    ml_rollout_key: str,
    ml_rollout_active: bool,
    ml_predicted_bps: float | None,
    ml_team_strength_payload: dict[str, Any],
    findings_count: int,
    gate_config: dict[str, Any],
    raw_findings_count: int,
    excluded_findings_count: int,
    elite_dimensions: dict[str, Any],
    role_classification: dict[str, Any],
    now: Any,
) -> dict[str, Any]:
    # Build the full ``details_json`` payload for a rating row.

    return {
        "algorithm_mode": rating_algorithm_mode,
        "base_rating": round(base_rating, 4),
        "performance_score": round(performance_score, 4),
        "penalty_deduction": round(penalty_deduction, 4),
        "raw_final_rating_0_100": round(raw_final_rating, 4),
        "final_rating_scale": {
            "label": "public_calibrated_v2_forgiving",
            "center": _PUBLIC_RATING_CENTER,
            "slope": _PUBLIC_RATING_SLOPE,
            "floor": _PUBLIC_RATING_FLOOR,
            "ceiling": _PUBLIC_RATING_CEILING,
        },
        "sparse_rating_guard": sparse_rating_guard,
        "model_components": {
            "results_anchor": round(results_anchor, 4),
            "performance": round(performance_score, 4),
            "driver_skill": round(driver_skill, 4),
            "robot_level": round(robot_level, 4),
            "auto_contribution": round(auto_contribution_score, 4),
            "anti_defense": round(anti_defense_score, 4),
            "anti_defense_perf_weight_applied": round(anti_defense_perf_weight_applied, 4),
            "anti_defense_base_weight_applied": round(anti_defense_base_weight_applied, 4),
            "manual_points_impact": round(manual_points_impact, 4),
            "rp_contribution": round(rp_contribution_score, 4),
            "penalty_discipline": round(penalty_discipline, 4),
            "throughput_trend": round(throughput_trend_score, 4),
            "reliability_trend": round(reliability_trend_score, 4),
            "cycle_trend": round(cycle_trend_score, 4),
            "penalty_trend": round(penalty_trend_score, 4),
            "statbotics_epa_baseline": (
                round(statbotics_epa_percentile, 4)
                if isinstance(statbotics_epa_percentile, (int, float))
                else None
            ),
            "ml_team_strength_percentile": (
                round(float(ml_predicted_percentile), 4)
                if ml_predicted_percentile is not None
                else None
            ),
            "ml_team_strength_blend_weight": (
                round(float(ml_blend_weight), 4)
                if ml_blend_applied
                else 0.0
            ),
        },
        "results_anchor_components": {
            "opr_pct": round(opr_pct_value, 4),
            "ccwm_pct": round(ccwm_pct_value, 4),
            "dpr_defense_pct": round(dpr_defense_pct_value, 4),
            "statbotics_epa_pct": (
                round(statbotics_epa_percentile, 4)
                if isinstance(statbotics_epa_percentile, (int, float))
                else None
            ),
            "statbotics_epa_weight_applied": (
                round(RESULTS_ANCHOR_EPA_WEIGHT, 4)
                if feature_raw.get("statbotics_norm_epa") is not None
                else 0.0
            ),
        },
        "penalty_model": {
            "minor_foul_points": MINOR_FOUL_POINTS,
            "major_foul_points": MAJOR_FOUL_POINTS,
            "impact_multipliers": {
                "net_points": PENALTY_IMPACT_NET_POINTS,
                "subscores": PENALTY_IMPACT_SUBSCORES,
                "driver": PENALTY_IMPACT_DRIVER,
                "base_rating": PENALTY_IMPACT_BASE_RATING,
            },
            "penalty_points_per_match": round(penalty_points_per_match, 4),
            "severe_penalty_rate": round(severe_penalty_rate, 4),
            "explicit_penalty_points_per_match": round(
                float(feature_raw.get("explicit_penalty_points_per_match") or 0.0),
                4,
            ),
            "inferred_penalty_points_per_match": round(
                float(feature_raw.get("inferred_penalty_points_per_match") or 0.0),
                4,
            ),
            "protected_zone_pressure": round(
                float(feature_raw.get("protected_zone_pressure") or 0.0),
                4,
            ),
        },
        "anti_defense_model": {
            "tier": anti_defense_tier,
            "drop_index_0_1": (
                round(float(anti_defense_drop_index), 4)
                if anti_defense_drop_index is not None
                else None
            ),
            "elite_drop_threshold_0_1": ANTI_DEFENSE_ELITE_DROP_PCT,
            "good_drop_threshold_0_1": ANTI_DEFENSE_GOOD_DROP_PCT,
            "pressure_coverage_0_1": round(anti_defense_pressure_coverage, 4),
            "scoring_drop_under_pressure_0_1": (
                round(float(feature_raw.get("anti_defense_scoring_drop") or 0.0), 4)
                if feature_raw.get("anti_defense_scoring_drop") is not None
                else None
            ),
            "cycle_increase_under_pressure_0_1": (
                round(float(feature_raw.get("anti_defense_cycle_increase") or 0.0), 4)
                if feature_raw.get("anti_defense_cycle_increase") is not None
                else None
            ),
            "reliability_drop_under_pressure_0_1": (
                round(float(feature_raw.get("anti_defense_reliability_drop") or 0.0), 4)
                if feature_raw.get("anti_defense_reliability_drop") is not None
                else None
            ),
            "escape_time_proxy_sec": (
                round(float(feature_raw.get("anti_defense_escape_time_sec") or 0.0), 3)
                if feature_raw.get("anti_defense_escape_time_sec") is not None
                else None
            ),
            "obstacle_efficiency": (
                round(float(feature_raw.get("anti_defense_obstacle_eff") or 0.0), 4)
                if feature_raw.get("anti_defense_obstacle_eff") is not None
                else None
            ),
            "pressured_matches": int(feature_raw.get("anti_defense_pressured_matches") or 0),
            "open_matches": int(feature_raw.get("anti_defense_open_matches") or 0),
            "stage_multiplier_applied": round(anti_defense_stage_multiplier, 4),
            "stage_early_quals_share": round(anti_defense_stage_early_share, 4),
            "stage_late_quals_share": round(anti_defense_stage_late_share, 4),
            "stage_elims_share": round(anti_defense_stage_elims_share, 4),
            "stage_config": {
                "early_quals_multiplier": ANTIDEFENSE_STAGE_EARLY_QUALS_MULTIPLIER,
                "late_quals_multiplier": ANTIDEFENSE_STAGE_LATE_QUALS_MULTIPLIER,
                "elims_multiplier": ANTIDEFENSE_STAGE_ELIMS_MULTIPLIER,
                "support_matches_target": ANTIDEFENSE_STAGE_SUPPORT_MATCHES,
            },
        },
        "subscores": {
            "results_anchor": round(results_anchor, 4),
            "throughput": round(throughput, 4),
            "shift_productivity": round(shift_productivity, 4),
            "capacity_utilization": round(capacity_utilization, 4),
            "endgame": round(endgame, 4),
            "auto_contribution": round(auto_contribution_score, 4),
            "anti_defense": round(anti_defense_score, 4),
            "manual_points_impact": round(manual_points_impact, 4),
            "rp_contribution": round(rp_contribution_score, 4),
            "defense_presence": round(defense_presence_score, 4),
            "consistency": round(consistency, 4),
            "penalty_discipline": round(penalty_discipline, 4),
        },
        "trends": {
            "throughput_delta": round(throughput_trend_delta, 5),
            "reliability_delta": round(reliability_trend_delta, 5),
            "cycle_time_delta": round(cycle_trend_delta, 5),
            "penalty_delta": round(penalty_trend_delta, 5),
            "throughput_score": round(throughput_trend_score, 4),
            "reliability_score": round(reliability_trend_score, 4),
            "cycle_score": round(cycle_trend_score, 4),
            "penalty_score": round(penalty_trend_score, 4),
        },
        "epa_context": {
            "enabled": STATBOTICS_EPA_ENABLED,
            "available": bool(epa_context.get("available")),
            "source": epa_context.get("source"),
            "raw_value": statbotics_epa_value,
            "percentile": (
                round(statbotics_epa_percentile, 4)
                if isinstance(statbotics_epa_percentile, (int, float))
                else None
            ),
            "performance_blend_weight": PERFORMANCE_EPA_BLEND,
            "detail": epa_context.get("detail"),
        },
        "recency_config": {
            "match_window": RECENT_MATCH_WINDOW,
            "priority_window": RECENT_PRIORITY_WINDOW,
            "priority_weight": RECENT_PRIORITY_WEIGHT,
            "base_weight": RECENT_BASE_WEIGHT,
        },
        "weighting": {
            "performance_auto_weight": PERFORMANCE_AUTO_WEIGHT,
            "base_auto_weight": BASE_AUTO_WEIGHT,
            "performance_antidefense_weight": PERFORMANCE_ANTIDEFENSE_WEIGHT,
            "base_antidefense_weight": BASE_ANTIDEFENSE_WEIGHT,
        },
        "ml_shadow": {
            "enabled": bool(ml_shadow_enabled),
            "team_strength": {
                "model_key": ml_model_key,
                "model_version": ml_team_strength_payload.get("model_version"),
                "prediction_ok": bool(ml_team_strength_payload.get("prediction_ok")),
                "reason": ml_team_strength_payload.get("reason"),
                "detail": ml_team_strength_payload.get("detail"),
                "rollout_key": ml_rollout_key,
                "rollout_active": bool(ml_rollout_active),
                "blend_knob": round(max(0.0, min(1.0, float(getattr(settings, "ml_team_strength_blend", 0.0) or 0.0))), 4),
                "blend_applied": bool(ml_blend_applied),
                "blend_weight": round(float(ml_blend_weight), 4),
                "predicted_active_bps": (
                    round(float(ml_predicted_bps), 6)
                    if ml_predicted_bps is not None
                    else None
                ),
                "predicted_strength_percentile_0_100": (
                    round(float(ml_predicted_percentile), 4)
                    if ml_predicted_percentile is not None
                    else None
                ),
            },
        },
        "fallback_model": {
            "active": bool(use_fallback_model),
            "label": FALLBACK_MODEL_LABEL if use_fallback_model else None,
            "trigger": "no_video_findings",
            "video_findings_count": int(video_findings_count),
            "external_signal_count": int(external_signal_count),
            "external_signal_availability": fallback_signal_availability,
            "official_match_support_0_1": round(official_match_support, 4),
        },
        "trend_thresholds": {
            "throughput_delta": THROUGHPUT_TREND_DELTA_THRESHOLD,
            "reliability_delta": RELIABILITY_TREND_DELTA_THRESHOLD,
            "cycle_delta": CYCLE_TREND_DELTA_THRESHOLD,
            "penalty_delta": PENALTY_TREND_DELTA_THRESHOLD,
        },
        "manual_context": manual_context,
        "raw_features": feature_raw,
        "confidence_support": support,
        "pros_cons_config": {
            "signal_min_confidence": SIGNAL_MIN_CONFIDENCE,
            "signal_strong_confidence": SIGNAL_STRONG_CONFIDENCE,
            "signal_min_matches": SIGNAL_MIN_MATCHES,
            "signal_strong_matches": SIGNAL_STRONG_MATCHES,
            "signal_trend_matches": SIGNAL_TREND_MATCHES,
            "signal_trend_coverage": SIGNAL_TREND_COVERAGE,
        },
        "elite_dimensions": elite_dimensions,
        "role_classification": role_classification,
        "match_count": findings_count,
        "quality_gate": {
            **gate_config,
            "raw_match_count": int(raw_findings_count),
            "accepted_match_count": int(findings_count),
            "excluded_match_count": int(excluded_findings_count),
        },
        "computed_at": now.isoformat(),
    }

def build_response_payload(
    persisted_rows: list[Any],
) -> list[dict[str, Any]]:
    # Build the API response list from persisted rating rows.

    payload = []
    for row, team in persisted_rows:
        payload.append(
            {
                "event_key": row.event_key,
                "team_key": row.team_key,
                "team_number": team.team_number if team else None,
                "nickname": team.nickname if team else None,
                "rating_0_100": row.rating_0_100,
                "confidence_0_1": row.confidence_0_1,
                "robot_level_0_100": row.robot_level_0_100,
                "driver_skill_0_100": row.driver_skill_0_100,
                "subscores": {
                    "results_anchor": row.results_anchor,
                    "throughput": row.throughput,
                    "shift_productivity": row.shift_productivity,
                    "capacity_utilization": row.capacity_utilization,
                    "endgame": row.endgame,
                    "auto_contribution": ((row.details_json or {}).get("subscores") or {}).get(
                        "auto_contribution"
                    ),
                    "anti_defense": ((row.details_json or {}).get("subscores") or {}).get(
                        "anti_defense"
                    ),
                    "manual_points_impact": ((row.details_json or {}).get("subscores") or {}).get(
                        "manual_points_impact"
                    ),
                    "rp_contribution": ((row.details_json or {}).get("subscores") or {}).get(
                        "rp_contribution"
                    ),
                    "defense_presence": ((row.details_json or {}).get("subscores") or {}).get(
                        "defense_presence"
                    ),
                    "consistency": row.consistency,
                    "penalty_discipline": ((row.details_json or {}).get("subscores") or {}).get(
                        "penalty_discipline"
                    ),
                },
                "pros": row.pros_json or [],
                "cons": row.cons_json or [],
                "evidence": row.evidence_json or [],
                "model_version": row.model_version,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return payload
