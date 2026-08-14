from __future__ import annotations

from typing import Any

from app.services.auto_scout.ml import (
    build_auto_scout_field_feature_vector,
    present_auto_scout_field_prediction,
)
from app.services.auto_scout.predictors.init import PredictorContext, PredictorResult, register
from app.services.ml.shadow import infer_auto_scout_field_shadow_from_rows


def _safe_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


# Per-field ML confidence gates. Matched to the thresholds the predictors register with.
_ML_FIELD_THRESHOLDS: dict[str, float] = {
    "teleop_under_defense_scored": 0.62,
    "offense_level_1_5": 0.60,
    "defense_level_1_5": 0.60,
    "field_awareness_1_5": 0.58,
    "decision_quality_1_5": 0.58,
    "intake_failures": 0.60,
}
# Gate for any round-2 field not listed above — so a newly registered predictor still
# runs ML (with a sane default) instead of silently falling back to deterministic-only.
_DEFAULT_ML_THRESHOLD = 0.60


def _feature_vector(context: PredictorContext) -> dict[str, float]:
    # Build once per draft and cache on the context — every round-2 field reads it,
    # and each predictor would otherwise build it twice (deterministic arg + resolve).
    if context.feature_vector is not None:
        return context.feature_vector
    feature_vector = build_auto_scout_field_feature_vector(
        season_year=int(context.season_year),
        coverage=float(context.coverage),
        throughput_coverage=context.throughput_coverage,
        events=context.events,
        tracks=context.tracks,
        finding=context.finding,
        throughput=context.throughput,
        quality=context.quality,
    )
    context.feature_vector = feature_vector
    return feature_vector


def _ml_predictions(context: PredictorContext) -> dict[str, PredictorResult | None]:
    # Run ML inference for every round-2 field once per draft and cache the results.
    # Each predictor then reads its own field instead of re-querying the registry.
    if context.ml_predictions_by_field is not None:
        return context.ml_predictions_by_field
    features = _feature_vector(context)
    predictions: dict[str, PredictorResult | None] = {}
    for field_name, threshold in _ML_FIELD_THRESHOLDS.items():
        predictions[field_name] = _ml_prediction(
            context,
            field_name=field_name,
            feature_vector=features,
            threshold=threshold,
        )
    context.ml_predictions_by_field = predictions
    return predictions


def _ml_prediction(
    context: PredictorContext,
    *,
    field_name: str,
    feature_vector: dict[str, float],
    threshold: float = 0.6,
) -> PredictorResult | None:
    if context.db is None:
        return None
    payload = infer_auto_scout_field_shadow_from_rows(
        context.db,
        field_name=field_name,
        rows=[
            {
                "event_key": context.event_key,
                "match_key": context.match_key,
                "team_key": context.team_key,
                "feature_vector": feature_vector,
            }
        ],
    )
    if not bool(payload.get("ok")):
        return None
    rows = payload.get("predictions")
    if not isinstance(rows, list) or not rows:
        return None
    first_row = rows[0] if isinstance(rows[0], dict) else {}
    prediction = _safe_float(first_row.get("field_value_pred"))
    confidence = _safe_float(first_row.get("confidence_0_1"))
    if prediction is None or confidence is None:
        return None
    if float(confidence) < float(threshold):
        return None

    value = present_auto_scout_field_prediction(field_name, float(prediction))
    return PredictorResult(
        value=value,
        confidence=_clamp(float(confidence), 0.0, 1.0),
        provenance="auto",
        evidence_refs=[
            {
                "type": "ml_model",
                "ref_id": f"ml_model:{payload.get('model_key')}:{payload.get('model_version')}",
                "t_sec": 0.0,
                "meta": {
                    "model_key": payload.get("model_key"),
                    "model_version": payload.get("model_version"),
                    "field_name": field_name,
                    "confidence_0_1": _round(float(confidence), 4),
                },
            }
        ],
    )


def _teleop_scored_estimate(feature_vector: dict[str, float]) -> float:
    return max(0.0, float(feature_vector.get("teleop_score_estimate") or 0.0))


def _teleop_success_count(feature_vector: dict[str, float]) -> float:
    return max(0.0, float(feature_vector.get("teleop_score_success_count") or 0.0))


def _teleop_attempt_count(feature_vector: dict[str, float]) -> float:
    return max(0.0, float(feature_vector.get("teleop_score_attempt_count") or 0.0))


def _deterministic_teleop_under_defense_scored(feature_vector: dict[str, float]) -> tuple[int, float, list[dict[str, Any]]]:
    teleop_scored = _teleop_scored_estimate(feature_vector)
    pressure = _clamp(float(feature_vector.get("under_defense_pressure_0_1") or 0.0), 0.0, 1.0)
    estimate = int(round(max(0.0, teleop_scored * (0.16 + (0.58 * pressure)))))
    coverage = _clamp(float(feature_vector.get("track_coverage_0_1") or 0.0), 0.0, 1.0)
    throughput_cov = _clamp(float(feature_vector.get("throughput_coverage_0_1") or 0.0), 0.0, 1.0)
    confidence = _clamp((0.42 + (0.28 * coverage) + (0.30 * throughput_cov)), 0.0, 0.95)
    evidence = [
        {
            "type": "analysis_inference",
            "ref_id": "auto_scout_rule:teleop_under_defense_scored",
            "t_sec": 0.0,
            "meta": {
                "teleop_score_estimate": int(round(teleop_scored)),
                "under_defense_pressure_0_1": _round(pressure, 4),
            },
        }
    ]
    return estimate, confidence, evidence


def _deterministic_offense_level(feature_vector: dict[str, float]) -> tuple[int, float, list[dict[str, Any]]]:
    teleop_scored = _teleop_scored_estimate(feature_vector)
    active_bps = max(0.0, float(feature_vector.get("active_bps") or 0.0))
    reliability = _clamp(float(feature_vector.get("reliability_score") or 0.0), 0.0, 1.0)
    index = (
        (0.40 * _clamp(teleop_scored / 14.0, 0.0, 1.0))
        + (0.38 * _clamp(active_bps / 0.90, 0.0, 1.0))
        + (0.22 * reliability)
    )
    level = int(round(1.0 + (4.0 * _clamp(index, 0.0, 1.0))))
    coverage = _clamp(float(feature_vector.get("throughput_coverage_0_1") or 0.0), 0.0, 1.0)
    confidence = _clamp(0.46 + (0.36 * coverage) + (0.12 * reliability), 0.0, 0.96)
    evidence = [
        {
            "type": "analysis_inference",
            "ref_id": "auto_scout_rule:offense_level_1_5",
            "t_sec": 0.0,
            "meta": {
                "teleop_score_estimate": int(round(teleop_scored)),
                "active_bps": _round(active_bps, 4),
                "reliability_score": _round(reliability, 4),
            },
        }
    ]
    return max(1, min(5, level)), confidence, evidence


def _deterministic_defense_level(feature_vector: dict[str, float]) -> tuple[int, float, list[dict[str, Any]]]:
    defensive_sec = max(0.0, float(feature_vector.get("defensive_engagement_sec") or 0.0))
    protected_count = max(0.0, float(feature_vector.get("protected_zone_interference_count") or 0.0))
    pressure = _clamp(float(feature_vector.get("under_defense_pressure_0_1") or 0.0), 0.0, 1.0)
    index = (
        (0.48 * _clamp(defensive_sec / 24.0, 0.0, 1.0))
        + (0.34 * _clamp(protected_count / 6.0, 0.0, 1.0))
        + (0.18 * pressure)
    )
    level = int(round(1.0 + (4.0 * _clamp(index, 0.0, 1.0))))
    coverage = _clamp(float(feature_vector.get("track_coverage_0_1") or 0.0), 0.0, 1.0)
    confidence = _clamp(0.46 + (0.34 * coverage) + (0.20 * pressure), 0.0, 0.95)
    evidence = [
        {
            "type": "analysis_inference",
            "ref_id": "auto_scout_rule:defense_level_1_5",
            "t_sec": 0.0,
            "meta": {
                "defensive_engagement_sec": _round(defensive_sec, 3),
                "protected_zone_interference_count": int(round(protected_count)),
            },
        }
    ]
    return max(1, min(5, level)), confidence, evidence


def _deterministic_field_awareness(feature_vector: dict[str, float]) -> tuple[int, float, list[dict[str, Any]]]:
    entropy = _clamp(float(feature_vector.get("zone_entropy_0_1") or 0.0), 0.0, 1.0)
    track_coverage = _clamp(float(feature_vector.get("track_coverage_0_1") or 0.0), 0.0, 1.0)
    reliability = _clamp(float(feature_vector.get("reliability_score") or 0.0), 0.0, 1.0)
    disabled_penalty = _clamp(float(feature_vector.get("disabled_seconds") or 0.0) / 20.0, 0.0, 1.0)
    index = (
        (0.44 * entropy)
        + (0.28 * track_coverage)
        + (0.28 * reliability)
        - (0.16 * disabled_penalty)
    )
    level = int(round(1.0 + (4.0 * _clamp(index, 0.0, 1.0))))
    confidence = _clamp(0.42 + (0.32 * track_coverage) + (0.18 * reliability), 0.0, 0.95)
    evidence = [
        {
            "type": "analysis_inference",
            "ref_id": "auto_scout_rule:field_awareness_1_5",
            "t_sec": 0.0,
            "meta": {
                "zone_entropy_0_1": _round(entropy, 4),
                "disabled_seconds": _round(float(feature_vector.get("disabled_seconds") or 0.0), 3),
            },
        }
    ]
    return max(1, min(5, level)), confidence, evidence


def _deterministic_decision_quality(feature_vector: dict[str, float]) -> tuple[int, float, list[dict[str, Any]]]:
    throughput_cov = _clamp(float(feature_vector.get("throughput_coverage_0_1") or 0.0), 0.0, 1.0)
    reliability = _clamp(float(feature_vector.get("reliability_score") or 0.0), 0.0, 1.0)
    teleop_scored = _teleop_scored_estimate(feature_vector)
    under_defense = max(0.0, float(feature_vector.get("under_defense_pressure_0_1") or 0.0))
    under_defense_scored_est = teleop_scored * (0.16 + (0.58 * _clamp(under_defense, 0.0, 1.0)))
    recovery_ratio = _clamp((under_defense_scored_est + 1.0) / (teleop_scored + 1.0), 0.0, 1.0)
    disabled_penalty = _clamp(float(feature_vector.get("disabled_seconds") or 0.0) / 20.0, 0.0, 1.0)
    index = (
        (0.34 * throughput_cov)
        + (0.30 * reliability)
        + (0.24 * recovery_ratio)
        + (0.12 * (1.0 - disabled_penalty))
    )
    level = int(round(1.0 + (4.0 * _clamp(index, 0.0, 1.0))))
    confidence = _clamp(0.44 + (0.30 * throughput_cov) + (0.20 * reliability), 0.0, 0.95)
    evidence = [
        {
            "type": "analysis_inference",
            "ref_id": "auto_scout_rule:decision_quality_1_5",
            "t_sec": 0.0,
            "meta": {
                "throughput_coverage_0_1": _round(throughput_cov, 4),
                "recovery_ratio_0_1": _round(recovery_ratio, 4),
            },
        }
    ]
    return max(1, min(5, level)), confidence, evidence


def _deterministic_intake_failures(feature_vector: dict[str, float]) -> tuple[int, float, list[dict[str, Any]]]:
    attempts = max(_teleop_attempt_count(feature_vector), _teleop_scored_estimate(feature_vector))
    successes = _teleop_success_count(feature_vector)
    intake_events = max(0.0, float(feature_vector.get("depot_intake_count") or 0.0))
    misses = max(0.0, attempts - successes)
    estimate = int(round(max(0.0, (0.45 * misses) + (0.20 * max(0.0, intake_events - successes)))))
    track_coverage = _clamp(float(feature_vector.get("track_coverage_0_1") or 0.0), 0.0, 1.0)
    throughput_cov = _clamp(float(feature_vector.get("throughput_coverage_0_1") or 0.0), 0.0, 1.0)
    confidence = _clamp(0.40 + (0.26 * track_coverage) + (0.28 * throughput_cov), 0.0, 0.92)
    evidence = [
        {
            "type": "analysis_inference",
            "ref_id": "auto_scout_rule:intake_failures",
            "t_sec": 0.0,
            "meta": {
                "teleop_attempt_count": int(round(attempts)),
                "teleop_success_count": int(round(successes)),
                "depot_intake_count": int(round(intake_events)),
            },
        }
    ]
    return max(0, estimate), confidence, evidence


def _resolve_prediction(
    context: PredictorContext,
    *,
    field_name: str,
    deterministic: tuple[int, float, list[dict[str, Any]]],
) -> PredictorResult:
    value_det, conf_det, evidence_det = deterministic
    predictions = _ml_predictions(context)
    if field_name not in predictions:
        # Field absent from the threshold map (e.g. a newly registered round-2 field):
        # compute + cache on demand rather than silently skipping ML for it.
        predictions[field_name] = _ml_prediction(
            context,
            field_name=field_name,
            feature_vector=_feature_vector(context),
            threshold=_ML_FIELD_THRESHOLDS.get(field_name, _DEFAULT_ML_THRESHOLD),
        )
    ml_result = predictions[field_name]
    if ml_result is not None:
        return ml_result
    return PredictorResult(
        value=value_det,
        confidence=float(_clamp(conf_det, 0.0, 1.0)),
        provenance="auto",
        evidence_refs=evidence_det,
    )


@register("teleop_under_defense_scored")
def predict_teleop_under_defense_scored(context: PredictorContext) -> PredictorResult:
    return _resolve_prediction(
        context,
        field_name="teleop_under_defense_scored",
        deterministic=_deterministic_teleop_under_defense_scored(_feature_vector(context)),
    )


@register("offense_level_1_5")
def predict_offense_level_1_5(context: PredictorContext) -> PredictorResult:
    return _resolve_prediction(
        context,
        field_name="offense_level_1_5",
        deterministic=_deterministic_offense_level(_feature_vector(context)),
    )


@register("defense_level_1_5")
def predict_defense_level_1_5(context: PredictorContext) -> PredictorResult:
    return _resolve_prediction(
        context,
        field_name="defense_level_1_5",
        deterministic=_deterministic_defense_level(_feature_vector(context)),
    )


@register("field_awareness_1_5")
def predict_field_awareness_1_5(context: PredictorContext) -> PredictorResult:
    return _resolve_prediction(
        context,
        field_name="field_awareness_1_5",
        deterministic=_deterministic_field_awareness(_feature_vector(context)),
    )


@register("decision_quality_1_5")
def predict_decision_quality_1_5(context: PredictorContext) -> PredictorResult:
    return _resolve_prediction(
        context,
        field_name="decision_quality_1_5",
        deterministic=_deterministic_decision_quality(_feature_vector(context)),
    )


@register("intake_failures")
def predict_intake_failures(context: PredictorContext) -> PredictorResult:
    return _resolve_prediction(
        context,
        field_name="intake_failures",
        deterministic=_deterministic_intake_failures(_feature_vector(context)),
    )
