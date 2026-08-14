from __future__ import annotations

import math
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")

def clamp_probability(value: float) -> float:
    return max(1e-6, min(1.0 - 1e-6, float(value)))

def binary_log_loss(targets: Sequence[float], probabilities: Sequence[float]) -> float | None:
    if not targets or len(targets) != len(probabilities):
        return None
    total = 0.0
    count = 0
    for y_raw, p_raw in zip(targets, probabilities):
        y = 1.0 if float(y_raw) >= 0.5 else 0.0
        p = clamp_probability(float(p_raw))
        total += -((y * math.log(p)) + ((1.0 - y) * math.log(1.0 - p)))
        count += 1
    if count <= 0:
        return None
    return total / float(count)

def brier_score(targets: Sequence[float], probabilities: Sequence[float]) -> float | None:
    if not targets or len(targets) != len(probabilities):
        return None
    total = 0.0
    count = 0
    for y_raw, p_raw in zip(targets, probabilities):
        y = 1.0 if float(y_raw) >= 0.5 else 0.0
        p = float(p_raw)
        total += (p - y) ** 2
        count += 1
    if count <= 0:
        return None
    return total / float(count)

def mean_absolute_error(actual: Sequence[float], predicted: Sequence[float]) -> float | None:
    if not actual or len(actual) != len(predicted):
        return None
    total = 0.0
    count = 0
    for y_true, y_pred in zip(actual, predicted):
        total += abs(float(y_true) - float(y_pred))
        count += 1
    if count <= 0:
        return None
    return total / float(count)

def root_mean_squared_error(actual: Sequence[float], predicted: Sequence[float]) -> float | None:
    if not actual or len(actual) != len(predicted):
        return None
    total = 0.0
    count = 0
    for y_true, y_pred in zip(actual, predicted):
        total += (float(y_true) - float(y_pred)) ** 2
        count += 1
    if count <= 0:
        return None
    return math.sqrt(total / float(count))

def r2_score(actual: Sequence[float], predicted: Sequence[float]) -> float | None:
    # Coefficient of determination (R²).
    if not actual or len(actual) != len(predicted) or len(actual) < 2:
        return None
    mean_actual = sum(float(y) for y in actual) / float(len(actual))
    ss_res = sum((float(y) - float(yhat)) ** 2 for y, yhat in zip(actual, predicted))
    ss_tot = sum((float(y) - mean_actual) ** 2 for y in actual)
    if ss_tot < 1e-12:
        return None
    return 1.0 - (ss_res / ss_tot)

def expected_calibration_error(
    targets: Sequence[float],
    probabilities: Sequence[float],
    n_bins: int = 10,
) -> float | None:
    # Expected Calibration Error for binary classification.
    if not targets or len(targets) != len(probabilities):
        return None
    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for target, prob in zip(targets, probabilities):
        idx = min(int(float(prob) * n_bins), n_bins - 1)
        bins[idx].append((float(target), float(prob)))
    total = float(len(targets))
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        avg_conf = sum(p for _, p in bucket) / float(len(bucket))
        avg_acc = sum(1.0 if t >= 0.5 else 0.0 for t, _ in bucket) / float(len(bucket))
        ece += (float(len(bucket)) / total) * abs(avg_acc - avg_conf)
    return ece

def _rank_values(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(float(v) for v in values), key=lambda row: row[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (float(i + 1) + float(j + 1)) / 2.0
        for k in range(i, j + 1):
            original_index = indexed[k][0]
            ranks[original_index] = avg_rank
        i = j + 1
    return ranks

def spearman_rank_correlation(lhs: Sequence[float], rhs: Sequence[float]) -> float | None:
    if len(lhs) != len(rhs) or len(lhs) < 2:
        return None

    lhs_ranks = _rank_values(lhs)
    rhs_ranks = _rank_values(rhs)

    lhs_mean = sum(lhs_ranks) / float(len(lhs_ranks))
    rhs_mean = sum(rhs_ranks) / float(len(rhs_ranks))
    cov = 0.0
    lhs_var = 0.0
    rhs_var = 0.0
    for l_rank, r_rank in zip(lhs_ranks, rhs_ranks):
        ld = l_rank - lhs_mean
        rd = r_rank - rhs_mean
        cov += ld * rd
        lhs_var += ld * ld
        rhs_var += rd * rd

    if lhs_var <= 0.0 or rhs_var <= 0.0:
        return None
    return cov / math.sqrt(lhs_var * rhs_var)

def time_split(rows: Sequence[T], *, timestamp_fn, train_ratio: float = 0.8) -> tuple[list[T], list[T]]:
    if not rows:
        return [], []
    bounded_ratio = max(0.5, min(float(train_ratio), 0.95))
    ordered = sorted(rows, key=timestamp_fn)
    split_idx = int(len(ordered) * bounded_ratio)
    split_idx = max(1, min(len(ordered) - 1, split_idx)) if len(ordered) > 1 else 1
    return list(ordered[:split_idx]), list(ordered[split_idx:])

def summarize_distribution(values: Iterable[float]) -> dict[str, float]:
    normalized = [float(v) for v in values]
    if not normalized:
        return {"count": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "count": float(len(normalized)),
        "min": min(normalized),
        "max": max(normalized),
        "mean": sum(normalized) / float(len(normalized)),
    }
