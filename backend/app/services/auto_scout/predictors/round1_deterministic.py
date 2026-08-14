from __future__ import annotations

from typing import Any

from app.services.auto_scout.predictors.init import PredictorContext, PredictorResult, register
from app.services.game_config import load_game_config
from app.services.ratings.constants import PENALTY_EVENT_POINT_WEIGHTS


AUTO_SCORE_SUCCESS_EVENT_TYPES = {"auto_fuel_score_success"}
AUTO_SCORE_ATTEMPT_EVENT_TYPES = {"auto_fuel_score_attempt"}
TELEOP_SCORE_SUCCESS_EVENT_TYPES = {"teleop_fuel_score_success"}
CLIMB_SUCCESS_EVENT_TYPES = {"climb_success"}
CLIMB_ATTEMPT_EVENT_TYPES = {"climb_attempt"}
PENALTY_EVENT_TYPES = {str(key) for key in PENALTY_EVENT_POINT_WEIGHTS.keys()}


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


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _match_total_sec() -> float:
    try:
        return float(load_game_config().phases.total_sec)
    except Exception:
        return 160.0


def _match_auto_sec() -> float:
    try:
        return float(load_game_config().phases.auto_sec)
    except Exception:
        return 20.0


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


def _matches_type(event_type: str, accepted: set[str]) -> bool:
    return str(event_type or "").strip().lower() in accepted


def _events_for_types(
    context: PredictorContext,
    *,
    accepted: set[str],
    before_or_at_sec: float | None = None,
    after_sec: float | None = None,
) -> list:
    rows = []
    for event in context.events:
        if not _matches_type(str(event.event_type or ""), accepted):
            continue
        event_time = float(event.time_sec)
        if before_or_at_sec is not None and event_time > float(before_or_at_sec):
            continue
        if after_sec is not None and event_time <= float(after_sec):
            continue
        rows.append(event)
    return rows


def _event_count(event) -> int:
    count_from_meta = _safe_float((event.meta or {}).get("count_estimate"))
    if count_from_meta is not None and count_from_meta > 0:
        return max(1, int(round(float(count_from_meta))))
    return 1


def _event_evidence(
    events: list,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    return [
        {
            "type": "match_event",
            "ref_id": f"match_event:{event.id}",
            "t_sec": float(event.time_sec),
            "meta": {
                "event_type": event.event_type,
                "confidence": _round(event.confidence, 4),
            },
        }
        for event in events[:limit]
    ]


def _inference_evidence(context: PredictorContext, reason: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "analysis_inference",
            "ref_id": f"analysis_run:{_analysis_run_id(context)}",
            "t_sec": 0.0,
            "meta": {
                "reason": reason,
                "track_coverage_0_1": _round(context.coverage, 4),
            },
        }
    ]


def _auto_success_and_attempt_counts(context: PredictorContext) -> tuple[int, int, list, list]:
    auto_sec = _match_auto_sec()
    success_events = _events_for_types(
        context,
        accepted=AUTO_SCORE_SUCCESS_EVENT_TYPES,
        before_or_at_sec=auto_sec,
    )
    attempt_events = _events_for_types(
        context,
        accepted=AUTO_SCORE_ATTEMPT_EVENT_TYPES,
        before_or_at_sec=auto_sec,
    )
    success_count = sum(_event_count(event) for event in success_events)
    attempt_count = sum(_event_count(event) for event in attempt_events)
    return success_count, attempt_count, success_events, attempt_events


@register("auto_scored")
def predict_auto_scored(context: PredictorContext) -> PredictorResult:
    success_count, _, success_events, _ = _auto_success_and_attempt_counts(context)
    confidence = _round(min(0.95, 0.6 + (0.35 * context.coverage)), 4) or 0.0
    has_signal = bool(success_events)
    if has_signal or context.coverage >= 0.65:
        return PredictorResult(
            value=int(max(0, success_count)),
            confidence=confidence,
            provenance="auto",
            evidence_refs=_event_evidence(success_events) if success_events else _inference_evidence(
                context,
                "No explicit auto scoring events detected; inferred zero under adequate coverage.",
            ),
        )
    return PredictorResult(value=None, confidence=0.0, provenance="blank", evidence_refs=[])


@register("auto_missed")
def predict_auto_missed(context: PredictorContext) -> PredictorResult:
    success_count, attempt_count, success_events, attempt_events = _auto_success_and_attempt_counts(context)
    missed_count = int(max(0, attempt_count - success_count))
    confidence = _round(min(0.95, 0.58 + (0.35 * context.coverage)), 4) or 0.0
    has_signal = bool(success_events or attempt_events)
    if has_signal or context.coverage >= 0.65:
        evidence = _event_evidence(attempt_events + success_events)
        return PredictorResult(
            value=missed_count,
            confidence=confidence,
            provenance="auto",
            evidence_refs=evidence if evidence else _inference_evidence(
                context,
                "No explicit auto attempts detected; inferred zero misses under adequate coverage.",
            ),
        )
    return PredictorResult(value=None, confidence=0.0, provenance="blank", evidence_refs=[])


@register("teleop_scored")
def predict_teleop_scored(context: PredictorContext) -> PredictorResult:
    auto_scored_count, _, _, _ = _auto_success_and_attempt_counts(context)
    throughput_total = (
        int(context.throughput.balls_shot_total)
        if context.throughput is not None and context.throughput.balls_shot_total is not None
        else None
    )
    if throughput_total is not None:
        value = int(max(0, throughput_total - auto_scored_count))
        coverage_signal = (
            float(context.throughput_coverage)
            if context.throughput_coverage is not None
            else float(context.coverage)
        )
        confidence = _round(_clamp(coverage_signal), 4) or 0.0
        evidence_refs: list[dict[str, Any]] = []
        if context.throughput is not None:
            evidence_refs.append(
                {
                    "type": "team_match_throughput",
                    "ref_id": f"team_match_throughput:{context.throughput.finding_id}",
                    "t_sec": 0.0,
                    "meta": {
                        "balls_shot_total": int(context.throughput.balls_shot_total or 0),
                        "coverage_score": _round(context.throughput_coverage, 4),
                    },
                }
            )
        return PredictorResult(
            value=value,
            confidence=confidence,
            provenance="auto",
            evidence_refs=evidence_refs,
        )

    teleop_success_events = _events_for_types(
        context,
        accepted=TELEOP_SCORE_SUCCESS_EVENT_TYPES,
        after_sec=_match_auto_sec(),
    )
    if teleop_success_events or context.coverage >= 0.7:
        value = int(max(0, sum(_event_count(event) for event in teleop_success_events)))
        confidence = _round(min(0.9, 0.52 + (0.35 * context.coverage)), 4) or 0.0
        return PredictorResult(
            value=value,
            confidence=confidence,
            provenance="auto",
            evidence_refs=_event_evidence(teleop_success_events) if teleop_success_events else _inference_evidence(
                context,
                "No throughput row available; inferred teleop scored from event detections.",
            ),
        )

    return PredictorResult(value=None, confidence=0.0, provenance="blank", evidence_refs=[])


@register("teleop_cycles")
def predict_teleop_cycles(context: PredictorContext) -> PredictorResult:
    cycle_time_sec = _safe_float(getattr(context.finding, "cycle_time_sec", None))
    if cycle_time_sec is None or cycle_time_sec <= 0:
        return PredictorResult(value=None, confidence=0.0, provenance="blank", evidence_refs=[])

    teleop_window = max(1.0, _match_total_sec() - _match_auto_sec())
    est_cycles = int(round(teleop_window / max(1.0, cycle_time_sec)))
    coverage_signal = float(context.throughput_coverage) if context.throughput_coverage is not None else float(context.coverage)
    confidence = _round(_clamp(0.45 + (0.45 * coverage_signal), 0.0, 0.96), 4) or 0.0
    evidence_refs = [
        {
            "type": "team_match_finding",
            "ref_id": f"team_match_finding:{context.finding.id}",
            "t_sec": 0.0,
            "meta": {
                "cycle_time_sec": _round(cycle_time_sec, 3),
                "teleop_window_sec": _round(teleop_window, 3),
            },
        }
    ]
    if context.throughput is not None:
        evidence_refs.append(
            {
                "type": "team_match_throughput",
                "ref_id": f"team_match_throughput:{context.throughput.finding_id}",
                "t_sec": 0.0,
                "meta": {
                    "coverage_score": _round(context.throughput_coverage, 4),
                },
            }
        )
    return PredictorResult(
        value=max(0, est_cycles),
        confidence=confidence,
        provenance="auto",
        evidence_refs=evidence_refs,
    )


@register("foul_count")
def predict_foul_count(context: PredictorContext) -> PredictorResult:
    penalty_events = _events_for_types(context, accepted=PENALTY_EVENT_TYPES)
    confidence = _round(
        min(0.95, 0.58 + (0.32 * context.coverage) + (0.08 if penalty_events else 0.0)),
        4,
    ) or 0.0
    if penalty_events or context.coverage >= 0.55:
        return PredictorResult(
            value=int(len(penalty_events)),
            confidence=confidence,
            provenance="auto",
            evidence_refs=_event_evidence(penalty_events) if penalty_events else _inference_evidence(
                context,
                "No penalty events detected under moderate coverage.",
            ),
        )
    return PredictorResult(value=None, confidence=0.0, provenance="blank", evidence_refs=[])


@register("endgame_mode")
def predict_endgame_mode(context: PredictorContext) -> PredictorResult:
    climb_success_events = _events_for_types(context, accepted=CLIMB_SUCCESS_EVENT_TYPES)
    climb_attempt_events = _events_for_types(context, accepted=CLIMB_ATTEMPT_EVENT_TYPES)
    climb_success_prob = _safe_float(getattr(context.finding, "climb_success_prob", None))

    if climb_success_events or (climb_success_prob is not None and climb_success_prob >= 0.82):
        mode = "climb_level_3"
    elif climb_success_prob is not None and climb_success_prob >= 0.62:
        mode = "climb_level_2"
    elif climb_success_prob is not None and climb_success_prob >= 0.42:
        mode = "climb_level_1"
    elif climb_attempt_events or (climb_success_prob is not None and climb_success_prob >= 0.20):
        mode = "parked"
    else:
        teleop_volume = (
            int(context.throughput.balls_shot_total or 0)
            if context.throughput is not None and context.throughput.balls_shot_total is not None
            else 0
        )
        mode = "kept_scoring" if teleop_volume > 0 else "none"

    has_signal = bool(climb_success_events or climb_attempt_events or climb_success_prob is not None)
    if not has_signal and context.coverage < 0.6:
        return PredictorResult(value=None, confidence=0.0, provenance="blank", evidence_refs=[])

    coverage_signal = float(context.throughput_coverage) if context.throughput_coverage is not None else float(context.coverage)
    confidence = _round(
        min(
            0.97,
            0.48
            + (0.26 * context.coverage)
            + (0.18 * coverage_signal)
            + (0.1 if climb_success_events else 0.0)
            + (0.06 if climb_attempt_events else 0.0)
            + (0.05 if climb_success_prob is not None else 0.0),
        ),
        4,
    ) or 0.0
    evidence_refs = _event_evidence(climb_success_events + climb_attempt_events, limit=6)
    if context.finding is not None:
        evidence_refs.append(
            {
                "type": "team_match_finding",
                "ref_id": f"team_match_finding:{context.finding.id}",
                "t_sec": 0.0,
                "meta": {
                    "climb_success_prob": _round(climb_success_prob, 3),
                },
            }
        )
    if not evidence_refs:
        evidence_refs = _inference_evidence(
            context,
            "No explicit climb events detected; endgame mode inferred from available summary signals.",
        )

    return PredictorResult(
        value=mode,
        confidence=confidence,
        provenance="auto",
        evidence_refs=evidence_refs,
    )
