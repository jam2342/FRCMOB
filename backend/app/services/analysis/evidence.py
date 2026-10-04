# Evidence constraints independent of optional statistical quality thresholds.
from __future__ import annotations


def evidence_rejection_reason(summary: dict | None) -> str | None:
    summary = summary or {}
    evidence = summary.get("video_evidence")
    if isinstance(evidence, dict) and not evidence.get("ratings_eligible", False):
        return "video_metrics_not_validated_for_ratings"
    # Quarantine historical video rows that mixed official allocations into CV.
    if str(summary.get("source", "")).startswith("video"):
        throughput = summary.get("throughput_metrics") or {}
        for key in ("cycle_time_source", "score_events_source"):
            source = str(throughput.get(key, "")).lower()
            if source and not source.startswith("cv_"):
                return "mixed_official_and_video_evidence"
    return None


def video_evidence(*, observations: list[dict], sample_interval: float,
                   duration: float, calibration_meta: dict | None,
                   identity_sources: set[str] | None = None) -> dict:
    meta = calibration_meta or {}
    # Count distinct timestamps, never duplicate tracks as additional coverage.
    times = sorted({float(row["time_sec"]) for row in observations})
    covered, end = 0.0, 0.0
    for t in times:
        if 0 <= t < duration:
            stop = min(duration, t + sample_interval)
            covered += max(0.0, stop - max(t, end))
            end = max(end, stop)
    coverage = min(1.0, covered / max(duration, 1e-6))
    geometry_verified = bool(meta.get("floor_validation_passed"))
    reasons = []
    if not observations:
        reasons.append("identity_unresolved_or_not_visible")
    if not geometry_verified:
        reasons.append("floor_calibration_not_independently_validated")
    if coverage < 0.5:
        reasons.append("partial_track_coverage")
    reasons.append("fuel_count_not_observable_from_robot_zone_visits")
    return {
        "schema_version": 1,
        "status": "partial" if observations else "insufficient_evidence",
        # Record how each robot was named: a bumper read, or deduction from bumper
        # colour when the alliance's other two robots were seen at the same moment.
        "identity_source": ("+".join(sorted(identity_sources)) if identity_sources else "bumper_ocr")
                           if observations else "unresolved",
        "track_coverage_0_1": round(coverage, 4),
        "observed_seconds": round(covered, 3),
        "geometry_verified": geometry_verified,
        "ratings_eligible": False,
        "training_eligible": False,
        "missing_reasons": reasons,
        "supported_form_fields": [],
        "measurement_kind": "robot_position_and_zone_visits",
    }
