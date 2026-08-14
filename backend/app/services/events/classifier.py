from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.utils import BACKEND_ROOT, _safe_float

DEFAULT_EVENT_LABELS = [
    "none",
    "depot_intake",
    "teleop_fuel_score_attempt",
    "climb_attempt",
    "protected_zone_interference",
]
DEFAULT_FEATURE_NAMES = [
    "dt_sec",
    "time_ratio",
    "after_auto",
    "in_endgame",
    "distance_m",
    "speed_mps",
    "speed_px_norm",
    "confidence",
    "prev_loading",
    "prev_scoring",
    "prev_endgame",
    "prev_protected",
    "curr_loading",
    "curr_scoring",
    "curr_endgame",
    "curr_protected",
    "curr_tower",
    "curr_neutral",
    "curr_zone_changed",
]

ENDGAME_ZONE_KEYWORDS = (
    "tower",
    "cage",
    "hangar",
    "stage",
    "chain",
    "rung",
    "climb",
    "endgame",
)


@dataclass(frozen=True)
class TransitionEventModel:
    version: str
    feature_names: list[str]
    class_labels: list[str]
    weights: list[list[float]]
    bias: list[float]
    feature_mean: list[float] | None = None
    feature_std: list[float] | None = None
    logit_temperature: float = 1.0
    class_thresholds: dict[str, float] | None = None
    min_top2_margin: float = 0.0


def _zone_flags(zone_key: str | None, zone_kind: str | None) -> dict[str, float]:
    key = str(zone_key or "").strip().lower()
    kind = str(zone_kind or "").strip().lower()
    endgame_zone = any(keyword in key for keyword in ENDGAME_ZONE_KEYWORDS)
    loading = 1.0 if (kind == "loading" or "loading" in key or "depot" in key) else 0.0
    scoring = 1.0 if (kind == "scoring" or "scoring" in key or "hub" in key) else 0.0
    endgame = 1.0 if (kind == "endgame" or endgame_zone) else 0.0
    protected = 1.0 if (kind == "protected" or "protected" in key) else 0.0
    # Keep legacy "tower" feature semantics, but treat modern climb structures similarly.
    tower = 1.0 if endgame_zone else 0.0
    neutral = 1.0 if ("neutral" in key or kind == "neutral") else 0.0
    return {
        "loading": loading,
        "scoring": scoring,
        "endgame": endgame,
        "protected": protected,
        "tower": tower,
        "neutral": neutral,
    }


def build_transition_feature_map(
    prev_row: dict[str, Any],
    curr_row: dict[str, Any],
    *,
    duration_sec: float,
    auto_window_sec: float,
    endgame_start_sec: float,
) -> dict[str, float]:
    prev_time = _safe_float(prev_row.get("time_sec"), 0.0)
    curr_time = _safe_float(curr_row.get("time_sec"), prev_time)
    dt_sec = max(1e-3, curr_time - prev_time)
    duration = max(1e-3, float(duration_sec))
    time_ratio = min(1.0, max(0.0, curr_time / duration))
    after_auto = 1.0 if curr_time > float(auto_window_sec) else 0.0
    in_endgame = 1.0 if curr_time >= float(endgame_start_sec) else 0.0

    prev_x = prev_row.get("field_x")
    prev_y = prev_row.get("field_y")
    curr_x = curr_row.get("field_x")
    curr_y = curr_row.get("field_y")
    distance_m = 0.0
    if all(isinstance(value, (int, float)) for value in (prev_x, prev_y, curr_x, curr_y)):
        distance_m = math.hypot(float(curr_x) - float(prev_x), float(curr_y) - float(prev_y))

    speed_mps = 0.0
    if isinstance(curr_row.get("speed_mps"), (int, float)):
        speed_mps = float(curr_row["speed_mps"])
    elif isinstance(curr_row.get("speed_px"), (int, float)):
        speed_mps = float(curr_row["speed_px"]) / 45.0
    speed_px_norm = float(curr_row.get("speed_px") or 0.0) / 120.0
    confidence = min(1.0, max(0.0, _safe_float(curr_row.get("confidence"), 0.0)))

    prev_flags = _zone_flags(prev_row.get("zone_key"), prev_row.get("zone_kind"))
    curr_flags = _zone_flags(curr_row.get("zone_key"), curr_row.get("zone_kind"))
    prev_zone = str(prev_row.get("zone_key") or "").strip().lower()
    curr_zone = str(curr_row.get("zone_key") or "").strip().lower()

    return {
        "dt_sec": dt_sec,
        "time_ratio": time_ratio,
        "after_auto": after_auto,
        "in_endgame": in_endgame,
        "distance_m": distance_m,
        "speed_mps": speed_mps,
        "speed_px_norm": speed_px_norm,
        "confidence": confidence,
        "prev_loading": prev_flags["loading"],
        "prev_scoring": prev_flags["scoring"],
        "prev_endgame": prev_flags["endgame"],
        "prev_protected": prev_flags["protected"],
        "curr_loading": curr_flags["loading"],
        "curr_scoring": curr_flags["scoring"],
        "curr_endgame": curr_flags["endgame"],
        "curr_protected": curr_flags["protected"],
        "curr_tower": curr_flags["tower"],
        "curr_neutral": curr_flags["neutral"],
        "curr_zone_changed": 1.0 if curr_zone != prev_zone else 0.0,
    }


def _softmax(values: list[float]) -> list[float]:
    if not values:
        return []
    max_value = max(values)
    exps = [math.exp(value - max_value) for value in values]
    denom = sum(exps)
    if denom <= 1e-9:
        uniform = 1.0 / float(len(values))
        return [uniform for _ in values]
    return [value / denom for value in exps]


def _resolve_model_path(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (BACKEND_ROOT / path).resolve()


def load_transition_event_model(raw_path: str | Path) -> TransitionEventModel | None:
    path = _resolve_model_path(raw_path)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None

    feature_names = payload.get("feature_names")
    if not isinstance(feature_names, list) or not all(isinstance(item, str) for item in feature_names):
        feature_names = DEFAULT_FEATURE_NAMES
    class_labels = payload.get("class_labels")
    if not isinstance(class_labels, list) or not all(isinstance(item, str) for item in class_labels):
        class_labels = DEFAULT_EVENT_LABELS
    weights_raw = payload.get("weights")
    bias_raw = payload.get("bias")
    if not isinstance(weights_raw, list) or not isinstance(bias_raw, list):
        return None
    if len(weights_raw) != len(class_labels) or len(bias_raw) != len(class_labels):
        return None

    weights: list[list[float]] = []
    for row in weights_raw:
        if not isinstance(row, list) or len(row) != len(feature_names):
            return None
        weights.append([_safe_float(value) for value in row])
    bias = [_safe_float(value) for value in bias_raw]

    feature_mean = payload.get("feature_mean")
    feature_std = payload.get("feature_std")
    if not isinstance(feature_mean, list) or len(feature_mean) != len(feature_names):
        feature_mean = None
    else:
        feature_mean = [_safe_float(value) for value in feature_mean]
    if not isinstance(feature_std, list) or len(feature_std) != len(feature_names):
        feature_std = None
    else:
        feature_std = [_safe_float(value, 1.0) for value in feature_std]

    thresholds_raw = payload.get("class_thresholds")
    class_thresholds: dict[str, float] | None = None
    if isinstance(thresholds_raw, dict):
        parsed: dict[str, float] = {}
        for key, value in thresholds_raw.items():
            label = str(key or "").strip()
            if not label:
                continue
            threshold = min(0.99, max(0.0, _safe_float(value, 0.0)))
            parsed[label] = threshold
        if parsed:
            class_thresholds = parsed

    min_top2_margin = min(0.95, max(0.0, _safe_float(payload.get("min_top2_margin"), 0.0)))

    return TransitionEventModel(
        version=str(payload.get("version") or "transition_event_model_v1"),
        feature_names=[str(item) for item in feature_names],
        class_labels=[str(item) for item in class_labels],
        weights=weights,
        bias=bias,
        feature_mean=feature_mean,
        feature_std=feature_std,
        logit_temperature=max(0.1, _safe_float(payload.get("logit_temperature"), 1.0)),
        class_thresholds=class_thresholds,
        min_top2_margin=min_top2_margin,
    )


def predict_transition_event(
    model: TransitionEventModel,
    feature_map: dict[str, float],
) -> dict[str, Any]:
    vector = [_safe_float(feature_map.get(name), 0.0) for name in model.feature_names]
    if model.feature_mean is not None and model.feature_std is not None:
        normalized: list[float] = []
        for value, mean, std in zip(vector, model.feature_mean, model.feature_std):
            scale = std if abs(std) > 1e-9 else 1.0
            normalized.append((value - mean) / scale)
        vector = normalized

    logits: list[float] = []
    for row, bias in zip(model.weights, model.bias):
        logit = _safe_float(bias)
        for weight, feature_value in zip(row, vector):
            logit += _safe_float(weight) * _safe_float(feature_value)
        logits.append(logit / model.logit_temperature)

    probabilities = _softmax(logits)
    if not probabilities:
        return {"label": "none", "confidence": 0.0, "probabilities": {}, "top_2_margin": 0.0}
    best_index = max(range(len(probabilities)), key=lambda idx: probabilities[idx])
    sorted_probs = sorted(probabilities, reverse=True)
    top_2_margin = float(sorted_probs[0] - sorted_probs[1]) if len(sorted_probs) > 1 else float(sorted_probs[0])
    return {
        "label": str(model.class_labels[best_index]),
        "confidence": float(probabilities[best_index]),
        "probabilities": {
            str(label): round(float(prob), 6)
            for label, prob in zip(model.class_labels, probabilities)
        },
        "top_2_margin": float(top_2_margin),
    }


def resolve_model_event_label(
    model: TransitionEventModel,
    prediction: dict[str, Any],
    *,
    default_conf_threshold: float,
    default_margin_threshold: float = 0.0,
) -> dict[str, Any]:
    raw_label = str(prediction.get("label") or "none")
    confidence = _safe_float(prediction.get("confidence"), 0.0)
    top_2_margin = _safe_float(prediction.get("top_2_margin"), 0.0)
    confidence_threshold = max(0.0, min(0.99, float(default_conf_threshold)))
    if model.class_thresholds and raw_label in model.class_thresholds:
        confidence_threshold = max(0.0, min(0.99, _safe_float(model.class_thresholds.get(raw_label), confidence_threshold)))
    margin_threshold = max(
        0.0,
        min(
            0.95,
            max(
                float(default_margin_threshold),
                float(model.min_top2_margin),
            ),
        ),
    )
    passed = (
        raw_label != "none"
        and confidence >= confidence_threshold
        and top_2_margin >= margin_threshold
    )
    return {
        "label": raw_label if passed else "none",
        "raw_label": raw_label,
        "passed": bool(passed),
        "confidence": confidence,
        "confidence_threshold": confidence_threshold,
        "top_2_margin": top_2_margin,
        "margin_threshold": margin_threshold,
    }
