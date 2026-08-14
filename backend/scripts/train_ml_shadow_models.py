#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings
from app.db import models
from app.db.session import SessionLocal
from app.services.auto_scout.ml import (
    AUTO_SCOUT_FIELD_FEATURE_ORDER,
    AUTO_SCOUT_FIELD_MODEL_PREFIX,
    auto_scout_field_model_key,
    normalize_auto_scout_field_name,
)
from app.services.auto_scout.training import auto_scout_training_source_version
from app.services.ml.shadow import (
    MATCH_OUTCOME_FEATURE_ORDER,
    MATCH_OUTCOME_MODEL_KEY,
    TEAM_STRENGTH_FEATURE_ORDER,
    TEAM_STRENGTH_MODEL_KEY,
    rebuild_feature_snapshots,
    train_auto_scout_field_shadow_model,
    train_match_outcome_shadow_model,
    train_role_signal_shadow_model,
    train_synergy_pair_shadow_model,
    train_team_strength_shadow_model,
)
from app.services.ml.role_ml import (
    ROLE_SIGNAL_FEATURE_ORDER,
    ROLE_SIGNAL_MODEL_PREFIX,
    ROLE_SIGNAL_NAMES,
    normalize_role_signal_name,
    role_signal_model_key,
)
from app.services.ml.synergy_ml import (
    SYNERGY_PAIR_FEATURE_ORDER,
    SYNERGY_PAIR_MODEL_KEY,
)


HOLDOUT_MIN_ROWS = 80
FEATURE_NON_NULL_MIN_RATE = 0.90
MIN_ROWS_BY_MODEL_KEY = {
    TEAM_STRENGTH_MODEL_KEY: 500,
    MATCH_OUTCOME_MODEL_KEY: 500,
    SYNERGY_PAIR_MODEL_KEY: 300,
}
MIN_ROWS_AUTO_SCOUT_FIELD = 200
MIN_ROWS_ROLE_SIGNAL = 200


def _is_role_signal_model_key(model_key: object) -> bool:
    token = str(model_key or "").strip().lower()
    return token.startswith(f"{ROLE_SIGNAL_MODEL_PREFIX}:")


def _signal_name_from_role_model_key(model_key: object) -> str | None:
    if not _is_role_signal_model_key(model_key):
        return None
    _, raw_name = str(model_key or "").strip().split(":", 1)
    normalized = normalize_role_signal_name(raw_name)
    return normalized if normalized in ROLE_SIGNAL_NAMES else None


def _is_auto_scout_field_model_key(model_key: object) -> bool:
    token = str(model_key or "").strip().lower()
    return token.startswith(f"{AUTO_SCOUT_FIELD_MODEL_PREFIX}:")


def _field_name_from_model_key(model_key: object) -> str | None:
    if not _is_auto_scout_field_model_key(model_key):
        return None
    _, raw_field = str(model_key or "").strip().split(":", 1)
    normalized = normalize_auto_scout_field_name(raw_field)
    return normalized or None


def _resolve_model_key(*, model_key: str | None, legacy_model: str | None, field_name: str | None) -> str:
    raw_model = str(model_key or legacy_model or "all").strip().lower()
    if raw_model in {TEAM_STRENGTH_MODEL_KEY, MATCH_OUTCOME_MODEL_KEY, SYNERGY_PAIR_MODEL_KEY, "all"}:
        return raw_model
    if raw_model == AUTO_SCOUT_FIELD_MODEL_PREFIX:
        normalized_field = normalize_auto_scout_field_name(field_name)
        if not normalized_field:
            raise RuntimeError(
                "field_name is required when model-key is auto_scout_field. "
                "Use --field-name offense_level_1_5 or --model-key auto_scout_field:offense_level_1_5."
            )
        return auto_scout_field_model_key(normalized_field)
    if _is_auto_scout_field_model_key(raw_model):
        normalized_field = _field_name_from_model_key(raw_model)
        if not normalized_field:
            raise RuntimeError("auto_scout_field model key is missing the field name.")
        return auto_scout_field_model_key(normalized_field)
    if raw_model == ROLE_SIGNAL_MODEL_PREFIX:
        normalized_signal = normalize_role_signal_name(field_name)
        if not normalized_signal or normalized_signal not in ROLE_SIGNAL_NAMES:
            raise RuntimeError(
                f"signal_name (via --field-name) is required when model-key is {ROLE_SIGNAL_MODEL_PREFIX}. "
                f"Use --field-name scorer or --model-key role_signal:scorer. "
                f"Valid signals: {ROLE_SIGNAL_NAMES}"
            )
        return role_signal_model_key(normalized_signal)
    if _is_role_signal_model_key(raw_model):
        normalized_signal = _signal_name_from_role_model_key(raw_model)
        if not normalized_signal:
            raise RuntimeError(
                f"Invalid role_signal model key. Valid signals: {ROLE_SIGNAL_NAMES}"
            )
        return role_signal_model_key(normalized_signal)
    raise RuntimeError(
        "Unsupported model key. Use one of: team_strength, match_outcome, synergy_pair, all, "
        "auto_scout_field:<field_name>, or role_signal:<signal_name>."
    )


def _source_version_for_model(model_key: str, explicit_source_version: str | None) -> str:
    explicit = str(explicit_source_version or "").strip()
    if explicit:
        return explicit
    if _is_auto_scout_field_model_key(model_key):
        return auto_scout_training_source_version()
    return str(getattr(settings, "ml_shadow_feature_source_version", "") or "").strip() or "shadow_features_v1"


def _feature_order_for_model(model_key: str) -> list[str]:
    if model_key == TEAM_STRENGTH_MODEL_KEY:
        return list(TEAM_STRENGTH_FEATURE_ORDER)
    if model_key == MATCH_OUTCOME_MODEL_KEY:
        return list(MATCH_OUTCOME_FEATURE_ORDER)
    if model_key == SYNERGY_PAIR_MODEL_KEY:
        return list(SYNERGY_PAIR_FEATURE_ORDER)
    if _is_auto_scout_field_model_key(model_key):
        return list(AUTO_SCOUT_FIELD_FEATURE_ORDER)
    if _is_role_signal_model_key(model_key):
        return list(ROLE_SIGNAL_FEATURE_ORDER)
    raise RuntimeError(f"Unsupported model key for feature-order lookup: {model_key}")


def _min_rows_for_model(model_key: str) -> int:
    if _is_auto_scout_field_model_key(model_key):
        return MIN_ROWS_AUTO_SCOUT_FIELD
    if _is_role_signal_model_key(model_key):
        return MIN_ROWS_ROLE_SIGNAL
    return int(MIN_ROWS_BY_MODEL_KEY.get(model_key) or 500)


def _scope_for_model(model_key: str) -> str:
    if model_key in {TEAM_STRENGTH_MODEL_KEY, MATCH_OUTCOME_MODEL_KEY, SYNERGY_PAIR_MODEL_KEY}:
        return model_key
    if _is_auto_scout_field_model_key(model_key):
        return model_key
    if _is_role_signal_model_key(model_key):
        return model_key
    raise RuntimeError(f"Unsupported model key for scope lookup: {model_key}")


def _feature_non_null_rate(db, *, scope: str, source_version: str, feature_order: list[str]) -> float:
    rows = (
        db.query(models.MLFeatureSnapshot.feature_vector)
        .filter(
            models.MLFeatureSnapshot.scope == scope,
            models.MLFeatureSnapshot.source_version == source_version,
        )
        .all()
    )
    if not rows or not feature_order:
        return 0.0
    total_cells = len(rows) * len(feature_order)
    if total_cells <= 0:
        return 0.0
    populated = 0
    for (feature_vector,) in rows:
        vector = feature_vector if isinstance(feature_vector, dict) else {}
        for feature_name in feature_order:
            if feature_name not in vector:
                continue
            if vector.get(feature_name) is None:
                continue
            populated += 1
    return float(populated) / float(total_cells)


def _assert_data_quality_gates(db, *, model_key: str, source_version: str) -> dict[str, Any]:
    scope = _scope_for_model(model_key)
    base_query = (
        db.query(models.MLFeatureSnapshot)
        .filter(
            models.MLFeatureSnapshot.scope == scope,
            models.MLFeatureSnapshot.source_version == source_version,
        )
    )
    total_rows = int(base_query.count())
    holdout_rows = int(base_query.filter(models.MLFeatureSnapshot.split_tag == "holdout").count())
    feature_order = _feature_order_for_model(model_key)
    non_null_rate = _feature_non_null_rate(
        db,
        scope=scope,
        source_version=source_version,
        feature_order=feature_order,
    )

    min_rows = _min_rows_for_model(model_key)
    if total_rows < min_rows:
        raise RuntimeError(
            f"Data quality gate failed for {model_key}: labeled rows {total_rows} < {min_rows} "
            f"(scope={scope}, source_version={source_version})."
        )
    if holdout_rows < HOLDOUT_MIN_ROWS:
        raise RuntimeError(
            f"Data quality gate failed for {model_key}: holdout rows {holdout_rows} < {HOLDOUT_MIN_ROWS} "
            f"(scope={scope}, source_version={source_version})."
        )
    if non_null_rate < FEATURE_NON_NULL_MIN_RATE:
        raise RuntimeError(
            f"Data quality gate failed for {model_key}: non-null feature rate {non_null_rate:.4f} < "
            f"{FEATURE_NON_NULL_MIN_RATE:.2f} (scope={scope}, source_version={source_version})."
        )

    return {
        "model_key": model_key,
        "scope": scope,
        "source_version": source_version,
        "labeled_rows": total_rows,
        "holdout_rows": holdout_rows,
        "feature_non_null_rate": round(non_null_rate, 4),
        "min_labeled_rows_required": min_rows,
        "min_holdout_rows_required": HOLDOUT_MIN_ROWS,
        "min_feature_non_null_rate_required": FEATURE_NON_NULL_MIN_RATE,
    }


def _train_single_model(
    db,
    *,
    model_key: str,
    model_version: str | None,
    source_version: str,
    activate: bool,
    train_ratio: float,
    epochs: int | None,
    learning_rate: float | None,
) -> dict[str, Any]:
    common_kwargs: dict[str, Any] = {
        "model_version": model_version,
        "source_version": source_version,
        "activate": bool(activate),
        "train_ratio": float(train_ratio),
    }
    if epochs is not None:
        common_kwargs["epochs"] = int(epochs)
    if learning_rate is not None:
        common_kwargs["learning_rate"] = float(learning_rate)

    if model_key == TEAM_STRENGTH_MODEL_KEY:
        return train_team_strength_shadow_model(db, **common_kwargs)
    if model_key == MATCH_OUTCOME_MODEL_KEY:
        return train_match_outcome_shadow_model(db, **common_kwargs)
    if model_key == SYNERGY_PAIR_MODEL_KEY:
        return train_synergy_pair_shadow_model(db, **common_kwargs)
    if _is_role_signal_model_key(model_key):
        signal_name = _signal_name_from_role_model_key(model_key)
        if not signal_name:
            raise RuntimeError("role_signal model key is missing the signal name.")
        return train_role_signal_shadow_model(
            db,
            signal_name=signal_name,
            **common_kwargs,
        )
    if _is_auto_scout_field_model_key(model_key):
        field_name = _field_name_from_model_key(model_key)
        if not field_name:
            raise RuntimeError("auto_scout_field model key is missing the field name.")
        return train_auto_scout_field_shadow_model(
            db,
            field_name=field_name,
            **common_kwargs,
        )
    raise RuntimeError(f"Unsupported model key: {model_key}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Train ML shadow models (torch).")
    parser.add_argument("--model-key", default=None)
    parser.add_argument(
        "--model",
        default=None,
        help="Legacy alias for --model-key.",
    )
    parser.add_argument(
        "--field-name",
        default=None,
        help="Field for auto_scout_field model training when --model-key auto_scout_field is used.",
    )
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--source-version", default=None)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--learning-rate", type=float, default=None)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--no-activate", dest="activate", action="store_false")
    parser.set_defaults(activate=True)
    parser.add_argument("--rebuild-snapshots", action="store_true")
    parser.add_argument("--event-key", default=None)
    parser.add_argument("--limit-events", type=int, default=30)
    parser.add_argument("--skip-data-gates", action="store_true")
    args = parser.parse_args()

    if args.epochs is not None and int(args.epochs) <= 0:
        raise RuntimeError("--epochs must be greater than 0.")
    if args.learning_rate is not None and float(args.learning_rate) <= 0.0:
        raise RuntimeError("--learning-rate must be greater than 0.")

    resolved_model_key = _resolve_model_key(
        model_key=args.model_key,
        legacy_model=args.model,
        field_name=args.field_name,
    )
    model_keys = (
        [TEAM_STRENGTH_MODEL_KEY, MATCH_OUTCOME_MODEL_KEY]
        if resolved_model_key == "all"
        else [resolved_model_key]
    )

    db = SessionLocal()
    try:
        output: dict[str, Any] = {"ok": True, "model_key": resolved_model_key}

        if args.rebuild_snapshots:
            output["snapshot_rebuild"] = rebuild_feature_snapshots(
                db,
                event_key=args.event_key,
                limit_events=int(args.limit_events),
                replace_existing=True,
                source_version=args.source_version,
            )

        gate_results: dict[str, Any] = {}
        if not bool(args.skip_data_gates):
            for model_key in model_keys:
                model_source_version = _source_version_for_model(model_key, args.source_version)
                gate_results[model_key] = _assert_data_quality_gates(
                    db,
                    model_key=model_key,
                    source_version=model_source_version,
                )
        output["data_quality_gates"] = gate_results

        if len(model_keys) == 1:
            key = model_keys[0]
            output["result"] = _train_single_model(
                db,
                model_key=key,
                model_version=args.model_version,
                source_version=_source_version_for_model(key, args.source_version),
                activate=bool(args.activate),
                train_ratio=float(args.train_ratio),
                epochs=args.epochs,
                learning_rate=args.learning_rate,
            )
        else:
            output["results"] = {}
            for key in model_keys:
                output["results"][key] = _train_single_model(
                    db,
                    model_key=key,
                    model_version=None,
                    source_version=_source_version_for_model(key, args.source_version),
                    activate=bool(args.activate),
                    train_ratio=float(args.train_ratio),
                    epochs=args.epochs,
                    learning_rate=args.learning_rate,
                )

        print(json.dumps(output, indent=2, sort_keys=True, default=str))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
