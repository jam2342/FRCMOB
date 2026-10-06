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


