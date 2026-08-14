# Pros / cons signal generation for the rating pipeline.
#
# Given computed scores and features, produce the structured signal lists
# (pros, cons) that appear in each team's rating row.

from __future__ import annotations

from typing import Any

from app.db import models
from app.services.ratings.anti_defense import _anti_defense_tier
from app.services.ratings.constants import (
    CYCLE_TREND_DELTA_THRESHOLD,
    FALLBACK_MODEL_LABEL,
    PENALTY_TREND_DELTA_THRESHOLD,
    RELIABILITY_TREND_DELTA_THRESHOLD,
    SIGNAL_MIN_CONFIDENCE,
    SIGNAL_MIN_MATCHES,
    SIGNAL_STRONG_CONFIDENCE,
    SIGNAL_STRONG_MATCHES,
    SIGNAL_TREND_COVERAGE,
    SIGNAL_TREND_MATCHES,
    THROUGHPUT_TREND_DELTA_THRESHOLD,
)
from app.services.ratings.game_context import _penalty_event_evidence
from app.services.ratings.helpers import _evidence_for_metric
from app.services.ratings.signals import (
    _dedupe_signals,
    _ensure_minimum_pros_cons_signals,
    _infer_signal_metric,
    _make_signal,
)

def generate_team_signals(
    *,
    team_key: str,
    feature_raw: dict[str, float | None],
    confidence: float,
    findings: list[models.TeamMatchFinding],
    findings_count: int,
    support: dict[str, float],
    use_fallback_model: bool,
    rating_algorithm_mode: str,
    # Score values
    throughput: float,
    shift_productivity: float,
    capacity_utilization: float,
    endgame: float,
    auto_contribution_score: float,
    manual_points_impact: float,
    rp_contribution_score: float,
    defense_presence_score: float,
    anti_defense_score: float,
    penalty_discipline: float,
    driver_skill: float,
    robot_level: float,
    consistency: float,
    # Percentile and raw values
    capacity_pct_value: float,
    throughput_trend_score: float,
    reliability_trend_score: float,
    cycle_trend_score: float,
    penalty_trend_score: float,
    throughput_trend_delta: float,
    reliability_trend_delta: float,
    cycle_trend_delta: float,
    penalty_trend_delta: float,
    penalty_points_per_match: float,
    severe_penalty_rate: float,
    anti_defense_drop_index: float | None,
    anti_defense_pressure_coverage: float,
    statbotics_epa_value: float | None,
    statbotics_epa_percentile: float | None,
    opr_pct_value: float,
    ccwm_pct_value: float,
    # Optional ML
    ml_blend_applied: bool,
    ml_predicted_bps: float | None,
    ml_predicted_percentile: float | None,
    # Evidence source
    events_by_team_match: dict[str, list[models.MatchEvent]],
    video_findings_count: int,
    fallback_signal_availability: dict[str, bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Return ``(pros, cons)`` signal lists for a single team.

    pros: list[dict[str, Any]] = []
    cons: list[dict[str, Any]] = []
    matches_observed = findings_count
    trend_coverage_factor = float(support.get("trend_coverage_factor") or 0.0)
    signal_medium_ready = (
        confidence >= SIGNAL_MIN_CONFIDENCE
        and matches_observed >= SIGNAL_MIN_MATCHES
    )
    signal_strong_ready = (
        confidence >= SIGNAL_STRONG_CONFIDENCE
        and matches_observed >= SIGNAL_STRONG_MATCHES
    )
    signal_trend_ready = (
        confidence >= SIGNAL_STRONG_CONFIDENCE
        and matches_observed >= SIGNAL_TREND_MATCHES
        and trend_coverage_factor >= SIGNAL_TREND_COVERAGE
    )
    throughput_trend_floor = max(0.01, THROUGHPUT_TREND_DELTA_THRESHOLD)
    reliability_trend_floor = max(0.01, RELIABILITY_TREND_DELTA_THRESHOLD)
    cycle_trend_floor = max(0.01, CYCLE_TREND_DELTA_THRESHOLD)
    penalty_trend_floor = max(0.01, PENALTY_TREND_DELTA_THRESHOLD)
    anti_defense_tier_label = _anti_defense_tier(anti_defense_drop_index)

    if use_fallback_model:
        if not str(rating_algorithm_mode).startswith(FALLBACK_MODEL_LABEL):
            pass  # caller handles rating_algorithm_mode
        cons.append(
            _make_signal(
                "No analyzed clips yet",
                float(video_findings_count),
                50.0,
                [],
                category="coverage",
                rationale="Fallback external-intel model is active until video-analyzed clips are available.",
                impact="risk_up",
                signal_confidence=confidence,
            )
        )
        if statbotics_epa_percentile is not None:
            if statbotics_epa_percentile >= 78.0:
                pros.append(
                    _make_signal(
                        "High external EPA baseline",
                        float(statbotics_epa_value or 0.0),
                        statbotics_epa_percentile,
                        [],
                        category="external",
                        rationale="Statbotics EPA supports a strong expected baseline while clip analysis is pending.",
                        impact="upside",
                        signal_confidence=confidence,
                    )
                )
            elif statbotics_epa_percentile <= 24.0:
                cons.append(
                    _make_signal(
                        "External EPA baseline is low",
                        float(statbotics_epa_value or 0.0),
                        statbotics_epa_percentile,
                        [],
                        category="external",
                        rationale="External EPA baseline is below most teams in this event context.",
                        impact="risk_up",
                        signal_confidence=confidence,
                    )
                )
        if fallback_signal_availability["opr"] and opr_pct_value >= 75.0:
            pros.append(
                _make_signal(
                    "Strong OPR projection",
                    float(feature_raw.get("opr") or 0.0),
                    opr_pct_value,
                    [],
                    category="external",
                    rationale="Event OPR suggests meaningful expected points contribution.",
                    impact="upside",
                    signal_confidence=confidence,
                )
            )
        if fallback_signal_availability["ccwm"] and ccwm_pct_value <= 25.0:
            cons.append(
                _make_signal(
                    "Weak win-margin projection",
                    float(feature_raw.get("ccwm") or 0.0),
                    ccwm_pct_value,
                    [],
                    category="external",
                    rationale="Event CCWM is currently low, indicating limited net match leverage.",
                    impact="risk_up",
                    signal_confidence=confidence,
                )
            )

    if ml_blend_applied and ml_predicted_percentile is not None and signal_medium_ready:
        if ml_predicted_percentile >= 78.0:
            pros.append(
                _make_signal(
                    "ML throughput projection is strong",
                    float(ml_predicted_bps or 0.0),
                    float(ml_predicted_percentile),
                    [],
                    category="ml_shadow",
                    rationale="Shadow model projects above-field throughput trend for this team profile.",
                    impact="upside",
                    signal_confidence=confidence,
                )
            )
        elif ml_predicted_percentile <= 24.0:
            cons.append(
                _make_signal(
                    "ML throughput projection is below field",
                    float(ml_predicted_bps or 0.0),
                    float(ml_predicted_percentile),
                    [],
                    category="ml_shadow",
                    rationale="Shadow model projects below-field throughput trend for this team profile.",
                    impact="risk_up",
                    signal_confidence=confidence,
                )
            )

    if throughput >= 80.0 and signal_strong_ready:
        pros.append(
            _make_signal(
                "Throughput elite",
                float(feature_raw.get("bps_median") or 0.0),
                throughput,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=True),
            )
        )
    if shift_productivity >= 80.0 and signal_strong_ready:
        pros.append(
            _make_signal(
                "Shift dominance",
                float(feature_raw.get("shift_productivity") or 0.0),
                shift_productivity,
                _evidence_for_metric(findings, "cycle_time_sec", descending=False),
            )
        )
    if endgame >= 80.0 and signal_medium_ready:
        pros.append(
            _make_signal(
                "Tower threat",
                float(feature_raw.get("climb_output") or 0.0),
                endgame,
                _evidence_for_metric(findings, "climb_success_prob", descending=True),
            )
        )
    if auto_contribution_score >= 72.0 and signal_medium_ready:
        pros.append(
            _make_signal(
                "Strong autonomous impact",
                float(feature_raw.get("auto_points_est") or 0.0),
                auto_contribution_score,
                _evidence_for_metric(findings, "auto_contribution", descending=True),
            )
        )
    if rp_contribution_score >= 75.0 and signal_strong_ready:
        pros.append(
            _make_signal(
                "RP threshold contributor",
                float(feature_raw.get("rp_contribution_raw") or 0.0),
                rp_contribution_score,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=True),
            )
        )
    if manual_points_impact >= 82.0 and signal_strong_ready:
        pros.append(
            _make_signal(
                "High net point impact",
                float(feature_raw.get("expected_net_points") or 0.0),
                manual_points_impact,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=True),
            )
        )
    if driver_skill >= 80.0 and robot_level <= 60.0 and signal_strong_ready:
        pros.append(
            _make_signal(
                "High driver carry",
                driver_skill,
                driver_skill,
                _evidence_for_metric(findings, "reliability_score", descending=True),
            )
        )
    if robot_level >= 85.0 and driver_skill >= 60.0 and signal_medium_ready:
        pros.append(
            _make_signal(
                "High ceiling",
                robot_level,
                robot_level,
                _evidence_for_metric(findings, "auto_contribution", descending=True),
            )
        )
    if defense_presence_score >= 80.0 and throughput >= 55.0 and signal_medium_ready:
        pros.append(
            _make_signal(
                "Two-way value (offense + defense)",
                float(feature_raw.get("defense_presence") or 0.0),
                defense_presence_score,
                _evidence_for_metric(findings, "defensive_engagement_sec", descending=True),
            )
        )
    if (
        anti_defense_score >= 76.0
        and anti_defense_pressure_coverage >= 0.35
        and signal_medium_ready
    ):
        pros.append(
            _make_signal(
                "Pressure-resistant scoring",
                max(0.0, 1.0 - float(anti_defense_drop_index or 0.0)),
                anti_defense_score,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=True),
                category="resilience",
                rationale=(
                    f"{anti_defense_tier_label}: maintained output with low defended drop."
                ),
                impact="upside",
                signal_confidence=confidence,
            )
        )
    if (
        signal_trend_ready
        and throughput_trend_score >= 72.0
        and throughput_trend_delta >= (1.1 * throughput_trend_floor)
    ):
        pros.append(
            _make_signal(
                "Output trend accelerating",
                throughput_trend_delta,
                throughput_trend_score,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=True),
                category="trend",
                rationale="Recent matches are scoring faster than earlier event samples.",
                impact="upside",
                trend_delta=throughput_trend_delta,
                signal_confidence=confidence,
            )
        )
    if (
        signal_trend_ready
        and reliability_trend_score >= 72.0
        and reliability_trend_delta >= (1.1 * reliability_trend_floor)
    ):
        pros.append(
            _make_signal(
                "Reliability trend improving",
                reliability_trend_delta,
                reliability_trend_score,
                _evidence_for_metric(findings, "reliability_score", descending=True),
                category="trend",
                rationale="Recent matches show stronger uptime/reliability than prior samples.",
                impact="risk_down",
                trend_delta=reliability_trend_delta,
                signal_confidence=confidence,
            )
        )
    if (
        statbotics_epa_percentile is not None
        and statbotics_epa_percentile >= 85.0
        and signal_medium_ready
    ):
        pros.append(
            _make_signal(
                "Strong EPA baseline",
                float(statbotics_epa_value or 0.0),
                statbotics_epa_percentile,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=True),
                category="external",
                rationale="Statbotics EPA trend supports a strong baseline contribution profile.",
                impact="upside",
                signal_confidence=confidence,
            )
        )

    if capacity_pct_value >= 70.0 and capacity_utilization <= 30.0 and signal_medium_ready:
        cons.append(
            _make_signal(
                "Underutilizes capacity",
                float(feature_raw.get("capacity_utilization") or 0.0),
                capacity_utilization,
                _evidence_for_metric(findings, "cycle_time_sec", descending=True),
            )
        )
    if robot_level >= 75.0 and driver_skill <= 30.0 and signal_strong_ready:
        cons.append(
            _make_signal(
                "Execution limiting output",
                driver_skill,
                driver_skill,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=False),
            )
        )
    if throughput <= 22.0 and signal_medium_ready:
        cons.append(
            _make_signal(
                "Low shooting throughput",
                float(feature_raw.get("bps_median") or 0.0),
                throughput,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=False),
            )
        )
    if endgame <= 26.0 and signal_medium_ready:
        cons.append(
            _make_signal(
                "Endgame missing",
                float(feature_raw.get("climb_output") or 0.0),
                endgame,
                _evidence_for_metric(findings, "climb_success_prob", descending=False),
            )
        )
    if auto_contribution_score <= 32.0 and signal_medium_ready:
        cons.append(
            _make_signal(
                "Weak autonomous output",
                float(feature_raw.get("auto_points_est") or 0.0),
                auto_contribution_score,
                _evidence_for_metric(findings, "auto_contribution", descending=False),
            )
        )
    if rp_contribution_score <= 30.0 and signal_strong_ready:
        cons.append(
            _make_signal(
                "Low RP threshold contribution",
                float(feature_raw.get("rp_contribution_raw") or 0.0),
                rp_contribution_score,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=False),
            )
        )
    if manual_points_impact <= 28.0 and confidence >= 0.5 and signal_strong_ready:
        cons.append(
            _make_signal(
                "Low net point impact",
                float(feature_raw.get("expected_net_points") or 0.0),
                manual_points_impact,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=False),
            )
        )
    if consistency <= 35.0 and confidence >= 0.55 and signal_strong_ready:
        cons.append(
            _make_signal(
                "Inconsistent output",
                float(feature_raw.get("consistency_var") or 0.0),
                consistency,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=True),
            )
        )
    if (
        anti_defense_score <= 45.0
        and anti_defense_tier_label == "Weak anti-defense"
        and anti_defense_pressure_coverage >= 0.30
        and signal_medium_ready
    ):
        cons.append(
            _make_signal(
                "Drops under defensive pressure",
                float(anti_defense_drop_index or 0.0),
                anti_defense_score,
                _evidence_for_metric(findings, "cycle_time_sec", descending=True),
                category="resilience",
                rationale=(
                    f"{anti_defense_tier_label}: defended performance drop is above target band."
                ),
                impact="risk_up",
                signal_confidence=confidence,
            )
        )
    if (
        signal_medium_ready
        and penalty_discipline <= 32.0
        and (penalty_points_per_match >= 2.5 or severe_penalty_rate >= 0.08)
    ):
        cons.append(
            _make_signal(
                "Penalty risk impacts expected points",
                penalty_points_per_match,
                penalty_discipline,
                _penalty_event_evidence(events_by_team_match, limit=3),
            )
        )
    if (
        signal_trend_ready
        and throughput_trend_score <= 30.0
        and throughput_trend_delta <= (-1.1 * throughput_trend_floor)
    ):
        cons.append(
            _make_signal(
                "Output trend cooling",
                throughput_trend_delta,
                throughput_trend_score,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=False),
                category="trend",
                rationale="Recent matches are producing less scoring throughput than earlier event samples.",
                impact="downside",
                trend_delta=throughput_trend_delta,
                signal_confidence=confidence,
            )
        )
    if (
        signal_trend_ready
        and reliability_trend_score <= 30.0
        and reliability_trend_delta <= (-1.1 * reliability_trend_floor)
    ):
        cons.append(
            _make_signal(
                "Reliability trend declining",
                reliability_trend_delta,
                reliability_trend_score,
                _evidence_for_metric(findings, "reliability_score", descending=False),
                category="trend",
                rationale="Uptime reliability has dropped in the most recent sample window.",
                impact="risk_up",
                trend_delta=reliability_trend_delta,
                signal_confidence=confidence,
            )
        )
    if (
        signal_trend_ready
        and cycle_trend_score <= 30.0
        and cycle_trend_delta >= (1.1 * cycle_trend_floor)
    ):
        cons.append(
            _make_signal(
                "Cycle timing regressing",
                cycle_trend_delta,
                cycle_trend_score,
                _evidence_for_metric(findings, "cycle_time_sec", descending=True),
                category="trend",
                rationale="Cycle times are drifting slower in recent matches.",
                impact="downside",
                trend_delta=cycle_trend_delta,
                signal_confidence=confidence,
            )
        )
    if (
        signal_trend_ready
        and penalty_trend_score <= 30.0
        and penalty_trend_delta >= (1.1 * penalty_trend_floor)
    ):
        cons.append(
            _make_signal(
                "Penalty pressure worsening",
                penalty_trend_delta,
                penalty_trend_score,
                _penalty_event_evidence(events_by_team_match, limit=3),
                category="discipline",
                rationale="Explicit penalty incidence is increasing in recent matches.",
                impact="risk_up",
                trend_delta=penalty_trend_delta,
                signal_confidence=confidence,
            )
        )
    if (
        statbotics_epa_percentile is not None
        and statbotics_epa_percentile <= 20.0
        and signal_medium_ready
    ):
        cons.append(
            _make_signal(
                "Low EPA baseline",
                float(statbotics_epa_value or 0.0),
                statbotics_epa_percentile,
                _evidence_for_metric(findings, "fuel_scoring_rate", descending=False),
                category="external",
                rationale="Statbotics EPA baseline is currently below most teams in the field.",
                impact="risk_up",
                signal_confidence=confidence,
            )
        )
    if penalty_discipline >= 85.0 and penalty_points_per_match <= 2.2 and confidence >= SIGNAL_STRONG_CONFIDENCE:
        pros.append(
            _make_signal(
                "Disciplined play",
                penalty_points_per_match,
                penalty_discipline,
                _evidence_for_metric(findings, "reliability_score", descending=True),
            )
        )
    if confidence < SIGNAL_MIN_CONFIDENCE and not pros and not cons:
        cons.append(
            _make_signal(
                "Limited current evidence",
                float(matches_observed),
                50.0,
                [],
                category="coverage",
                rationale="Not enough accepted analyzed matches are available for high-confidence strengths/risks.",
                impact="risk_up",
                signal_confidence=confidence,
            )
        )

    pros, cons = _ensure_minimum_pros_cons_signals(
        pros=pros,
        cons=cons,
        confidence=confidence,
        findings=findings,
        use_fallback_model=use_fallback_model,
        throughput=throughput,
        auto_contribution_score=auto_contribution_score,
        endgame=endgame,
        defense_presence_score=defense_presence_score,
        anti_defense_score=anti_defense_score,
        penalty_discipline=penalty_discipline,
    )

    pros = _dedupe_signals(pros)
    cons = _dedupe_signals(cons)

    for signal in pros:
        signal.setdefault("impact", "positive")
        signal.setdefault("category", "strength")
        signal.setdefault("signal_confidence_0_1", round(confidence, 4))
        signal.setdefault("confidence_0_1", round(confidence, 4))
        signal.setdefault("sample_size", int(matches_observed))
        signal.setdefault("metric", _infer_signal_metric(None, signal.get("evidence", []), str(signal.get("label") or "")))
        signal.setdefault(
            "delta",
            round(float(signal.get("percentile") or 50.0) - 50.0, 4),
        )
    for signal in cons:
        signal.setdefault("impact", "negative")
        signal.setdefault("category", "risk")
        signal.setdefault("signal_confidence_0_1", round(confidence, 4))
        signal.setdefault("confidence_0_1", round(confidence, 4))
        signal.setdefault("sample_size", int(matches_observed))
        signal.setdefault("metric", _infer_signal_metric(None, signal.get("evidence", []), str(signal.get("label") or "")))
        signal.setdefault(
            "delta",
            round(float(signal.get("percentile") or 50.0) - 50.0, 4),
        )

    pros = sorted(
        pros,
        key=lambda item: float(item.get("percentile") or 0.0),
        reverse=True,
    )[:8]
    cons = sorted(
        cons,
        key=lambda item: float(item.get("percentile") or 100.0),
    )[:8]

    return pros, cons
