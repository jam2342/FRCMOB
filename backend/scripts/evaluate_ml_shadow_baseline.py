#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.ml.model_eval import (
    binary_log_loss,
    brier_score,
    mean_absolute_error,
    root_mean_squared_error,
    spearman_rank_correlation,
    time_split,
)


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            numeric = float(raw)
        except ValueError:
            return None
        return numeric if math.isfinite(numeric) else None
    return None


def _parse_timestamp(row: dict[str, str]) -> float:
    from_feature = _safe_float(row.get("f_match_time_unix"))
    if from_feature is not None:
        return float(from_feature)
    created_at = str(row.get("created_at") or "").strip()
    if created_at:
        normalized = created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
        try:
            return float(datetime.fromisoformat(normalized).timestamp())
        except ValueError:
            pass
    return 0.0


def _team_strength_baseline(row: dict[str, str]) -> float:
    model_prediction = _safe_float(row.get("p_strength_active_bps_pred"))
    if model_prediction is not None:
        return float(model_prediction)

    rating = float(_safe_float(row.get("f_rating_0_100")) or 50.0)
    throughput = float(_safe_float(row.get("f_throughput_0_100")) or 50.0)
    endgame = float(_safe_float(row.get("f_endgame_0_100")) or 50.0)
    consistency = float(_safe_float(row.get("f_consistency_0_100")) or 50.0)

    # Simple transparent baseline for offline comparisons.
    return (0.018 * rating) + (0.046 * throughput) + (0.012 * endgame) + (0.009 * consistency)


def _match_win_baseline_probability(row: dict[str, str]) -> float:
    model_prediction = _safe_float(row.get("p_red_win_prob"))
    if model_prediction is not None:
        return max(1e-6, min(1.0 - 1e-6, float(model_prediction)))

    rating_margin = float(_safe_float(row.get("f_rating_margin")) or 0.0)
    synergy_margin = float(_safe_float(row.get("f_synergy_margin")) or 0.0)
    projected_margin = float(_safe_float(row.get("f_projected_margin")) or 0.0)
    logit = (0.055 * rating_margin) + (0.030 * synergy_margin) + (0.045 * projected_margin)
    probability = 1.0 / (1.0 + math.exp(-logit))
    return max(1e-6, min(1.0 - 1e-6, probability))


def _evaluate_team_strength(rows: list[dict[str, str]], train_ratio: float) -> dict[str, Any]:
    labeled = []
    for row in rows:
        target = _safe_float(row.get("t_strength_active_bps"))
        if target is None:
            continue
        labeled.append(
            {
                "timestamp": _parse_timestamp(row),
                "event_key": str(row.get("event_key") or "").strip().lower(),
                "target": float(target),
                "pred": float(_team_strength_baseline(row)),
            }
        )

    if len(labeled) < 10:
        return {
            "row_count": len(labeled),
            "error": "Not enough labeled team_strength rows for evaluation.",
        }

    train_rows, val_rows = time_split(labeled, timestamp_fn=lambda item: item["timestamp"], train_ratio=train_ratio)
    train_actual = [float(row["target"]) for row in train_rows]
    train_pred = [float(row["pred"]) for row in train_rows]
    val_actual = [float(row["target"]) for row in val_rows]
    val_pred = [float(row["pred"]) for row in val_rows]

    def _rank_corr(split_rows: list[dict[str, Any]]) -> float | None:
        by_event: dict[str, list[dict[str, Any]]] = {}
        for row in split_rows:
            event_key = str(row.get("event_key") or "").strip()
            if not event_key:
                continue
            by_event.setdefault(event_key, []).append(row)

        correlations: list[float] = []
        for event_rows in by_event.values():
            if len(event_rows) < 4:
                continue
            targets = [float(item["target"]) for item in event_rows]
            predictions = [float(item["pred"]) for item in event_rows]
            corr = spearman_rank_correlation(predictions, targets)
            if corr is not None:
                correlations.append(float(corr))

        if not correlations:
            return None
        return sum(correlations) / float(len(correlations))

    return {
        "row_count": len(labeled),
        "train_count": len(train_rows),
        "val_count": len(val_rows),
        "train_mae": mean_absolute_error(train_actual, train_pred),
        "val_mae": mean_absolute_error(val_actual, val_pred),
        "train_rmse": root_mean_squared_error(train_actual, train_pred),
        "val_rmse": root_mean_squared_error(val_actual, val_pred),
        "train_rank_correlation": _rank_corr(train_rows),
        "val_rank_correlation": _rank_corr(val_rows),
    }


def _evaluate_match_outcome(rows: list[dict[str, str]], train_ratio: float) -> dict[str, Any]:
    labeled = []
    for row in rows:
        target_win = _safe_float(row.get("t_red_win"))
        if target_win is None:
            continue
        probability = _match_win_baseline_probability(row)
        target_margin = _safe_float(row.get("t_red_margin"))
        projected_margin = _safe_float(row.get("f_projected_margin"))
        labeled.append(
            {
                "timestamp": _parse_timestamp(row),
                "target_win": float(target_win),
                "pred_win_prob": float(probability),
                "target_margin": float(target_margin) if target_margin is not None else None,
                "pred_margin": float(projected_margin) if projected_margin is not None else None,
            }
        )

    if len(labeled) < 10:
        return {
            "row_count": len(labeled),
            "error": "Not enough labeled match_outcome rows for evaluation.",
        }

    train_rows, val_rows = time_split(labeled, timestamp_fn=lambda item: item["timestamp"], train_ratio=train_ratio)

    def _metrics(split_rows: list[dict[str, Any]]) -> dict[str, Any]:
        targets = [float(row["target_win"]) for row in split_rows]
        probabilities = [float(row["pred_win_prob"]) for row in split_rows]

        margin_actual: list[float] = []
        margin_pred: list[float] = []
        for row in split_rows:
            if row.get("target_margin") is None or row.get("pred_margin") is None:
                continue
            margin_actual.append(float(row["target_margin"]))
            margin_pred.append(float(row["pred_margin"]))

        return {
            "count": len(split_rows),
            "brier": brier_score(targets, probabilities),
            "logloss": binary_log_loss(targets, probabilities),
            "margin_mae": mean_absolute_error(margin_actual, margin_pred) if margin_actual else None,
            "margin_rmse": root_mean_squared_error(margin_actual, margin_pred) if margin_actual else None,
        }

    return {
        "row_count": len(labeled),
        "train": _metrics(train_rows),
        "val": _metrics(val_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate ML shadow dataset with time-split metrics.")
    parser.add_argument("--input", required=True, help="CSV from export_ml_shadow_eval_dataset.py")
    parser.add_argument("--output", default=None, help="Optional JSON report output path")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = (BACKEND_ROOT / input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input dataset not found: {input_path}")

    rows: list[dict[str, str]] = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)

    team_rows = [row for row in rows if str(row.get("scope") or "") == "team_strength"]
    match_rows = [row for row in rows if str(row.get("scope") or "") == "match_outcome"]

    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "input_path": input_path.as_posix(),
        "row_count": len(rows),
        "train_ratio": max(0.5, min(float(args.train_ratio), 0.95)),
        "team_strength": _evaluate_team_strength(team_rows, max(0.5, min(float(args.train_ratio), 0.95))),
        "match_outcome": _evaluate_match_outcome(match_rows, max(0.5, min(float(args.train_ratio), 0.95))),
    }

    output_json = json.dumps(report, indent=2, sort_keys=True)
    print(output_json)

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = (BACKEND_ROOT / output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_json + "\n", encoding="utf-8")
        print(f"wrote report to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
