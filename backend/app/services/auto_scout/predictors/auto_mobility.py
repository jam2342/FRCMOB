from __future__ import annotations

from app.services.auto_scout.predictors.init import PredictorContext, PredictorResult, register
from app.services.auto_scout.specs import AUTO_SCOUT_FIELD_PRIORS_BY_SEASON


def _safe_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _analysis_run_id(context: PredictorContext) -> int:
    if context.events:
        return int(context.events[0].analysis_run_id)
    if context.tracks:
        return int(context.tracks[0].analysis_run_id)
    if context.finding is not None:
        return int(context.finding.analysis_run_id)
    if context.throughput is not None:
        return int(context.throughput.analysis_run_id)
    if context.quality is not None:
        return int(context.quality.run_id)
    return 0


@register("auto_mobility")
def predict_auto_mobility(context: PredictorContext) -> PredictorResult:
    priors = AUTO_SCOUT_FIELD_PRIORS_BY_SEASON.get(int(context.season_year), {})
    auto_events = [event for event in context.events if str(event.event_type) == "auto_mobility"]
    auto_mobility_conf = max([float(event.confidence or 0.0) for event in auto_events] or [0.0])
    mobility_confidence = _round(
        min(
            0.98,
            (priors.get("auto_mobility", 0.85) * max(context.coverage, 0.55))
            + (0.18 if auto_events else 0.0)
            + (0.06 * auto_mobility_conf),
        ),
        4,
    ) or 0.0

    if auto_events or context.coverage >= 0.7:
        evidence_refs = [
            {
                "type": "match_event",
                "ref_id": f"match_event:{event.id}",
                "t_sec": float(event.time_sec),
                "meta": {
                    "event_type": event.event_type,
                    "confidence": _round(event.confidence, 4),
                    "distance_m": _safe_float((event.meta or {}).get("distance_m")),
                },
            }
            for event in auto_events
        ] or [
            {
                "type": "analysis_inference",
                "ref_id": f"analysis_run:{_analysis_run_id(context)}",
                "t_sec": 0.0,
                "meta": {
                    "reason": "No auto_mobility event detected under high track coverage.",
                    "track_coverage_0_1": _round(context.coverage, 4),
                },
            }
        ]
        return PredictorResult(
            value=bool(auto_events),
            confidence=mobility_confidence,
            provenance="auto",
            evidence_refs=evidence_refs,
        )

    return PredictorResult(
        value=None,
        confidence=0.0,
        provenance="blank",
        evidence_refs=[],
    )
