from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.db import models
from app.services.analysis.pipeline_types import (
    _QualityResult,
    _StageContext,
    _TeamMetricsResult,
    _TrackingResult,
)
from app.services.utils import _clamp

_CALIBRATION_POINTS_TARGET = 8

def _stage_evaluate_quality(
    ctx: _StageContext,
    tr: _TrackingResult,
    tm: _TeamMetricsResult,
    pipeline_stage_ms: dict[str, float],
) -> _QualityResult:
    # Compute quality scores and evaluate the quality gate.
    #
    # Returns a result that tells the caller whether to accept or reject this run.
    # This function does not commit to the database — the caller owns the transaction.
    calibration_point_factor = _clamp(len(ctx.calibration.field_points) / _CALIBRATION_POINTS_TARGET, 0.0, 1.0)
    tracking_density_factor = _clamp(
        len(tr.observations) / max(1.0, len(tr.sample_frames) * 5.0),
        0.0,
        1.0,
    )
    track_count_factor = _clamp(len(tr.track_summaries) / 6.0, 0.0, 1.0)
    tracking_quality_score = _clamp((0.6 * tracking_density_factor) + (0.4 * track_count_factor), 0.0, 1.0)
    identity_quality_score = _clamp(
        len(set(tr.track_team_map.values())) / max(1.0, float(len(ctx.match_teams))),
        0.0,
        1.0,
    )
    calibration_quality_score = _clamp(
        (0.7 * calibration_point_factor) + (0.3 if ctx.calibration.homography else 0.0),
        0.0,
        1.0,
    )
    overall_quality_score = _clamp(
        (0.35 * calibration_quality_score)
        + (0.4 * tracking_quality_score)
        + (0.25 * identity_quality_score),
        0.0,
        1.0,
    )
    coverage_avg = _clamp(
        (sum(tm.team_coverage_scores) / len(tm.team_coverage_scores))
        if tm.team_coverage_scores
        else 0.0,
        0.0,
        1.0,
    )
    coverage_min = _clamp(min(tm.team_coverage_scores), 0.0, 1.0) if tm.team_coverage_scores else None
    detections_min = min(tm.team_detection_counts) if tm.team_detection_counts else None
    quality_gate_enabled = bool(settings.analysis_quality_gate_enabled)
    reject_low_runs_enabled = bool(settings.analysis_quality_reject_low_runs)
    min_overall_threshold = _clamp(float(settings.analysis_quality_min_overall_score), 0.0, 1.0)
    min_coverage_threshold = _clamp(float(settings.analysis_quality_min_coverage_score), 0.0, 1.0)
    min_detections_threshold = max(0, int(settings.analysis_quality_min_detections))
    rejection_reasons: list[str] = []
    if quality_gate_enabled:
        if overall_quality_score < min_overall_threshold:
            rejection_reasons.append("overall_quality_below_threshold")
        if coverage_avg < min_coverage_threshold:
            rejection_reasons.append("average_coverage_below_threshold")
        if detections_min is not None and detections_min < min_detections_threshold:
            rejection_reasons.append("detections_below_threshold")
    reject_low_quality = bool(reject_low_runs_enabled and rejection_reasons)
    ctx.mark_stage("quality_gate_evaluation")

    quality_details: dict[str, Any] = {
        "mapped_team_count": len(set(tr.track_team_map.values())),
        "expected_team_count": len(ctx.match_teams),
        "track_count": len(tr.track_summaries),
        "observation_count": len(tr.observations),
        "sample_frame_count": len(tr.sample_frames),
        "ocr_tracks_with_hints": int(tr.ocr_meta.get("tracks_with_hints") or 0),
        "ocr_reads": int(tr.ocr_meta.get("ocr_reads") or 0),
        "ocr_hits": int(tr.ocr_meta.get("ocr_hits") or 0),
        "ocr_preassigned_tracks": int(tr.assignment_meta.get("ocr_preassigned_track_count") or 0),
        "pipeline_stage_timing_ms": pipeline_stage_ms,
        "coverage_quality": {
            "avg_coverage_score_0_1": round(float(coverage_avg), 4),
            "min_coverage_score_0_1": round(float(coverage_min), 4) if coverage_min is not None else None,
            "min_detections_per_team": int(detections_min) if detections_min is not None else None,
            "team_missing_reason_counts": dict(tm.team_missing_reason_counts),
        },
        "quality_gate": {
            "enabled": quality_gate_enabled,
            "reject_low_runs_enabled": reject_low_runs_enabled,
            "min_overall_score_0_1": round(float(min_overall_threshold), 4),
            "min_coverage_score_0_1": round(float(min_coverage_threshold), 4),
            "min_detections": int(min_detections_threshold),
            "rejected": bool(reject_low_quality),
            "rejection_reasons": rejection_reasons,
        },
    }
    ctx.db.merge(
        models.AnalysisQuality(
            run_id=ctx.run.id,
            match_key=ctx.match_key,
            event_key=ctx.match.event_key,
            calibration_quality_score=round(calibration_quality_score, 4),
            tracking_quality_score=round(tracking_quality_score, 4),
            identity_quality_score=round(identity_quality_score, 4),
            overall_quality_score=round(overall_quality_score, 4),
            details=quality_details,
        )
    )

    return _QualityResult(
        calibration_quality_score=calibration_quality_score,
        tracking_quality_score=tracking_quality_score,
        identity_quality_score=identity_quality_score,
        overall_quality_score=overall_quality_score,
        coverage_avg=coverage_avg,
        coverage_min=coverage_min,
        detections_min=detections_min,
        reject_low_quality=reject_low_quality,
        rejection_reasons=rejection_reasons,
        quality_details=quality_details,
    )
