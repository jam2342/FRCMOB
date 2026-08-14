# Event rating model — main orchestrator.
#
# Helper functions and constants have been split into sub-modules:
# rating_constants, rating_helpers, rating_anti_defense,
# rating_signals, rating_game_context, rating_statbotics,
# rating_data_loader, rating_feature_extraction, rating_scoring,
# rating_signal_generation, rating_output_builder.
#
# This file retains ``recompute_event_ratings`` and backward-compatible
# re-exports so existing ``from app.services.ratings.model import …``
# statements continue to work.

from __future__ import annotations

import time
from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.analysis.elite_robot import EliteRobotAnalyzer
from app.services.scouting_rooms.elite_detector import RoleClassifier
from app.services.utils import _as_float, _clamp, _mean, _weighted_mean, _weighted_median, _weighted_std  # noqa: F401

from app.services.ratings.constants import (  # noqa: F401
    ANTI_DEFENSE_ELITE_DROP_PCT,
    ANTI_DEFENSE_GOOD_DROP_PCT,
    ANTIDEFENSE_STAGE_EARLY_QUALS_MULTIPLIER,
    ANTIDEFENSE_STAGE_ELIMS_MULTIPLIER,
    ANTIDEFENSE_STAGE_LATE_QUALS_MULTIPLIER,
    ANTIDEFENSE_STAGE_SUPPORT_MATCHES,
    BASE_ANTIDEFENSE_WEIGHT,
    BASE_AUTO_WEIGHT,
    CYCLE_TREND_DELTA_THRESHOLD,
    FALLBACK_MODEL_LABEL,
    MAJOR_FOUL_POINTS,
    MINOR_FOUL_POINTS,
    MODEL_VERSION,
    PENALTY_EVENT_POINT_WEIGHTS,
    PENALTY_IMPACT_BASE_RATING,
    PENALTY_IMPACT_DRIVER,
    PENALTY_IMPACT_NET_POINTS,
    PENALTY_IMPACT_SUBSCORES,
    PENALTY_TREND_DELTA_THRESHOLD,
    PERFORMANCE_ANTIDEFENSE_WEIGHT,
    PERFORMANCE_AUTO_WEIGHT,
    PERFORMANCE_EPA_BLEND,
    RECENT_BASE_WEIGHT,
    RECENT_MATCH_WINDOW,
    RECENT_PRIORITY_WEIGHT,
    RECENT_PRIORITY_WINDOW,
    RELEVANT_MATCH_EVENT_TYPES,
    RELIABILITY_TREND_DELTA_THRESHOLD,
    RESULTS_ANCHOR_EPA_WEIGHT,
    SIGNAL_MIN_CONFIDENCE,
    SIGNAL_MIN_MATCHES,
    SIGNAL_STRONG_CONFIDENCE,
    SIGNAL_STRONG_MATCHES,
    SIGNAL_TREND_COVERAGE,
    SIGNAL_TREND_MATCHES,
    STATBOTICS_EPA_ENABLED,
    TBA_SCOREBREAKDOWN_SOURCE,
    THROUGHPUT_TREND_DELTA_THRESHOLD,
    _PUBLIC_RATING_CEILING,
    _PUBLIC_RATING_CENTER,
    _PUBLIC_RATING_FLOOR,
    _PUBLIC_RATING_SLOPE,
)
from app.services.ratings.helpers import (  # noqa: F401
    calibrate_public_rating_scale,
    _dedupe_findings_by_match,
    _event_from_official_source,
    _event_meta_number,
    _evidence_for_metric,
    _extract_clip_url,
    _extract_first_number,
    _fit_linear_model,
    _is_active_hub_attempt_event,
    _match_stage,
    _percentile_map,
    _recent_weight_for_index,
    _sort_findings_newest_first,
    _trend_delta_ratio,
    _weighted_score,
)
from app.services.ratings.anti_defense import (  # noqa: F401
    _anti_defense_drop_band_score,
    _anti_defense_stage_multiplier,
    _anti_defense_tier,
)
from app.services.ratings.signals import (  # noqa: F401
    _apply_sparse_rating_guard,
    _dedupe_signals,
    _ensure_minimum_pros_cons_signals,
    _infer_signal_metric,
    _make_signal,
)
from app.services.ratings.game_context import (  # noqa: F401
    _manual_game_context,
    _penalty_event_evidence,
    _rebuilt_active_hub_duration_sec,
)
from app.services.ratings.statbotics import (  # noqa: F401
    _extract_statbotics_epa_value,
    _load_statbotics_epa_by_team,
    _load_statbotics_epa_by_team_async,
)
from app.services.ml.shadow import (
    TEAM_STRENGTH_MODEL_KEY,
    infer_team_strength_shadow_from_rows,
    is_shadow_rollout_active,
)

from app.services.ratings.data_loader import load_event_rating_data
from app.services.ratings.feature_extraction import extract_team_features
from app.services.ratings.scoring import compute_percentile_scores
from app.services.ratings.snapshots import record_event_rating_snapshots
from app.services.ratings.signal_generation import generate_team_signals
from app.services.ratings.output_builder import (
    build_details_json,
    build_rating_row,
    build_response_payload,
)

logger = logging.getLogger(__name__)

def recompute_event_ratings(db: Session, event_key: str) -> dict[str, Any]:
    started = time.perf_counter()

    manual_context = _manual_game_context()

    data = load_event_rating_data(db, event_key, manual_context)

    if not data.team_keys:
        return {
            "ok": True,
            "event_key": event_key,
            "count": 0,
            "ratings": [],
            "duration_sec": round(max(0.0, time.perf_counter() - started), 4),
        }

    features = extract_team_features(data, manual_context)

    scores = compute_percentile_scores(
        data.team_keys,
        features.feature_raw,
        features.confidence_support,
    )

    ml_shadow_enabled = bool(getattr(settings, "ml_shadow_enabled", False))
    ml_team_strength_payload: dict[str, Any] = {
        "enabled": ml_shadow_enabled,
        "prediction_ok": False,
        "model_version": None,
        "reason": "disabled",
        "detail": None,
    }
    ml_team_strength_prediction_by_team: dict[str, float] = {}
    ml_team_strength_percentile_by_team: dict[str, float] = {}

    if ml_shadow_enabled:
        ml_rows: list[dict[str, Any]] = []
        for team_key in data.team_keys:
            confidence_seed = float(scores.confidence_by_team.get(team_key) or 0.0)
            base_components_seed = [
                (0.21, scores.results_anchor_pct[team_key]),
                (0.29, scores.performance_by_team[team_key]),
                (0.16, scores.driver_skill_pct[team_key]),
                (0.11, scores.robot_level_by_team[team_key]),
                (0.09, scores.manual_points_pct[team_key]),
                (0.05, scores.rp_contribution_pct[team_key]),
                (BASE_AUTO_WEIGHT, scores.auto_pct[team_key]),
                (
                    float(scores.anti_defense_base_weight_by_team.get(team_key, BASE_ANTIDEFENSE_WEIGHT)),
                    scores.anti_defense_pct[team_key],
                ),
            ]
            total_seed_weight = max(1e-6, sum(weight for weight, _ in base_components_seed))
            base_seed = sum(weight * value for weight, value in base_components_seed) / total_seed_weight
            raw_seed_rating = _clamp(50.0 + (confidence_seed * (base_seed - 50.0)), 0.0, 100.0)
            ml_rows.append(
                {
                    "event_key": event_key,
                    "team_key": team_key,
                    "rating_0_100": calibrate_public_rating_scale(raw_seed_rating),
                    "confidence_0_1": confidence_seed,
                    "throughput_0_100": scores.throughput_pct[team_key],
                    "driver_skill_0_100": calibrate_public_rating_scale(scores.driver_skill_pct[team_key]),
                    "robot_level_0_100": calibrate_public_rating_scale(scores.robot_level_by_team[team_key]),
                    "endgame_0_100": scores.endgame_pct[team_key],
                    "consistency_0_100": scores.consistency_pct[team_key],
                    "shift_productivity_0_100": scores.shift_pct[team_key],
                    "capacity_utilization_0_100": scores.cap_util_pct[team_key],
                    "auto_contribution_0_100": scores.auto_pct[team_key],
                    "manual_points_impact_0_100": scores.manual_points_pct[team_key],
                    "rp_contribution_0_100": scores.rp_contribution_pct[team_key],
                    "anti_defense_0_100": scores.anti_defense_pct[team_key],
                    "defense_presence_0_100": scores.defense_presence_pct[team_key],
                    "penalty_discipline_0_100": scores.penalty_discipline_pct[team_key],
                    "expected_net_points": features.feature_raw[team_key].get("expected_net_points"),
                    "auto_points_est": features.feature_raw[team_key].get("auto_points_est"),
                    "teleop_points_est": features.feature_raw[team_key].get("teleop_points_est"),
                    "climb_output": features.feature_raw[team_key].get("climb_output"),
                    "uptime_0_1": features.feature_raw[team_key].get("uptime"),
                    "throughput_trend_delta": features.feature_raw[team_key].get("throughput_trend_delta"),
                    "reliability_trend_delta": features.feature_raw[team_key].get("reliability_trend_delta"),
                    "cycle_trend_delta": features.feature_raw[team_key].get("cycle_trend_delta"),
                    "penalty_trend_delta": features.feature_raw[team_key].get("penalty_trend_delta"),
                    "throughput_coverage_0_1": features.feature_raw[team_key].get("throughput_coverage"),
                    "avg_analysis_quality_0_1": features.confidence_support[team_key].get("avg_analysis_quality"),
                    "avg_identity_quality_0_1": features.confidence_support[team_key].get("avg_identity_quality"),
                    "coverage_factor_0_1": features.confidence_support[team_key].get("coverage_factor"),
                    "match_quality_weight_0_1": features.confidence_support[team_key].get("match_quality_weight_factor"),
                    "recent_match_factor_0_1": features.confidence_support[team_key].get("recent_match_factor"),
                    "trend_coverage_factor_0_1": features.confidence_support[team_key].get("trend_coverage_factor"),
                    "match_count": features.findings_count.get(team_key, 0),
                    "excluded_match_count": data.excluded_findings_count_by_team.get(team_key, 0),
                    "video_findings_count": data.video_findings_count_by_team.get(team_key, 0),
                    "anti_defense_pressure_coverage_0_1": features.feature_raw[team_key].get(
                        "anti_defense_pressure_coverage"
                    ),
                    "severe_penalty_rate": features.feature_raw[team_key].get("severe_penalty_rate"),
                    "hub_awareness_0_100": scores.hub_awareness_pct[team_key],
                    "terrain_mobility_0_100": scores.terrain_mobility_pct[team_key],
                }
            )

        try:
            ml_team_strength_payload = infer_team_strength_shadow_from_rows(db, rows=ml_rows)
        except Exception as exc:
            ml_team_strength_payload = {
                "enabled": ml_shadow_enabled,
                "prediction_ok": False,
                "model_version": None,
                "reason": "inference_failed",
                "detail": str(exc),
            }

        if bool(ml_team_strength_payload.get("ok")):
            ml_team_strength_payload["prediction_ok"] = True
            ml_team_strength_payload.setdefault("reason", "ok")
            ml_team_strength_payload.setdefault("detail", None)
            ml_team_strength_payload["enabled"] = True
            for prediction in ml_team_strength_payload.get("predictions") or []:
                if not isinstance(prediction, dict):
                    continue
                team_key = str(prediction.get("team_key") or "").strip().lower()
                predicted_bps = _as_float(prediction.get("strength_active_bps_pred"))
                if team_key and predicted_bps is not None:
                    ml_team_strength_prediction_by_team[team_key] = float(predicted_bps)
            if ml_team_strength_prediction_by_team:
                ml_team_strength_percentile_by_team = _percentile_map(ml_team_strength_prediction_by_team)
            else:
                ml_team_strength_payload["prediction_ok"] = False
                ml_team_strength_payload["reason"] = "no_predictions"
        else:
            ml_team_strength_payload["prediction_ok"] = False
            ml_team_strength_payload["enabled"] = True
            ml_team_strength_payload["reason"] = str(ml_team_strength_payload.get("reason") or "prediction_failed")
            ml_team_strength_payload["detail"] = ml_team_strength_payload.get("detail")

    ml_rollout_active_count = 0
    ml_blend_applied_count = 0

    elite_dimensions_by_team: dict[str, dict[str, Any]] = {}
    role_classifications_by_team: dict[str, dict[str, Any]] = {}

    ml_role_blend_knob = max(0.0, min(1.0, float(getattr(settings, "ml_role_blend", 0.0) or 0.0)))

    for team_key in data.team_keys:
        try:
            analyzer = EliteRobotAnalyzer(db, event_key, team_key)
            elite_dimensions_by_team[team_key] = analyzer.analyze()
        except Exception:
            elite_dimensions_by_team[team_key] = {}

        try:
            role_classifier = RoleClassifier(db, event_key, team_key)
            deterministic_classification = role_classifier.classify()
        except Exception:
            deterministic_classification = {"primary_role": "unknown"}

        # Phase 5: Blend ML role signals if knob > 0
        if ml_role_blend_knob > 0.0:
            try:
                from app.services.ml.shadow import infer_role_signals_shadow
                from app.services.ml.role_ml import blend_role_signals

                season_year = int(event_key[:4]) if event_key[:4].isdigit() else None
                rating_row = (
                    db.query(models.EventTeamRating)
                    .filter(
                        models.EventTeamRating.event_key == event_key,
                        models.EventTeamRating.team_key == team_key,
                    )
                    .one_or_none()
                )
                ml_result = infer_role_signals_shadow(db, rating=rating_row, event_year=season_year)
                if bool(ml_result.get("ok")) and ml_result.get("predictions"):
                    det_signals = deterministic_classification.get("role_signals", {})
                    ml_signals = ml_result["predictions"]
                    blended_signals = blend_role_signals(det_signals, ml_signals, ml_role_blend_knob)
                    deterministic_classification = dict(deterministic_classification)
                    deterministic_classification["role_signals"] = blended_signals
                    # Re-derive primary_role from blended signals
                    signal_items = sorted(blended_signals.items(), key=lambda x: x[1], reverse=True)
                    if signal_items:
                        top_key = signal_items[0][0].replace("_signal", "")
                        role_map = {
                            "scorer": "primary_scorer",
                            "defender": "defender",
                            "feeder": "feeder",
                            "endgame": "endgame_specialist",
                        }
                        deterministic_classification["primary_role"] = role_map.get(top_key, "versatile")
                    deterministic_classification["ml_role_blend"] = round(ml_role_blend_knob, 4)
            except Exception as exc:
                logger.warning("ML role signal blend failed for %s/%s: %s", event_key, team_key, exc)

        role_classifications_by_team[team_key] = deterministic_classification

    now = datetime.now(timezone.utc)
    rating_rows: list[models.EventTeamRating] = []
    for event_team, team in data.team_rows:
        team_key = event_team.team_key
        robot_level = scores.robot_level_by_team[team_key]
        performance_score = scores.performance_by_team[team_key]
        driver_skill = scores.driver_skill_pct[team_key]
        results_anchor = scores.results_anchor_pct[team_key]
        throughput = scores.throughput_pct[team_key]
        shift_productivity = scores.shift_pct[team_key]
        capacity_utilization = scores.cap_util_pct[team_key]
        endgame = scores.endgame_pct[team_key]
        consistency = scores.consistency_pct[team_key]
        auto_contribution_score = scores.auto_pct[team_key]
        manual_points_impact = scores.manual_points_pct[team_key]
        rp_contribution_score = scores.rp_contribution_pct[team_key]
        defense_presence_score = scores.defense_presence_pct[team_key]
        anti_defense_score = scores.anti_defense_pct[team_key]
        penalty_discipline = scores.penalty_discipline_pct[team_key]
        throughput_trend_score = scores.throughput_trend_pct[team_key]
        reliability_trend_score = scores.reliability_trend_pct[team_key]
        cycle_trend_score = scores.cycle_trend_pct[team_key]
        penalty_trend_score = scores.penalty_trend_pct[team_key]
        throughput_trend_delta = float(features.feature_raw[team_key].get("throughput_trend_delta") or 0.0)
        reliability_trend_delta = float(features.feature_raw[team_key].get("reliability_trend_delta") or 0.0)
        cycle_trend_delta = float(features.feature_raw[team_key].get("cycle_trend_delta") or 0.0)
        penalty_trend_delta = float(features.feature_raw[team_key].get("penalty_trend_delta") or 0.0)
        penalty_points_per_match = float(features.feature_raw[team_key].get("penalty_points_per_match") or 0.0)
        severe_penalty_rate = float(features.feature_raw[team_key].get("severe_penalty_rate") or 0.0)
        anti_defense_drop_index = _as_float(features.feature_raw[team_key].get("anti_defense_drop_index"))
        anti_defense_tier_label = _anti_defense_tier(anti_defense_drop_index)
        anti_defense_pressure_coverage = float(
            features.feature_raw[team_key].get("anti_defense_pressure_coverage") or 0.0
        )
        anti_defense_stage_multiplier = float(
            features.feature_raw[team_key].get("anti_defense_stage_multiplier") or 1.0
        )
        anti_defense_stage_early_share = float(
            features.feature_raw[team_key].get("anti_defense_stage_early_quals_share") or 0.0
        )
        anti_defense_stage_late_share = float(
            features.feature_raw[team_key].get("anti_defense_stage_late_quals_share") or 0.0
        )
        anti_defense_stage_elims_share = float(
            features.feature_raw[team_key].get("anti_defense_stage_elims_share") or 0.0
        )
        anti_defense_perf_weight_applied = float(
            scores.anti_defense_performance_weight_by_team.get(team_key, PERFORMANCE_ANTIDEFENSE_WEIGHT)
        )
        anti_defense_base_weight_applied = float(
            scores.anti_defense_base_weight_by_team.get(team_key, BASE_ANTIDEFENSE_WEIGHT)
        )
        statbotics_epa_value = _as_float(features.feature_raw[team_key].get("statbotics_norm_epa"))
        statbotics_epa_percentile = (
            float(scores.statbotics_epa_pct[team_key]) if statbotics_epa_value is not None else None
        )
        statbotics_epa_context = data.epa_context_by_team.get(team_key) or {
            "available": False,
            "source": "not_loaded",
            "raw_value": None,
        }
        video_findings_count = int(features.feature_raw[team_key].get("video_findings_count") or 0)
        use_fallback_model = video_findings_count <= 0

        support = features.confidence_support[team_key]
        fallback_signal_availability = {
            "statbotics_epa": statbotics_epa_value is not None,
            "opr": features.feature_raw[team_key].get("opr") is not None,
            "ccwm": features.feature_raw[team_key].get("ccwm") is not None,
            "dpr": features.feature_raw[team_key].get("dpr") is not None,
            "event_strength": features.feature_raw[team_key].get("event_strength_bps") is not None,
            "season_strength": features.feature_raw[team_key].get("season_strength_bps") is not None,
        }
        external_signal_count = sum(1 for present in fallback_signal_availability.values() if present)
        official_match_support = _clamp(
            float(features.feature_raw[team_key].get("recent_matches_used") or 0.0) / 10.0,
            0.0,
            1.0,
        )
        fallback_anchor_score = _weighted_score(
            [
                (0.36, scores.statbotics_epa_pct[team_key] if fallback_signal_availability["statbotics_epa"] else None),
                (0.32, scores.opr_pct[team_key] if fallback_signal_availability["opr"] else None),
                (0.20, scores.ccwm_pct[team_key] if fallback_signal_availability["ccwm"] else None),
                (0.12, scores.dpr_defense_pct[team_key] if fallback_signal_availability["dpr"] else None),
            ],
            default=50.0,
        )
        fallback_throughput_score = _weighted_score(
            [
                (0.50, scores.event_strength_pct[team_key] if fallback_signal_availability["event_strength"] else None),
                (0.25, scores.season_strength_pct[team_key] if fallback_signal_availability["season_strength"] else None),
                (0.25, fallback_anchor_score),
            ],
            default=fallback_anchor_score,
        )
        fallback_defense_score = _weighted_score(
            [
                (0.55, scores.dpr_defense_pct[team_key] if fallback_signal_availability["dpr"] else None),
                (0.20, scores.anti_defense_pct[team_key]),
                (0.25, fallback_anchor_score),
            ],
            default=fallback_anchor_score,
        )
        fallback_auto_score = _weighted_score(
            [
                (0.60, scores.auto_pct[team_key] if features.feature_raw[team_key].get("auto_points_est") is not None else None),
                (0.40, fallback_anchor_score),
            ],
            default=fallback_anchor_score,
        )
        fallback_endgame_score = _weighted_score(
            [
                (0.60, scores.endgame_pct[team_key] if features.feature_raw[team_key].get("climb_output") is not None else None),
                (0.40, fallback_anchor_score),
            ],
            default=fallback_anchor_score,
        )
        fallback_manual_score = _weighted_score(
            [
                (0.45, scores.opr_pct[team_key] if fallback_signal_availability["opr"] else None),
                (0.25, scores.ccwm_pct[team_key] if fallback_signal_availability["ccwm"] else None),
                (0.30, fallback_anchor_score),
            ],
            default=fallback_anchor_score,
        )
        fallback_rp_score = _weighted_score(
            [
                (0.55, scores.rp_contribution_pct[team_key] if features.feature_raw[team_key].get("rp_contribution_raw") is not None else None),
                (0.45, fallback_manual_score),
            ],
            default=fallback_manual_score,
        )
        fallback_driver_score = _weighted_score(
            [
                (0.45, scores.ccwm_pct[team_key] if fallback_signal_availability["ccwm"] else None),
                (0.25, scores.consistency_pct[team_key] if features.feature_raw[team_key].get("consistency_var") is not None else None),
                (0.30, fallback_anchor_score),
            ],
            default=fallback_anchor_score,
        )
        fallback_robot_score = _weighted_score(
            [
                (0.40, fallback_throughput_score),
                (0.25, fallback_anchor_score),
                (0.20, fallback_endgame_score),
                (0.15, scores.speed_pct[team_key] if features.feature_raw[team_key].get("top_speed") is not None else None),
            ],
            default=fallback_anchor_score,
        )
        fallback_shift_productivity = _weighted_score(
            [
                (0.60, scores.shift_pct[team_key] if features.feature_raw[team_key].get("shift_productivity") is not None else None),
                (0.40, fallback_throughput_score),
            ],
            default=fallback_throughput_score,
        )
        fallback_capacity_utilization = _weighted_score(
            [
                (
                    0.55,
                    scores.cap_util_pct[team_key]
                    if features.feature_raw[team_key].get("capacity_utilization") is not None
                    else None,
                ),
                (0.45, fallback_throughput_score),
            ],
            default=fallback_throughput_score,
        )
        fallback_consistency_score = _weighted_score(
            [
                (0.65, scores.consistency_pct[team_key] if features.feature_raw[team_key].get("consistency_var") is not None else None),
                (0.35, fallback_anchor_score),
            ],
            default=fallback_anchor_score,
        )
        fallback_anti_defense_score = _weighted_score(
            [
                (0.60, fallback_defense_score),
                (0.25, scores.anti_defense_pct[team_key]),
                (0.15, fallback_anchor_score),
            ],
            default=fallback_defense_score,
        )

        confidence = float(scores.confidence_by_team.get(team_key, 0.0))
        rating_algorithm_mode = "primary_video_model_v10"
        if use_fallback_model:
            strength_confidence = _weighted_score(
                [
                    (
                        0.60,
                        scores.event_strength_conf_pct[team_key]
                        if features.feature_raw[team_key].get("event_strength_confidence") is not None
                        else None,
                    ),
                    (
                        0.40,
                        scores.season_strength_conf_pct[team_key]
                        if features.feature_raw[team_key].get("season_strength_confidence") is not None
                        else None,
                    ),
                ],
                default=50.0,
            )
            confidence = _clamp(
                0.40
                + (0.10 * min(5, external_signal_count))
                + (0.18 * official_match_support)
                + (0.12 * (strength_confidence / 100.0)),
                0.36,
                0.86,
            )
            results_anchor = fallback_anchor_score
            performance_score = _weighted_score(
                [
                    (0.34, fallback_anchor_score),
                    (0.22, fallback_throughput_score),
                    (0.13, fallback_manual_score),
                    (0.11, fallback_defense_score),
                    (0.10, fallback_rp_score),
                    (0.10, fallback_endgame_score),
                ],
                default=fallback_anchor_score,
            )
            robot_level = fallback_robot_score
            driver_skill = fallback_driver_score
            throughput = fallback_throughput_score
            shift_productivity = fallback_shift_productivity
            capacity_utilization = fallback_capacity_utilization
            endgame = fallback_endgame_score
            consistency = fallback_consistency_score
            auto_contribution_score = fallback_auto_score
            manual_points_impact = fallback_manual_score
            rp_contribution_score = fallback_rp_score
            defense_presence_score = fallback_defense_score
            anti_defense_score = fallback_anti_defense_score
            anti_defense_perf_weight_applied = _clamp(
                PERFORMANCE_ANTIDEFENSE_WEIGHT * 0.75,
                0.0,
                0.2,
            )
            anti_defense_base_weight_applied = _clamp(
                BASE_ANTIDEFENSE_WEIGHT * 0.8,
                0.0,
                0.16,
            )
            rating_algorithm_mode = FALLBACK_MODEL_LABEL

        ml_rollout_key = f"{event_key}:{team_key}:{TEAM_STRENGTH_MODEL_KEY}"
        ml_team_strength_blend_knob = max(0.0, min(1.0, float(getattr(settings, "ml_team_strength_blend", 0.0) or 0.0)))
        ml_rollout_active = bool(
            ml_team_strength_payload.get("prediction_ok")
            and (team_key in ml_team_strength_prediction_by_team)
            and (
                ml_team_strength_blend_knob > 0.0
                or is_shadow_rollout_active(ml_rollout_key)
            )
        )
        ml_predicted_bps = _as_float(ml_team_strength_prediction_by_team.get(team_key))
        ml_predicted_percentile = _as_float(ml_team_strength_percentile_by_team.get(team_key))
        ml_blend_weight = 0.0
        ml_blend_applied = False
        if ml_rollout_active and ml_predicted_percentile is not None:
            ml_rollout_active_count += 1
            # Confidence-scaled base weight (5-18% range at full knob)
            base_ml_weight = _clamp(
                (0.06 + (0.10 * confidence)) * (0.75 if use_fallback_model else 1.0),
                0.05,
                0.18,
            )
            # Scale by blend knob: knob=0 → shadow-only (legacy), knob=1 → full ML weight
            # If the knob is >0, use it as the scaling factor. If it's 0, fall
            # through to legacy shadow-rollout behavior (weight is unscaled).
            if ml_team_strength_blend_knob > 0.0:
                ml_blend_weight = base_ml_weight * ml_team_strength_blend_knob
            else:
                ml_blend_weight = base_ml_weight
            throughput = _clamp(
                ((1.0 - ml_blend_weight) * throughput) + (ml_blend_weight * ml_predicted_percentile),
                0.0,
                100.0,
            )
            performance_score = _clamp(
                ((1.0 - (0.55 * ml_blend_weight)) * performance_score)
                + ((0.55 * ml_blend_weight) * ml_predicted_percentile),
                0.0,
                100.0,
            )
            robot_level = _clamp(
                ((1.0 - (0.40 * ml_blend_weight)) * robot_level)
                + ((0.40 * ml_blend_weight) * ml_predicted_percentile),
                0.0,
                100.0,
            )
            ml_blend_applied = True
            ml_blend_applied_count += 1
            if ml_team_strength_blend_knob > 0.0:
                rating_algorithm_mode = f"{rating_algorithm_mode}+ml_team_strength_blend_v1"
            else:
                rating_algorithm_mode = f"{rating_algorithm_mode}+ml_shadow_team_strength_v1"

        base_components = [
            (0.21, results_anchor),
            (0.29, performance_score),
            (0.16, driver_skill),
            (0.11, robot_level),
            (0.09, manual_points_impact),
            (0.05, rp_contribution_score),
            (BASE_AUTO_WEIGHT, auto_contribution_score),
            (anti_defense_base_weight_applied, anti_defense_score),
        ]
        total_base_weight = max(1e-6, sum(weight for weight, _ in base_components))
        base_rating = sum(weight * value for weight, value in base_components) / total_base_weight
        penalty_deduction = _clamp(
            (
                ((penalty_points_per_match / MAJOR_FOUL_POINTS) * 4.0)
                + (severe_penalty_rate * 1.8)
            )
            * PENALTY_IMPACT_BASE_RATING,
            0.0,
            7.5 * PENALTY_IMPACT_BASE_RATING,
        )
        base_rating = _clamp(base_rating - penalty_deduction, 0.0, 100.0)
        raw_final_rating = _clamp(50.0 + (confidence * (base_rating - 50.0)), 0.0, 100.0)
        matches_observed_for_guard = int(features.findings_count.get(team_key, 0) or 0)
        raw_final_rating, sparse_rating_guard = _apply_sparse_rating_guard(
            raw_final_rating=raw_final_rating,
            confidence=confidence,
            matches_observed=matches_observed_for_guard,
            video_findings_count=video_findings_count,
            use_fallback_model=use_fallback_model,
        )
        final_rating = calibrate_public_rating_scale(raw_final_rating)
        robot_level = calibrate_public_rating_scale(robot_level)
        driver_skill = calibrate_public_rating_scale(driver_skill)

        findings = _dedupe_findings_by_match(
            _sort_findings_newest_first(data.findings_by_team.get(team_key, []), data.match_time_by_key),
            data.match_time_by_key,
        )

        pros, cons = generate_team_signals(
            team_key=team_key,
            feature_raw=features.feature_raw[team_key],
            confidence=confidence,
            findings=findings,
            findings_count=int(features.findings_count.get(team_key, 0) or 0),
            support=support,
            use_fallback_model=use_fallback_model,
            rating_algorithm_mode=rating_algorithm_mode,
            throughput=throughput,
            shift_productivity=shift_productivity,
            capacity_utilization=capacity_utilization,
            endgame=endgame,
            auto_contribution_score=auto_contribution_score,
            manual_points_impact=manual_points_impact,
            rp_contribution_score=rp_contribution_score,
            defense_presence_score=defense_presence_score,
            anti_defense_score=anti_defense_score,
            penalty_discipline=penalty_discipline,
            driver_skill=driver_skill,
            robot_level=robot_level,
            consistency=consistency,
            capacity_pct_value=scores.capacity_pct[team_key],
            throughput_trend_score=throughput_trend_score,
            reliability_trend_score=reliability_trend_score,
            cycle_trend_score=cycle_trend_score,
            penalty_trend_score=penalty_trend_score,
            throughput_trend_delta=throughput_trend_delta,
            reliability_trend_delta=reliability_trend_delta,
            cycle_trend_delta=cycle_trend_delta,
            penalty_trend_delta=penalty_trend_delta,
            penalty_points_per_match=penalty_points_per_match,
            severe_penalty_rate=severe_penalty_rate,
            anti_defense_drop_index=anti_defense_drop_index,
            anti_defense_pressure_coverage=anti_defense_pressure_coverage,
            statbotics_epa_value=statbotics_epa_value,
            statbotics_epa_percentile=statbotics_epa_percentile,
            opr_pct_value=scores.opr_pct[team_key],
            ccwm_pct_value=scores.ccwm_pct[team_key],
            ml_blend_applied=ml_blend_applied,
            ml_predicted_bps=ml_predicted_bps,
            ml_predicted_percentile=ml_predicted_percentile,
            events_by_team_match=data.events_by_team_match.get(team_key, {}),
            video_findings_count=video_findings_count,
            fallback_signal_availability=fallback_signal_availability,
        )

        combined_evidence = []
        seen_keys: set[tuple[str, str]] = set()
        for signal in pros + cons:
            for entry in signal.get("evidence", []):
                match_key = str(entry.get("match_key") or "")
                metric = str(entry.get("metric") or "")
                if not match_key:
                    continue
                dedupe = (match_key, metric)
                if dedupe in seen_keys:
                    continue
                seen_keys.add(dedupe)
                combined_evidence.append(entry)

        details_json = build_details_json(
            rating_algorithm_mode=rating_algorithm_mode,
            base_rating=base_rating,
            performance_score=performance_score,
            penalty_deduction=penalty_deduction,
            raw_final_rating=raw_final_rating,
            sparse_rating_guard=sparse_rating_guard,
            results_anchor=results_anchor,
            driver_skill=driver_skill,
            robot_level=robot_level,
            auto_contribution_score=auto_contribution_score,
            anti_defense_score=anti_defense_score,
            anti_defense_perf_weight_applied=anti_defense_perf_weight_applied,
            anti_defense_base_weight_applied=anti_defense_base_weight_applied,
            manual_points_impact=manual_points_impact,
            rp_contribution_score=rp_contribution_score,
            penalty_discipline=penalty_discipline,
            throughput_trend_score=throughput_trend_score,
            reliability_trend_score=reliability_trend_score,
            cycle_trend_score=cycle_trend_score,
            penalty_trend_score=penalty_trend_score,
            statbotics_epa_percentile=statbotics_epa_percentile,
            ml_predicted_percentile=ml_predicted_percentile,
            ml_blend_weight=ml_blend_weight,
            ml_blend_applied=ml_blend_applied,
            opr_pct_value=scores.opr_pct[team_key],
            ccwm_pct_value=scores.ccwm_pct[team_key],
            dpr_defense_pct_value=scores.dpr_defense_pct[team_key],
            statbotics_epa_value=statbotics_epa_value,
            feature_raw=features.feature_raw[team_key],
            support=support,
            epa_context=statbotics_epa_context,
            anti_defense_drop_index=anti_defense_drop_index,
            anti_defense_pressure_coverage=anti_defense_pressure_coverage,
            anti_defense_stage_multiplier=anti_defense_stage_multiplier,
            anti_defense_stage_early_share=anti_defense_stage_early_share,
            anti_defense_stage_late_share=anti_defense_stage_late_share,
            anti_defense_stage_elims_share=anti_defense_stage_elims_share,
            anti_defense_tier=anti_defense_tier_label,
            penalty_points_per_match=penalty_points_per_match,
            severe_penalty_rate=severe_penalty_rate,
            throughput_trend_delta=throughput_trend_delta,
            reliability_trend_delta=reliability_trend_delta,
            cycle_trend_delta=cycle_trend_delta,
            penalty_trend_delta=penalty_trend_delta,
            throughput=throughput,
            shift_productivity=shift_productivity,
            capacity_utilization=capacity_utilization,
            endgame=endgame,
            consistency=consistency,
            defense_presence_score=defense_presence_score,
            manual_context=manual_context,
            use_fallback_model=use_fallback_model,
            video_findings_count=video_findings_count,
            external_signal_count=external_signal_count,
            fallback_signal_availability=fallback_signal_availability,
            official_match_support=official_match_support,
            ml_shadow_enabled=ml_shadow_enabled,
            ml_model_key=TEAM_STRENGTH_MODEL_KEY,
            ml_rollout_key=ml_rollout_key,
            ml_rollout_active=ml_rollout_active,
            ml_predicted_bps=ml_predicted_bps,
            ml_team_strength_payload=ml_team_strength_payload,
            findings_count=int(features.findings_count.get(team_key, 0) or 0),
            gate_config=data.gate_config,
            raw_findings_count=int(data.raw_findings_count_by_team.get(team_key, 0)),
            excluded_findings_count=int(data.excluded_findings_count_by_team.get(team_key, 0)),
            elite_dimensions=elite_dimensions_by_team.get(team_key, {}),
            role_classification=role_classifications_by_team.get(team_key, {}),
            now=now,
        )

        rating_row = build_rating_row(
            event_key=event_key,
            team_key=team_key,
            final_rating=final_rating,
            confidence=confidence,
            robot_level=robot_level,
            driver_skill=driver_skill,
            results_anchor=results_anchor,
            throughput=throughput,
            shift_productivity=shift_productivity,
            capacity_utilization=capacity_utilization,
            endgame=endgame,
            consistency=consistency,
            pros=pros,
            cons=cons,
            combined_evidence=combined_evidence,
            details_json=details_json,
            now=now,
        )
        db.merge(rating_row)
        rating_rows.append(rating_row)

    db.commit()

    # Append a snapshot row per team so the live UI can chart momentum.
    # Best-effort: a snapshot failure must never abort a recompute.
    try:
        record_event_rating_snapshots(
            db,
            event_key,
            findings_count_by_team={
                tk: int(features.findings_count.get(tk, 0) or 0) for tk in data.team_keys
            },
        )
    except Exception:
        logger.exception("rating snapshot write failed for event=%s", event_key)
        db.rollback()

    persisted_rows = (
        db.query(models.EventTeamRating, models.Team)
        .outerjoin(models.Team, models.Team.team_key == models.EventTeamRating.team_key)
        .filter(models.EventTeamRating.event_key == event_key)
        .order_by(models.EventTeamRating.rating_0_100.desc(), models.EventTeamRating.team_key.asc())
        .all()
    )

    payload = build_response_payload(persisted_rows)

    return {
        "ok": True,
        "event_key": event_key,
        "model_version": MODEL_VERSION,
        "duration_sec": round(max(0.0, time.perf_counter() - started), 4),
        "quality_gate": {
            **data.gate_config,
            "raw_findings_count": len(data.raw_finding_rows),
            "accepted_findings_count": len(data.finding_rows),
            "excluded_findings_count": max(0, len(data.raw_finding_rows) - len(data.finding_rows)),
        },
        "ml_shadow": {
            "enabled": bool(ml_shadow_enabled),
            "team_strength": {
                "model_key": TEAM_STRENGTH_MODEL_KEY,
                "model_version": ml_team_strength_payload.get("model_version"),
                "prediction_ok": bool(ml_team_strength_payload.get("prediction_ok")),
                "reason": ml_team_strength_payload.get("reason"),
                "detail": ml_team_strength_payload.get("detail"),
                "blend_knob": round(max(0.0, min(1.0, float(getattr(settings, "ml_team_strength_blend", 0.0) or 0.0))), 4),
                "teams_with_predictions": len(ml_team_strength_prediction_by_team),
                "teams_with_rollout_active": int(ml_rollout_active_count),
                "teams_with_blend_applied": int(ml_blend_applied_count),
            },
        },
        "count": len(payload),
        "ratings": payload,
    }
