from typing import Any

from app.core.config import settings
from app.services.utils import _clamp


def _normalize_thresholds(
    min_coverage_score: float | None,
    min_detections: int | None,
) -> tuple[float, int]:
    coverage = (
        float(min_coverage_score)
        if min_coverage_score is not None
        else float(settings.analysis_quality_min_coverage_score)
    )
    detections = (
        int(min_detections)
        if min_detections is not None
        else int(settings.analysis_quality_min_detections)
    )
    return (_clamp(coverage, 0.0, 1.0), max(0, detections))


def evaluate_summary_quality_gate(
    summary: dict[str, Any] | None,
    *,
    min_coverage_score: float | None = None,
    min_detections: int | None = None,
) -> tuple[bool, str, float | None, int | None]:
    coverage_threshold, detections_threshold = _normalize_thresholds(
        min_coverage_score,
        min_detections,
    )
    safe_summary = summary if isinstance(summary, dict) else {}

    throughput_metrics = safe_summary.get("throughput_metrics")
    throughput_metrics = throughput_metrics if isinstance(throughput_metrics, dict) else {}
    sampling = safe_summary.get("sampling")
    sampling = sampling if isinstance(sampling, dict) else {}

    coverage_value = throughput_metrics.get("coverage_score")
    coverage_score = float(coverage_value) if isinstance(coverage_value, (int, float)) else None
    detection_value = sampling.get("detections")
    detections = int(detection_value) if isinstance(detection_value, (int, float)) else None

    if coverage_score is not None and coverage_score < coverage_threshold:
        return False, "coverage_score_below_threshold", coverage_score, detections
    if detections is not None and detections < detections_threshold:
        return False, "detections_below_threshold", coverage_score, detections

    return True, "accepted", coverage_score, detections


def quality_gate_config_payload() -> dict[str, Any]:
    return {
        "enabled": bool(settings.analysis_quality_gate_enabled),
        "min_coverage_score": _clamp(float(settings.analysis_quality_min_coverage_score), 0.0, 1.0),
        "min_detections": max(0, int(settings.analysis_quality_min_detections)),
    }
