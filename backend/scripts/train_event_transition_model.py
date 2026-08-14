#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import csv
import json
import math
import random
from pathlib import Path
import sys

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.events.classifier import DEFAULT_EVENT_LABELS, DEFAULT_FEATURE_NAMES


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exp_scores = np.exp(shifted)
    denom = np.sum(exp_scores, axis=1, keepdims=True)
    denom = np.maximum(denom, 1e-9)
    return exp_scores / denom


def _one_hot(indices: np.ndarray, class_count: int) -> np.ndarray:
    out = np.zeros((indices.shape[0], class_count), dtype=np.float64)
    out[np.arange(indices.shape[0]), indices] = 1.0
    return out


def _accuracy(probabilities: np.ndarray, labels: np.ndarray) -> float:
    pred = np.argmax(probabilities, axis=1)
    return float(np.mean((pred == labels).astype(np.float64)))


def _binary_f1(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    tp = float(np.sum((y_true == 1) & (y_pred == 1)))
    fp = float(np.sum((y_true == 0) & (y_pred == 1)))
    fn = float(np.sum((y_true == 1) & (y_pred == 0)))
    precision = tp / (tp + fp) if (tp + fp) > 1e-9 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 1e-9 else 0.0
    if (precision + recall) <= 1e-9:
        return 0.0
    return (2.0 * precision * recall) / (precision + recall)


def _apply_gates(
    *,
    pred_idx: np.ndarray,
    pred_conf: np.ndarray,
    top2_margin: np.ndarray,
    class_labels: list[str],
    none_index: int,
    class_thresholds: dict[str, float],
    margin_threshold: float,
) -> np.ndarray:
    gated = np.full_like(pred_idx, fill_value=none_index)
    for row_idx, label_idx in enumerate(pred_idx):
        if int(label_idx) == int(none_index):
            continue
        label = str(class_labels[int(label_idx)])
        threshold = float(class_thresholds.get(label, 0.58))
        if float(pred_conf[row_idx]) >= threshold and float(top2_margin[row_idx]) >= float(margin_threshold):
            gated[row_idx] = int(label_idx)
    return gated


def _macro_f1_non_none(
    *,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    class_labels: list[str],
    none_index: int,
) -> float:
    scores: list[float] = []
    for idx, _ in enumerate(class_labels):
        if idx == none_index:
            continue
        y_true_bin = (y_true == idx).astype(np.int64)
        y_pred_bin = (y_pred == idx).astype(np.int64)
        if int(np.sum(y_true_bin)) <= 0:
            continue
        scores.append(_binary_f1(y_true_bin, y_pred_bin))
    if not scores:
        return 0.0
    return float(sum(scores) / len(scores))


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a transition event classifier (multinomial logistic).")
    parser.add_argument("--input", required=True, help="CSV exported by export_event_transition_dataset.py")
    parser.add_argument("--output", default="media/models/frc_event_transition_v1.json")
    parser.add_argument("--epochs", type=int, default=700)
    parser.add_argument("--lr", type=float, default=0.05)
    parser.add_argument("--l2", type=float, default=0.0008)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--min-class-samples", type=int, default=20)
    args = parser.parse_args()

    random.seed(int(args.seed))
    np.random.seed(int(args.seed))

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not input_path.exists():
        raise FileNotFoundError(f"Input dataset not found: {input_path}")

    rows: list[dict[str, str]] = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)
    if not rows:
        raise RuntimeError("Input dataset is empty.")

    label_counts: dict[str, int] = {}
    for row in rows:
        label = str(row.get("label") or "none")
        label_counts[label] = label_counts.get(label, 0) + 1

    class_labels = [label for label in DEFAULT_EVENT_LABELS if label_counts.get(label, 0) >= int(args.min_class_samples)]
    if "none" not in class_labels:
        class_labels.insert(0, "none")
    label_to_index = {label: idx for idx, label in enumerate(class_labels)}

    feature_names = DEFAULT_FEATURE_NAMES.copy()
    x_rows: list[list[float]] = []
    y_rows: list[int] = []
    for row in rows:
        label = str(row.get("label") or "none")
        if label not in label_to_index:
            label = "none"
        features = []
        for name in feature_names:
            raw = row.get(name)
            try:
                value = float(raw) if raw is not None and raw != "" else 0.0
            except Exception:
                value = 0.0
            features.append(value)
        x_rows.append(features)
        y_rows.append(int(label_to_index[label]))

    x_all = np.asarray(x_rows, dtype=np.float64)
    y_all = np.asarray(y_rows, dtype=np.int64)
    n_samples, n_features = x_all.shape
    class_count = len(class_labels)
    if n_samples < max(200, class_count * 20):
        raise RuntimeError(f"Not enough samples for stable training. Got {n_samples}.")

    indices = np.arange(n_samples)
    np.random.shuffle(indices)
    val_ratio = max(0.05, min(0.4, float(args.val_ratio)))
    val_size = int(math.floor(n_samples * val_ratio))
    val_size = max(class_count, min(n_samples - class_count, val_size))
    train_idx = indices[val_size:]
    val_idx = indices[:val_size]

    x_train = x_all[train_idx]
    y_train = y_all[train_idx]
    x_val = x_all[val_idx]
    y_val = y_all[val_idx]

    mean = np.mean(x_train, axis=0)
    std = np.std(x_train, axis=0)
    std = np.where(std < 1e-9, 1.0, std)

    x_train_n = (x_train - mean) / std
    x_val_n = (x_val - mean) / std

    w = np.zeros((class_count, n_features), dtype=np.float64)
    b = np.zeros((class_count,), dtype=np.float64)
    y_one_hot = _one_hot(y_train, class_count)

    lr = max(1e-5, float(args.lr))
    l2 = max(0.0, float(args.l2))
    epochs = max(50, int(args.epochs))

    for epoch in range(epochs):
        logits = x_train_n @ w.T + b
        probs = _softmax(logits)
        err = probs - y_one_hot
        grad_w = (err.T @ x_train_n) / float(x_train_n.shape[0]) + (l2 * w)
        grad_b = np.mean(err, axis=0)
        w -= lr * grad_w
        b -= lr * grad_b
        if epoch % 100 == 0 or epoch == epochs - 1:
            train_acc = _accuracy(probs, y_train)
            val_probs = _softmax(x_val_n @ w.T + b)
            val_acc = _accuracy(val_probs, y_val)
            print(
                f"epoch={epoch:04d} train_acc={train_acc:.4f} val_acc={val_acc:.4f}",
                flush=True,
            )

    train_probs = _softmax(x_train_n @ w.T + b)
    val_probs = _softmax(x_val_n @ w.T + b)
    val_pred_idx = np.argmax(val_probs, axis=1)
    val_pred_conf = val_probs[np.arange(val_probs.shape[0]), val_pred_idx]
    if class_count >= 2:
        val_sorted = np.sort(val_probs, axis=1)
        val_top2_margin = val_sorted[:, -1] - val_sorted[:, -2]
    else:
        val_top2_margin = np.ones((val_probs.shape[0],), dtype=np.float64)

    none_index = int(class_labels.index("none")) if "none" in class_labels else 0
    class_thresholds: dict[str, float] = {}
    threshold_candidates = np.linspace(0.30, 0.92, 32)
    for idx, label in enumerate(class_labels):
        if idx == none_index:
            continue
        y_true_bin = (y_val == idx).astype(np.int64)
        if int(np.sum(y_true_bin)) < 8:
            class_thresholds[label] = 0.58
            continue
        best_threshold = 0.58
        best_f1 = -1.0
        for threshold in threshold_candidates:
            y_pred_bin = (
                (val_pred_idx == idx)
                & (val_pred_conf >= float(threshold))
            ).astype(np.int64)
            f1 = _binary_f1(y_true_bin, y_pred_bin)
            if f1 > best_f1:
                best_f1 = f1
                best_threshold = float(threshold)
        class_thresholds[label] = round(float(best_threshold), 4)

    margin_candidates = np.linspace(0.0, 0.32, 17)
    best_margin = 0.0
    best_macro_f1 = -1.0
    best_gated_pred = _apply_gates(
        pred_idx=val_pred_idx,
        pred_conf=val_pred_conf,
        top2_margin=val_top2_margin,
        class_labels=class_labels,
        none_index=none_index,
        class_thresholds=class_thresholds,
        margin_threshold=0.0,
    )
    for margin in margin_candidates:
        gated_pred = _apply_gates(
            pred_idx=val_pred_idx,
            pred_conf=val_pred_conf,
            top2_margin=val_top2_margin,
            class_labels=class_labels,
            none_index=none_index,
            class_thresholds=class_thresholds,
            margin_threshold=float(margin),
        )
        macro_f1 = _macro_f1_non_none(
            y_true=y_val,
            y_pred=gated_pred,
            class_labels=class_labels,
            none_index=none_index,
        )
        if macro_f1 > best_macro_f1:
            best_macro_f1 = macro_f1
            best_margin = float(margin)
            best_gated_pred = gated_pred

    payload = {
        "version": "transition_event_model_v1",
        "class_labels": class_labels,
        "feature_names": feature_names,
        "feature_mean": mean.tolist(),
        "feature_std": std.tolist(),
        "weights": w.tolist(),
        "bias": b.tolist(),
        "logit_temperature": 1.0,
        "class_thresholds": class_thresholds,
        "min_top2_margin": round(float(best_margin), 4),
        "metrics": {
            "train_rows": int(x_train.shape[0]),
            "val_rows": int(x_val.shape[0]),
            "train_accuracy": round(_accuracy(train_probs, y_train), 6),
            "val_accuracy": round(_accuracy(val_probs, y_val), 6),
            "val_accuracy_thresholded": round(float(np.mean((best_gated_pred == y_val).astype(np.float64))), 6),
            "val_macro_f1_non_none": round(float(best_macro_f1), 6),
            "class_counts": {label: int(label_counts.get(label, 0)) for label in class_labels},
        },
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": output_path.as_posix(), "metrics": payload["metrics"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
