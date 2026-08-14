# Pure helper functions for the rating model.
#
# These functions depend only on constants, ``app.services.utils``,
# ``app.db.models``, and the standard library — no IO or DB access.

from __future__ import annotations

import math
import re
from typing import Any

from app.db import models
from app.services.utils import _clamp, _mean

from app.services.ratings.constants import (
    RECENT_BASE_WEIGHT,
    RECENT_MATCH_WINDOW,
    RECENT_PRIORITY_WEIGHT,
    RECENT_PRIORITY_WINDOW,
    TBA_SCOREBREAKDOWN_SOURCE,
    _PUBLIC_RATING_CEILING,
    _PUBLIC_RATING_CENTER,
    _PUBLIC_RATING_FLOOR,
    _PUBLIC_RATING_SLOPE,
)

# ── Public-facing rating scale ────────────────────────────────────────
def calibrate_public_rating_scale(raw_score_0_100: float) -> float:
    clamped = _clamp(float(raw_score_0_100), 0.0, 100.0)
    logistic = 1.0 / (1.0 + math.exp(-(clamped - _PUBLIC_RATING_CENTER) / _PUBLIC_RATING_SLOPE))
    scaled = _PUBLIC_RATING_FLOOR + ((_PUBLIC_RATING_CEILING - _PUBLIC_RATING_FLOOR) * logistic)
    return _clamp(scaled, 0.0, 100.0)

# ── Weighted score aggregation ────────────────────────────────────────
def _weighted_score(components: list[tuple[float, float | None]], default: float = 50.0) -> float:
    weighted_sum = 0.0
    total_weight = 0.0
    for weight, value in components:
        if weight <= 0:
            continue
        if value is None:
            continue
        weighted_sum += float(weight) * float(value)
        total_weight += float(weight)
    if total_weight <= 1e-9:
        return _clamp(float(default), 0.0, 100.0)
    return _clamp(weighted_sum / total_weight, 0.0, 100.0)

# ── Recency weighting ────────────────────────────────────────────────
def _recent_weight_for_index(index: int) -> float:
    if index < RECENT_PRIORITY_WINDOW:
        return RECENT_PRIORITY_WEIGHT
    if index < RECENT_MATCH_WINDOW:
        return RECENT_BASE_WEIGHT
    return RECENT_BASE_WEIGHT * 0.5

# ── Trend analysis ───────────────────────────────────────────────────
def _trend_delta_ratio(values_newest_first: list[float], *, max_window: int = 4) -> float | None:
    if len(values_newest_first) < 4:
        return None
    window = min(max_window, max(2, len(values_newest_first) // 2))
    recent = values_newest_first[:window]
    prior = values_newest_first[window : window * 2]
    if not prior:
        return None
    recent_avg = _mean(recent)
    prior_avg = _mean(prior)
    if recent_avg is None or prior_avg is None:
        return None
    baseline = max(0.15, abs(float(prior_avg)))
    return (float(recent_avg) - float(prior_avg)) / baseline

# ── Finding sort / dedupe helpers ─────────────────────────────────────
def _sort_findings_newest_first(
    findings: list[models.TeamMatchFinding],
    match_time_by_key: dict[str, int | None],
) -> list[models.TeamMatchFinding]:
    return sorted(
        findings,
        key=lambda finding: (
            int(match_time_by_key.get(finding.match_key) or -1),
            int(finding.id or 0),
        ),
        reverse=True,
    )

def _event_from_official_source(event: models.MatchEvent) -> bool:
    meta = event.meta if isinstance(event.meta, dict) else {}
    source = str(meta.get("source") or "").strip().lower()
    return source.startswith(TBA_SCOREBREAKDOWN_SOURCE)

def _match_stage(
    comp_level: str | None,
    match_number: int | None,
    max_qm_match_number: int | None,
) -> str:
    token = str(comp_level or "").strip().lower()
    if token in {"f", "sf", "qf", "ef", "pf", "cmpm", "m"}:
        return "elims"
    if token == "qm":  # nosec B105
        if (
            isinstance(match_number, int)
            and match_number > 0
            and isinstance(max_qm_match_number, int)
            and max_qm_match_number >= 6
        ):
            late_start = max(1, int(math.ceil(max_qm_match_number * 0.67)))
            if match_number >= late_start:
                return "late_quals"
        return "early_quals"
    return "early_quals"

def _finding_source_priority(source: str | None) -> int:
    token = str(source or "").strip().lower()
    if token.startswith("video"):
        return 3
    if token.startswith(TBA_SCOREBREAKDOWN_SOURCE):
        return 1
    if token:
        return 2
    return 0

def _dedupe_findings_by_match(
    findings_newest_first: list[models.TeamMatchFinding],
    match_time_by_key: dict[str, int | None],
) -> list[models.TeamMatchFinding]:
    selected: dict[str, models.TeamMatchFinding] = {}
    for finding in findings_newest_first:
        match_key = str(finding.match_key or "")
        if not match_key:
            continue
        current = selected.get(match_key)
        if current is None:
            selected[match_key] = finding
            continue
        candidate_priority = _finding_source_priority(finding.source)
        current_priority = _finding_source_priority(current.source)
        if candidate_priority > current_priority:
            selected[match_key] = finding
            continue
        if candidate_priority < current_priority:
            continue
        if int(finding.id or 0) > int(current.id or 0):
            selected[match_key] = finding

    return sorted(
        selected.values(),
        key=lambda finding: (
            int(match_time_by_key.get(finding.match_key) or -1),
            int(finding.id or 0),
        ),
        reverse=True,
    )

# ── Event metadata helpers ────────────────────────────────────────────
def _event_meta_number(event: models.MatchEvent, key: str) -> float | None:
    meta = event.meta if isinstance(event.meta, dict) else {}
    value = meta.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None

def _is_active_hub_attempt_event(event: models.MatchEvent) -> bool:
    if str(getattr(event, "event_type", "") or "").strip().lower() != "teleop_fuel_score_attempt":
        return False
    meta = getattr(event, "meta", None)
    meta_payload = meta if isinstance(meta, dict) else {}
    hub_active_window = meta_payload.get("hub_active_window")
    if isinstance(hub_active_window, bool):
        return bool(hub_active_window)
    # Backward-compat for older rows that do not have hub activity tagging.
    return True

# ── Percentile / linear model helpers ─────────────────────────────────
def _percentile_map(
    raw_by_key: dict[str, float | None],
    *,
    higher_is_better: bool = True,
    default: float = 50.0,
) -> dict[str, float]:
    valid = [
        (key, float(value))
        for key, value in raw_by_key.items()
        if value is not None and isinstance(value, (int, float)) and not math.isnan(float(value))
    ]
    if not valid:
        return {key: default for key in raw_by_key}

    valid.sort(key=lambda item: item[1])
    result = {key: default for key in raw_by_key}
    n = len(valid)
    for index, (key, _) in enumerate(valid):
        pct = 50.0 if n == 1 else (index / (n - 1)) * 100.0
        if not higher_is_better:
            pct = 100.0 - pct
        result[key] = _clamp(pct, 0.0, 100.0)
    return result

def _fit_linear_model(x_values: list[float], y_values: list[float]) -> tuple[float, float]:
    if len(x_values) != len(y_values) or not x_values:
        return 0.0, 0.0
    if len(x_values) == 1:
        return float(y_values[0]), 0.0

    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    denom = sum((x - x_mean) ** 2 for x in x_values)
    if denom <= 1e-9:
        return y_mean, 0.0

    numer = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
    slope = numer / denom
    intercept = y_mean - (slope * x_mean)
    return intercept, slope

# ── Clip / evidence extraction ────────────────────────────────────────
def _extract_clip_url(summary: dict[str, Any] | None) -> str | None:
    if not isinstance(summary, dict):
        return None
    sampling = summary.get("sampling")
    if isinstance(sampling, dict):
        previews = sampling.get("sample_preview_urls")
        if isinstance(previews, list):
            for item in previews:
                if isinstance(item, str) and item:
                    return item
    video = summary.get("video")
    if isinstance(video, dict):
        local_url = video.get("local_video_url")
        if isinstance(local_url, str) and local_url:
            return local_url
    return None

def _evidence_for_metric(
    findings: list[models.TeamMatchFinding],
    metric_name: str,
    descending: bool = True,
    limit: int = 3,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, models.TeamMatchFinding]] = []
    for finding in findings:
        value = getattr(finding, metric_name, None)
        if value is None or not isinstance(value, (int, float)):
            continue
        scored.append((float(value), finding))
    scored.sort(key=lambda row: row[0], reverse=descending)

    evidence: list[dict[str, Any]] = []
    for value, finding in scored[:limit]:
        evidence.append(
            {
                "match_key": finding.match_key,
                "metric": metric_name,
                "value": round(value, 4),
                "clip_url": _extract_clip_url(finding.summary),
            }
        )
    return evidence

def _extract_first_number(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None
