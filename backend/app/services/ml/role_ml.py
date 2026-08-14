# Phase 5 — ML role classifier feature vector builder and training data export.
#
# Replaces the hand-tuned RoleClassifier with ML-predicted role signals.
# Uses per-team EventTeamRating features as input, and the deterministic
# RoleClassifier output (stored in details_json.role_classification) as
# training labels.
#
# One model per role signal: scorer, defender, feeder, endgame (0-100 each).
# At inference, the 4 ML-predicted signals are blended with the deterministic
# signals via the ml_role_blend knob.

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app.db import models

ROLE_SIGNAL_MODEL_PREFIX = "role_signal"
FEATURE_SCOPE_ROLE_SIGNAL = "role_signal"
ROLE_SIGNAL_NAMES = ["scorer", "defender", "feeder", "endgame"]

ROLE_SIGNAL_FEATURE_ORDER: list[str] = [
    "rating_0_100",
    "confidence_0_1",
    "throughput_0_100",
    "driver_skill_0_100",
    "robot_level_0_100",
    "endgame_0_100",
    "consistency_0_100",
    "shift_productivity_0_100",
    "capacity_utilization_0_100",
    "auto_contribution_0_100",
    "defense_presence_0_100",
    "penalty_discipline_0_100",
    "expected_net_points",
    "auto_points_est",
    "teleop_points_est",
    "climb_output",
    "uptime_0_1",
    "throughput_coverage_0_1",
    "avg_analysis_quality_0_1",
    "match_count",
    "event_year",
]

def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    return None

def normalize_role_signal_name(name: object) -> str:
    return str(name or "").strip().lower()

def role_signal_model_key(signal_name: str) -> str:
    normalized = normalize_role_signal_name(signal_name)
    return f"{ROLE_SIGNAL_MODEL_PREFIX}:{normalized}"

def role_signal_scope(signal_name: str) -> str:
    normalized = normalize_role_signal_name(signal_name)
    return f"{FEATURE_SCOPE_ROLE_SIGNAL}:{normalized}"

def build_role_signal_feature_vector(
    rating: models.EventTeamRating | None,
    *,
    event_year: int | None = None,
) -> dict[str, float]:
    if rating is None:
        return {name: 0.0 for name in ROLE_SIGNAL_FEATURE_ORDER}

    details = rating.details_json if isinstance(rating.details_json, dict) else {}
    subscores = details.get("subscores") if isinstance(details, dict) else None
    subscores = subscores if isinstance(subscores, dict) else {}
    raw_features = details.get("raw_features") if isinstance(details, dict) else None
    raw_features = raw_features if isinstance(raw_features, dict) else {}
    support = details.get("confidence_support") if isinstance(details, dict) else None
    support = support if isinstance(support, dict) else {}
    quality_gate = details.get("quality_gate") if isinstance(details, dict) else None
    quality_gate = quality_gate if isinstance(quality_gate, dict) else {}

    def _f(val: object, default: float = 0.0) -> float:
        v = _safe_float(val)
        return float(v) if v is not None else default

    return {
        "rating_0_100": _f(rating.rating_0_100, 50.0),
        "confidence_0_1": _f(rating.confidence_0_1, 0.0),
        "throughput_0_100": _f(rating.throughput, 50.0),
        "driver_skill_0_100": _f(rating.driver_skill_0_100, 50.0),
        "robot_level_0_100": _f(rating.robot_level_0_100, 50.0),
        "endgame_0_100": _f(subscores.get("endgame", getattr(rating, "endgame", None)), 50.0),
        "consistency_0_100": _f(subscores.get("consistency", getattr(rating, "consistency", None)), 50.0),
        "shift_productivity_0_100": _f(
            subscores.get("shift_productivity", getattr(rating, "shift_productivity", None)), 50.0
        ),
        "capacity_utilization_0_100": _f(
            subscores.get("capacity_utilization", getattr(rating, "capacity_utilization", None)), 50.0
        ),
        "auto_contribution_0_100": _f(subscores.get("auto_contribution"), 50.0),
        "defense_presence_0_100": _f(subscores.get("defense_presence"), 50.0),
        "penalty_discipline_0_100": _f(subscores.get("penalty_discipline"), 50.0),
        "expected_net_points": _f(raw_features.get("expected_net_points"), 0.0),
        "auto_points_est": _f(raw_features.get("auto_points_est"), 0.0),
        "teleop_points_est": _f(raw_features.get("teleop_points_est"), 0.0),
        "climb_output": _f(raw_features.get("climb_output"), 0.0),
        "uptime_0_1": _f(raw_features.get("uptime"), 0.0),
        "throughput_coverage_0_1": _f(raw_features.get("throughput_coverage"), 0.0),
        "avg_analysis_quality_0_1": _f(support.get("avg_analysis_quality"), 0.0),
        "match_count": _f(quality_gate.get("accepted_match_count", details.get("match_count")), 0.0),
        "event_year": float(event_year or 0),
    }

def rebuild_role_signal_feature_snapshots(
    db: Session,
    *,
    event_keys: set[str],
    source_version: str,
    year_map: dict[str, int],
    signal_names: list[str] | None = None,
) -> dict[str, int]:
    if not event_keys:
        return {"rows_written": 0, "skipped_no_labels": 0}

    from app.services.ml.shadow import _latest_year, _split_tag_for_year

    latest_year = _latest_year(year_map)
    target_signals = signal_names or ROLE_SIGNAL_NAMES
    rows_written = 0
    skipped_no_labels = 0

    for event_key in sorted(event_keys):
        season_candidate = event_key[:4]
        if not season_candidate.isdigit():
            continue
        season = int(season_candidate)

        rating_rows = (
            db.query(models.EventTeamRating)
            .filter(models.EventTeamRating.event_key == event_key)
            .all()
        )

        for rating in rating_rows:
            team_key = str(rating.team_key or "").strip().lower()
            if not team_key:
                continue

            details = rating.details_json if isinstance(rating.details_json, dict) else {}
            role_cls = details.get("role_classification")
            if not isinstance(role_cls, dict):
                skipped_no_labels += 1
                continue

            role_signals = role_cls.get("role_signals")
            if not isinstance(role_signals, dict):
                skipped_no_labels += 1
                continue

            feature_vector = build_role_signal_feature_vector(rating, event_year=season)
            split_tag = _split_tag_for_year(year_map.get(event_key), latest_year)

            for signal_name in target_signals:
                signal_key = f"{signal_name}_signal"
                target_value = _safe_float(role_signals.get(signal_key))
                if target_value is None:
                    continue

                scope = role_signal_scope(signal_name)
                snapshot_key = f"{scope}:{event_key}:{team_key}:{source_version}"
                db.merge(
                    models.MLFeatureSnapshot(
                        snapshot_key=snapshot_key,
                        scope=scope,
                        event_key=event_key,
                        team_key=team_key,
                        alliance_color=None,
                        feature_vector=feature_vector,
                        target={"signal_value": float(target_value)},
                        split_tag=split_tag,
                        source_version=source_version,
                    )
                )
                rows_written += 1

    return {"rows_written": rows_written, "skipped_no_labels": skipped_no_labels}

def blend_role_signals(
    deterministic: dict[str, float],
    ml_predicted: dict[str, float | None],
    blend_knob: float,
) -> dict[str, float]:
    blended: dict[str, float] = {}
    for signal_key in deterministic:
        det_value = float(deterministic.get(signal_key, 50.0))
        ml_value = ml_predicted.get(signal_key)
        if ml_value is not None and blend_knob > 0.0:
            blended[signal_key] = (blend_knob * float(ml_value)) + ((1.0 - blend_knob) * det_value)
        else:
            blended[signal_key] = det_value
    return blended
