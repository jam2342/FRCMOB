# Signal construction and pros/cons generation for the rating model.

from __future__ import annotations

import re
from typing import Any

from app.db import models
from app.services.utils import _clamp

from app.services.ratings.helpers import _evidence_for_metric

def _infer_signal_metric(
    metric: str | None,
    evidence: list[dict[str, Any]],
    label: str,
) -> str:
    if isinstance(metric, str) and metric.strip():
        return metric.strip()
    for entry in evidence:
        candidate = entry.get("metric") if isinstance(entry, dict) else None
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    lowered = label.lower()
    if "auto" in lowered:
        return "auto_contribution"
    if "endgame" in lowered or "tower" in lowered or "climb" in lowered:
        return "climb_success_prob"
    if "defense" in lowered:
        return "defensive_engagement_sec"
    if "penalty" in lowered or "discipline" in lowered:
        return "penalty_points_per_match"
    if "trend" in lowered:
        return "trend_delta"
    return "composite_signal"

def _apply_sparse_rating_guard(
    *,
    raw_final_rating: float,
    confidence: float,
    matches_observed: int,
    video_findings_count: int,
    use_fallback_model: bool,
) -> tuple[float, dict[str, Any]]:
    from app.services.ratings.constants import (
        SPARSE_RATING_MAX_RAW_LOW_CONFIDENCE,
        SPARSE_RATING_MAX_RAW_LOW_MATCH,
        SPARSE_RATING_MAX_RAW_MEDIUM_MATCH,
        SPARSE_RATING_MAX_RAW_NO_VIDEO,
        SPARSE_RATING_MIN_RAW,
    )

    lower = SPARSE_RATING_MIN_RAW
    if use_fallback_model or video_findings_count <= 0:
        upper = SPARSE_RATING_MAX_RAW_NO_VIDEO
    elif matches_observed < 3:
        upper = SPARSE_RATING_MAX_RAW_LOW_MATCH
    elif matches_observed < 5:
        upper = SPARSE_RATING_MAX_RAW_MEDIUM_MATCH
    else:
        upper = 100.0
    if confidence < 0.35:
        upper = min(upper, SPARSE_RATING_MAX_RAW_LOW_CONFIDENCE)
    guarded = _clamp(float(raw_final_rating), float(lower), float(upper))
    return guarded, {
        "lower_bound_raw": round(float(lower), 4),
        "upper_bound_raw": round(float(upper), 4),
        "applied": abs(float(guarded) - float(raw_final_rating)) > 1e-6,
        "raw_before_guard": round(float(raw_final_rating), 4),
        "raw_after_guard": round(float(guarded), 4),
        "fallback_model_active": bool(use_fallback_model),
        "matches_observed": int(matches_observed),
        "video_findings_count": int(video_findings_count),
        "confidence_0_1": round(_clamp(float(confidence), 0.0, 1.0), 4),
    }

def _make_signal(
    label: str,
    metric_value: float,
    percentile: float,
    evidence: list[dict[str, Any]],
    *,
    metric: str | None = None,
    baseline_value: float | None = None,
    sample_size: int | None = None,
    category: str | None = None,
    rationale: str | None = None,
    impact: str | None = None,
    trend_delta: float | None = None,
    signal_confidence: float | None = None,
) -> dict[str, Any]:
    signal_metric = _infer_signal_metric(metric, evidence, label)
    computed_delta = (
        float(trend_delta)
        if isinstance(trend_delta, (int, float))
        else (
            float(metric_value) - float(baseline_value)
            if isinstance(baseline_value, (int, float))
            else float(percentile) - 50.0
        )
    )
    inferred_sample_size = (
        max(0, int(sample_size))
        if isinstance(sample_size, int)
        else max(0, len([entry for entry in evidence if isinstance(entry, dict)]))
    )
    inferred_confidence = (
        _clamp(float(signal_confidence), 0.0, 1.0)
        if isinstance(signal_confidence, (int, float))
        else _clamp(
            (0.35 * _clamp(abs(float(computed_delta)) / 50.0, 0.0, 1.0))
            + (0.65 * _clamp(float(inferred_sample_size) / 6.0, 0.0, 1.0)),
            0.0,
            1.0,
        )
    )
    rule_id = re.sub(r"[^a-z0-9]+", "_", str(label).strip().lower()).strip("_") or "signal"
    payload: dict[str, Any] = {
        "rule_id": rule_id,
        "label": label,
        "metric": signal_metric,
        "metric_value": round(metric_value, 4),
        "percentile": round(percentile, 2),
        "delta": round(float(computed_delta), 4),
        "sample_size": int(inferred_sample_size),
        "confidence_0_1": round(float(inferred_confidence), 4),
        "signal_confidence_0_1": round(float(inferred_confidence), 4),
        "evidence_count": int(len(evidence)),
        "evidence": evidence,
    }
    if category:
        payload["category"] = category
    if rationale:
        payload["rationale"] = rationale
    if impact:
        payload["impact"] = impact
    if isinstance(trend_delta, (int, float)):
        payload["trend_delta"] = round(float(trend_delta), 4)
    return payload

def _dedupe_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_labels: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        label = str(signal.get("label") or "").strip().lower()
        if not label or label in seen_labels:
            continue
        seen_labels.add(label)
        deduped.append(signal)
    return deduped

def _ensure_minimum_pros_cons_signals(
    *,
    pros: list[dict[str, Any]],
    cons: list[dict[str, Any]],
    confidence: float,
    findings: list[models.TeamMatchFinding],
    use_fallback_model: bool,
    throughput: float,
    auto_contribution_score: float,
    endgame: float,
    defense_presence_score: float,
    anti_defense_score: float,
    penalty_discipline: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    min_target = 2
    if len(pros) >= min_target and len(cons) >= min_target:
        return pros, cons

    model_hint = (
        "External fallback model signal (no analyzed clips yet)."
        if use_fallback_model
        else "Video-derived baseline signal."
    )
    seen_labels = {
        str(item.get("label") or "").strip().lower()
        for item in pros + cons
        if isinstance(item, dict)
    }

    pro_candidates: list[dict[str, Any]] = [
        {
            "label": "Autonomous floor is serviceable",
            "score": auto_contribution_score,
            "metric": "auto_contribution",
            "descending": True,
            "category": "baseline",
            "impact": "upside",
            "rationale": f"Auto contribution percentile is holding above the median. {model_hint}",
        },
        {
            "label": "Teleop baseline is stable",
            "score": throughput,
            "metric": "fuel_scoring_rate",
            "descending": True,
            "category": "baseline",
            "impact": "upside",
            "rationale": f"Throughput percentile indicates repeatable teleop scoring. {model_hint}",
        },
        {
            "label": "Endgame baseline is viable",
            "score": endgame,
            "metric": "climb_success_prob",
            "descending": True,
            "category": "baseline",
            "impact": "upside",
            "rationale": f"Endgame percentile supports reliable closing points. {model_hint}",
        },
        {
            "label": "Defense utility baseline",
            "score": defense_presence_score,
            "metric": "defensive_engagement_sec",
            "descending": True,
            "category": "baseline",
            "impact": "upside",
            "rationale": f"Defense presence percentile supports situational two-way value. {model_hint}",
        },
        {
            "label": "Anti-defense baseline",
            "score": anti_defense_score,
            "metric": "fuel_scoring_rate",
            "descending": True,
            "category": "resilience",
            "impact": "upside",
            "rationale": f"Anti-defense percentile suggests manageable output drop under pressure. {model_hint}",
        },
        {
            "label": "Clean-play baseline",
            "score": penalty_discipline,
            "metric": "reliability_score",
            "descending": True,
            "category": "discipline",
            "impact": "risk_down",
            "rationale": f"Penalty-discipline percentile supports cleaner match outcomes. {model_hint}",
        },
    ]
    cons_candidates: list[dict[str, Any]] = [
        {
            "label": "Autonomous floor needs work",
            "score": auto_contribution_score,
            "metric": "auto_contribution",
            "descending": False,
            "category": "baseline",
            "impact": "risk_up",
            "rationale": f"Auto contribution percentile is currently below the event median. {model_hint}",
        },
        {
            "label": "Teleop baseline is limited",
            "score": throughput,
            "metric": "fuel_scoring_rate",
            "descending": False,
            "category": "baseline",
            "impact": "risk_up",
            "rationale": f"Throughput percentile is limiting offensive floor. {model_hint}",
        },
        {
            "label": "Endgame baseline is limited",
            "score": endgame,
            "metric": "climb_success_prob",
            "descending": False,
            "category": "baseline",
            "impact": "risk_up",
            "rationale": f"Endgame percentile is below average in current sample. {model_hint}",
        },
        {
            "label": "Defense utility is limited",
            "score": defense_presence_score,
            "metric": "defensive_engagement_sec",
            "descending": False,
            "category": "baseline",
            "impact": "risk_up",
            "rationale": f"Defense presence percentile is low for counter-play value. {model_hint}",
        },
        {
            "label": "Anti-defense risk profile",
            "score": anti_defense_score,
            "metric": "cycle_time_sec",
            "descending": True,
            "category": "resilience",
            "impact": "risk_up",
            "rationale": f"Anti-defense percentile indicates vulnerability under pressure. {model_hint}",
        },
        {
            "label": "Discipline risk baseline",
            "score": penalty_discipline,
            "metric": "reliability_score",
            "descending": False,
            "category": "discipline",
            "impact": "risk_up",
            "rationale": f"Penalty-discipline percentile can pressure match outcomes. {model_hint}",
        },
    ]

    def _append_candidate(
        target: list[dict[str, Any]],
        candidate: dict[str, Any],
        *,
        is_pro: bool,
    ) -> bool:
        label_key = str(candidate.get("label") or "").strip().lower()
        if not label_key or label_key in seen_labels:
            return False
        score = _clamp(float(candidate.get("score") or 0.0), 0.0, 100.0)
        signal = _make_signal(
            str(candidate.get("label") or ""),
            score,
            score,
            _evidence_for_metric(
                findings,
                str(candidate.get("metric") or "fuel_scoring_rate"),
                descending=bool(candidate.get("descending", True)),
            ),
            category=str(candidate.get("category") or "baseline"),
            rationale=str(candidate.get("rationale") or ""),
            impact=str(candidate.get("impact") or ("upside" if is_pro else "risk_up")),
            signal_confidence=confidence,
        )
        target.append(signal)
        seen_labels.add(label_key)
        return True

    if len(pros) < min_target:
        ranked = sorted(pro_candidates, key=lambda item: float(item.get("score") or 0.0), reverse=True)
        for candidate in ranked:
            if len(pros) >= min_target:
                break
            score = float(candidate.get("score") or 0.0)
            if score < 58.0 and len(pros) > 0:
                continue
            _append_candidate(pros, candidate, is_pro=True)
        while len(pros) < min_target and ranked:
            if not _append_candidate(pros, ranked.pop(0), is_pro=True):
                continue

    if len(cons) < min_target:
        ranked = sorted(cons_candidates, key=lambda item: float(item.get("score") or 100.0))
        for candidate in ranked:
            if len(cons) >= min_target:
                break
            score = float(candidate.get("score") or 100.0)
            if score > 42.0 and len(cons) > 0:
                continue
            _append_candidate(cons, candidate, is_pro=False)
        while len(cons) < min_target and ranked:
            if not _append_candidate(cons, ranked.pop(0), is_pro=False):
                continue

    return pros, cons
