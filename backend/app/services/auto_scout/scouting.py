from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import logging
from statistics import mean, median
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db import models
from app.services.auto_scout.predictors import PREDICTORS, PredictorContext
from app.services.auto_scout.specs import (
    AUTO_SCOUT_DERIVED_INSIGHT_FIELDS_BY_SEASON,
    AUTO_SCOUT_FIELD_PRIORS_BY_SEASON,
    AUTO_SCOUT_FORM_FIELDS,
    AUTO_SCOUT_MIN_TRACK_COVERAGE_BY_SEASON,
    AUTO_SCOUT_V1_FORM_FIELDS_BY_SEASON,
    mapper_version_for_season,
    season_support_payload,
)
from app.services.events.pipeline import (
    ANALYSIS_VERSION,
    _enqueue_analysis_job,
    _latest_matching_run,
    _resolve_event_perimeter_type,
    get_queue,
)
from app.services.game_config import load_game_config
from app.services.jobs import build_analysis_params_hash


READY_STATUSES = {"ready", "low_confidence"}
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_key(raw: object) -> str:
    return str(raw or "").strip().lower()


def _normalize_profile(raw: object) -> str:
    return " ".join(str(raw or "").strip().split())[:120]


def _safe_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _supported_form_fields(season_year: int) -> tuple[str, ...]:
    return AUTO_SCOUT_V1_FORM_FIELDS_BY_SEASON.get(int(season_year), ())


def _supported_derived_fields(season_year: int) -> tuple[str, ...]:
    return AUTO_SCOUT_DERIVED_INSIGHT_FIELDS_BY_SEASON.get(int(season_year), ())


def _field_priors(season_year: int) -> dict[str, float]:
    return AUTO_SCOUT_FIELD_PRIORS_BY_SEASON.get(int(season_year), {})


def _min_track_coverage(season_year: int) -> float:
    return float(AUTO_SCOUT_MIN_TRACK_COVERAGE_BY_SEASON.get(int(season_year), 0.35))


def _draft_support_payload(season_year: int) -> dict[str, Any]:
    return season_support_payload(int(season_year))


def _normalize_draft_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    form_patch_raw = raw.get("form_patch")
    notes_seed = str(raw.get("notes_seed") or "").strip()
    derived_raw = raw.get("derived_insights")
    form_patch = {
        str(key): value
        for key, value in (form_patch_raw.items() if isinstance(form_patch_raw, dict) else [])
        if str(key) in AUTO_SCOUT_FORM_FIELDS
    }
    derived = {
        str(key): value
        for key, value in (derived_raw.items() if isinstance(derived_raw, dict) else [])
    }
    return {
        "form_patch": form_patch,
        "notes_seed": notes_seed,
        "derived_insights": derived,
    }


def _form_patch(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    form_patch = payload.get("form_patch")
    if not isinstance(form_patch, dict):
        return {}
    return {str(key): value for key, value in form_patch.items()}


def _percentile(sorted_values: list[float], ratio: float) -> float | None:
    if not sorted_values:
        return None
    clamped_ratio = _clamp(float(ratio), 0.0, 1.0)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    index = int(round((len(sorted_values) - 1) * clamped_ratio))
    return float(sorted_values[max(0, min(index, len(sorted_values) - 1))])


def _event_year_map(db: Session, event_keys: set[str]) -> dict[str, int]:
    if not event_keys:
        return {}
    rows = (
        db.query(models.Event.event_key, models.Event.year)
        .filter(models.Event.event_key.in_(sorted(event_keys)))
        .all()
    )
    output: dict[str, int] = {}
    for raw_event_key, raw_year in rows:
        key = _normalize_key(raw_event_key)
        if not key:
            continue
        try:
            output[key] = int(raw_year)
        except (TypeError, ValueError):
            continue
    return output


def _field_override_diff(auto_payload: dict[str, Any], edited_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    auto_form = (
        auto_payload.get("form_patch")
        if isinstance(auto_payload.get("form_patch"), dict)
        else {}
    )
    edited_form = (
        edited_payload.get("form_patch")
        if isinstance(edited_payload.get("form_patch"), dict)
        else {}
    )
    overrides: dict[str, dict[str, Any]] = {}
    for field_name, auto_value in auto_form.items():
        edited_value = edited_form.get(field_name, auto_value)
        if edited_value != auto_value:
            overrides[str(field_name)] = {
                "from": auto_value,
                "to": edited_value,
            }
    return overrides


def _serialize_draft(row: models.AutoScoutDraft, *, season_year: int | None = None) -> dict[str, Any]:
    resolved_season_year = int(season_year or 0) if season_year is not None else None
    read_only = False
    read_only_reason: str | None = None
    if resolved_season_year is not None:
        current_mapper = mapper_version_for_season(resolved_season_year)
        if row.mapper_version != current_mapper:
            read_only = True
            read_only_reason = f"Draft was produced by mapper {row.mapper_version}, current mapper is {current_mapper}."
    return {
        "id": row.id,
        "event_key": row.event_key,
        "match_key": row.match_key,
        "team_key": row.team_key,
        "status": row.status,
        "draft_payload": row.draft_payload or {},
        "approved_payload": row.approved_payload or {},
        "field_confidence": row.field_confidence or {},
        "field_provenance": row.field_provenance or {},
        "field_evidence_refs": row.field_evidence_refs or {},
        "field_overrides": row.field_overrides or {},
        "coverage_summary": row.coverage_summary or {},
        "missing_reasons": row.missing_reasons or [],
        "analysis_version": row.analysis_version,
        "analysis_run_id": row.analysis_run_id,
        "mapper_version": row.mapper_version,
        "draft_version": row.draft_version,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "approved_by": row.approved_by,
        "rejected_at": row.rejected_at.isoformat() if row.rejected_at else None,
        "rejected_reason": row.rejected_reason,
        "superseded_at": row.superseded_at.isoformat() if row.superseded_at else None,
        "read_only": read_only,
        "read_only_reason": read_only_reason,
    }


def _latest_completed_run_for_match(
    db: Session,
    *,
    match_key: str,
) -> tuple[models.AnalysisRun, models.AnalysisRunContext | None] | None:
    row = (
        db.query(models.AnalysisRun, models.AnalysisRunContext)
        .outerjoin(models.AnalysisRunContext, models.AnalysisRunContext.run_id == models.AnalysisRun.id)
        .filter(
            models.AnalysisRun.match_key == match_key,
            models.AnalysisRun.status == "completed",
        )
        .order_by(models.AnalysisRun.created_at.desc(), models.AnalysisRun.id.desc())
        .first()
    )
    return row


def _latest_non_superseded_draft(
    db: Session,
    *,
    event_key: str,
    match_key: str,
    team_key: str,
) -> models.AutoScoutDraft | None:
    return (
        db.query(models.AutoScoutDraft)
        .filter(
            models.AutoScoutDraft.event_key == event_key,
            models.AutoScoutDraft.match_key == match_key,
            models.AutoScoutDraft.team_key == team_key,
            models.AutoScoutDraft.superseded_at.is_(None),
        )
        .order_by(models.AutoScoutDraft.generated_at.desc(), models.AutoScoutDraft.id.desc())
        .first()
    )


def _supersede_other_drafts(
    db: Session,
    *,
    event_key: str,
    match_key: str,
    team_key: str,
    keep_id: int | None,
) -> None:
    now = _utc_now()
    rows = (
        db.query(models.AutoScoutDraft)
        .filter(
            models.AutoScoutDraft.event_key == event_key,
            models.AutoScoutDraft.match_key == match_key,
            models.AutoScoutDraft.team_key == team_key,
            models.AutoScoutDraft.superseded_at.is_(None),
            models.AutoScoutDraft.status.notin_(["approved", "rejected", "superseded"]),
        )
        .all()
    )
    for row in rows:
        if keep_id is not None and row.id == keep_id:
            continue
        row.status = "superseded"
        row.superseded_at = now
        row.updated_at = now


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


def _approx_track_coverage(tracks: list[models.RobotTrack]) -> tuple[float, float]:
    if not tracks:
        return 0.0, 0.0
    total_sec = max(_match_total_sec(), 1.0)
    min_time = min(float(track.time_sec) for track in tracks)
    max_time = max(float(track.time_sec) for track in tracks)
    tracked_sec = max(0.0, max_time - min_time)
    coverage = _clamp(tracked_sec / total_sec, 0.0, 1.0)
    return coverage, tracked_sec


def _zone_dwell_breakdown(tracks: list[models.RobotTrack]) -> dict[str, float]:
    if len(tracks) < 2:
        return {}
    rows = sorted(tracks, key=lambda row: (float(row.time_sec), int(row.id)))
    zone_time: dict[str, float] = defaultdict(float)
    for index in range(1, len(rows)):
        prev = rows[index - 1]
        curr = rows[index]
        if not prev.zone_key:
            continue
        delta = max(0.0, min(1.0, float(curr.time_sec) - float(prev.time_sec)))
        zone_time[str(prev.zone_key)] += delta
    return {
        key: round(value, 3)
        for key, value in sorted(zone_time.items(), key=lambda item: item[1], reverse=True)
        if value > 0
    }


def _estimate_disabled_period(
    tracks: list[models.RobotTrack],
    *,
    coverage: float,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], float]:
    if len(tracks) < 2:
        return None, [], 0.0
    rows = sorted(tracks, key=lambda row: (float(row.time_sec), int(row.id)))
    threshold_speed = 0.18
    min_window_sec = 4.0
    windows: list[dict[str, Any]] = []
    window_start: float | None = None
    last_time: float | None = None
    for row in rows:
        speed = _safe_float(row.speed_mps)
        row_time = float(row.time_sec)
        slow = speed is not None and speed <= threshold_speed
        if slow and window_start is None:
            window_start = row_time
        if not slow and window_start is not None and last_time is not None:
            duration = max(0.0, last_time - window_start)
            if duration >= min_window_sec:
                windows.append(
                    {
                        "start_sec": round(window_start, 3),
                        "end_sec": round(last_time, 3),
                        "seconds": round(duration, 3),
                    }
                )
            window_start = None
        last_time = row_time
    if window_start is not None and last_time is not None:
        duration = max(0.0, last_time - window_start)
        if duration >= min_window_sec:
            windows.append(
                {
                    "start_sec": round(window_start, 3),
                    "end_sec": round(last_time, 3),
                    "seconds": round(duration, 3),
                }
            )
    if not windows:
        return {"seconds": 0.0}, [], _round(0.7 * coverage, 4) or 0.0
    total_seconds = round(sum(float(window["seconds"]) for window in windows), 3)
    evidence = [
        {
            "type": "robot_track_window",
            "ref_id": f"track-window-{index}",
            "t_sec": float(window["start_sec"]),
            "meta": window,
        }
        for index, window in enumerate(windows, start=1)
    ]
    confidence = _round(min(0.96, (0.52 + (0.35 * coverage) + min(total_seconds / 18.0, 0.12))), 4) or 0.0
    return {"seconds": total_seconds, "windows": windows}, evidence, confidence


def _build_notes_seed(derived_insights: dict[str, Any]) -> str:
    lines: list[str] = []
    cycle = derived_insights.get("cycle_pace_summary")
    if isinstance(cycle, dict) and cycle.get("cycle_time_sec") is not None:
        lines.append(f"Estimated cycle time: {cycle['cycle_time_sec']}s.")
    disabled = derived_insights.get("disabled_period")
    if isinstance(disabled, dict) and _safe_float(disabled.get("seconds")) and float(disabled["seconds"]) > 0:
        lines.append(f"Possible disabled/stalled period: {disabled['seconds']}s.")
    if derived_insights.get("defensive_engagement_presence") is True:
        lines.append("Video analysis detected defensive engagement.")
    return " ".join(lines).strip()


def _build_ready_payload(
    *,
    db: Session,
    season_year: int,
    team_key: str,
    run: models.AnalysisRun,
    run_context: models.AnalysisRunContext | None,
    finding: models.TeamMatchFinding | None,
    throughput: models.TeamMatchThroughput | None,
    quality: models.AnalysisQuality | None,
    events: list[models.MatchEvent],
    tracks: list[models.RobotTrack],
) -> tuple[str, dict[str, Any], dict[str, float], dict[str, str], dict[str, list[dict[str, Any]]], dict[str, Any], list[str]]:
    support = _draft_support_payload(season_year)
    if not support["supported"]:
        return (
            "failed",
            {"form_patch": {}, "notes_seed": "", "derived_insights": {}},
            {},
            {},
            {},
            {"supported": False},
            ["game_year_unsupported"],
        )

    supported_form_fields = tuple(_supported_form_fields(season_year))
    supported_derived_fields = set(_supported_derived_fields(season_year))
    priors = _field_priors(season_year)
    coverage, tracked_sec = _approx_track_coverage(tracks)
    throughput_coverage = (
        _safe_float((throughput.metric_coverage or {}).get("coverage_score"))
        if throughput is not None and isinstance(throughput.metric_coverage, dict)
        else None
    )
    tracking_quality = quality.tracking_quality_score if quality is not None else None
    overall_quality = quality.overall_quality_score if quality is not None else None
    calibration_ok = bool(run_context is not None and run_context.calibration_id is not None)
    coverage_summary = {
        "video_sec": _round(_match_total_sec(), 3),
        "tracked_sec": _round(tracked_sec, 3),
        "track_coverage_0_1": _round(coverage, 4),
        "throughput_coverage_0_1": _round(throughput_coverage, 4),
        "tracking_quality_score": _round(tracking_quality, 4),
        "overall_quality_score": _round(overall_quality, 4),
        "calibration_ok": calibration_ok,
        "track_rows": len(tracks),
        "event_rows": len(events),
    }

    field_provenance = {field: "needs_review" for field in AUTO_SCOUT_FORM_FIELDS}
    field_confidence = {field: 0.0 for field in AUTO_SCOUT_FORM_FIELDS}
    field_evidence_refs: dict[str, list[dict[str, Any]]] = {field: [] for field in AUTO_SCOUT_FORM_FIELDS}
    draft_payload: dict[str, Any] = {
        "form_patch": {},
        "notes_seed": "",
        "derived_insights": {},
    }
    missing_reasons: list[str] = []

    for insight_key in supported_derived_fields:
        field_provenance[insight_key] = "blank"
        field_confidence[insight_key] = 0.0
        field_evidence_refs[insight_key] = []

    if coverage < _min_track_coverage(season_year):
        missing_reasons.append("low_track_coverage")

    predictor_context = PredictorContext(
        db=db,
        season_year=season_year,
        event_key=(
            str(run_context.event_key or "").strip().lower()
            if run_context is not None and getattr(run_context, "event_key", None)
            else None
        ),
        match_key=str(run.match_key or "").strip().lower() if getattr(run, "match_key", None) else None,
        team_key=str(team_key or "").strip().lower() if team_key else None,
        analysis_run_id=int(run.id),
        coverage=coverage,
        throughput_coverage=throughput_coverage,
        events=events,
        tracks=tracks,
        finding=finding,
        throughput=throughput,
        quality=quality,
    )
    for field_name in supported_form_fields:
        predictor = PREDICTORS.get(field_name)
        if predictor is None:
            continue
        prediction = predictor(predictor_context)
        if prediction is None:
            continue
        field_provenance[field_name] = str(prediction.provenance or "needs_review")
        field_confidence[field_name] = _round(_clamp(float(prediction.confidence or 0.0)), 4) or 0.0
        field_evidence_refs[field_name] = (
            prediction.evidence_refs
            if isinstance(prediction.evidence_refs, list)
            else []
        )
        if field_provenance[field_name] == "auto" and prediction.value is not None:
            draft_payload["form_patch"][field_name] = prediction.value

    disabled_period, disabled_evidence, disabled_confidence = _estimate_disabled_period(tracks, coverage=coverage)
    if "disabled_period" in supported_derived_fields:
        if disabled_period is not None:
            draft_payload["derived_insights"]["disabled_period"] = disabled_period
            field_provenance["disabled_period"] = "auto"
            field_confidence["disabled_period"] = disabled_confidence
            field_evidence_refs["disabled_period"] = disabled_evidence

    defensive_events = [event for event in events if str(event.event_type) == "protected_zone_interference"]
    defensive_seconds = _safe_float(getattr(finding, "defensive_engagement_sec", None))
    if "defensive_engagement_presence" in supported_derived_fields:
        if defensive_seconds is not None:
            presence = bool(defensive_seconds >= 4.0 or defensive_events)
            defensive_confidence = _round(
                min(
                    0.95,
                    (priors.get("defensive_engagement_presence", 0.8) * max(coverage, 0.45))
                    + (0.12 if presence else 0.0)
                    + (0.08 if defensive_events else 0.0),
                ),
                4,
            ) or 0.0
            draft_payload["derived_insights"]["defensive_engagement_presence"] = presence
            field_provenance["defensive_engagement_presence"] = "auto"
            field_confidence["defensive_engagement_presence"] = defensive_confidence
            field_evidence_refs["defensive_engagement_presence"] = [
                {
                    "type": "match_event",
                    "ref_id": f"match_event:{event.id}",
                    "t_sec": float(event.time_sec),
                    "meta": {
                        "event_type": event.event_type,
                        "confidence": _round(event.confidence, 4),
                    },
                }
                for event in defensive_events[:8]
            ] or [
                {
                    "type": "team_match_finding",
                    "ref_id": f"team_match_finding:{finding.id}",
                    "t_sec": 0.0,
                    "meta": {"defensive_engagement_sec": _round(defensive_seconds, 3)},
                }
            ]

    if "cycle_pace_summary" in supported_derived_fields and finding is not None:
        cycle_time_sec = _safe_float(getattr(finding, "cycle_time_sec", None))
        if cycle_time_sec is not None:
            total_window_sec = max(1.0, float(_match_total_sec() - _match_auto_sec()))
            est_cycles = round(total_window_sec / max(1.0, cycle_time_sec), 2)
            coverage_hint = throughput_coverage if throughput_coverage is not None else coverage
            cycle_confidence = _round(
                min(
                    0.93,
                    (priors.get("cycle_pace_summary", 0.78) * max(float(coverage_hint), 0.45))
                    + (0.12 if throughput is not None else 0.0),
                ),
                4,
            ) or 0.0
            draft_payload["derived_insights"]["cycle_pace_summary"] = {
                "cycle_time_sec": _round(cycle_time_sec, 3),
                "est_cycles": est_cycles,
                "balls_shot_total": int(getattr(throughput, "balls_shot_total", 0) or 0) if throughput is not None else None,
            }
            field_provenance["cycle_pace_summary"] = "auto"
            field_confidence["cycle_pace_summary"] = cycle_confidence
            field_evidence_refs["cycle_pace_summary"] = [
                {
                    "type": "team_match_finding",
                    "ref_id": f"team_match_finding:{finding.id}",
                    "t_sec": 0.0,
                    "meta": {
                        "cycle_time_sec": _round(cycle_time_sec, 3),
                        "auto_contribution": _round(getattr(finding, "auto_contribution", None), 3),
                    },
                }
            ]
            if throughput is not None:
                field_evidence_refs["cycle_pace_summary"].append(
                    {
                        "type": "team_match_throughput",
                        "ref_id": f"team_match_throughput:{throughput.finding_id}",
                        "t_sec": 0.0,
                        "meta": throughput.metric_coverage or {},
                    }
                )

    zone_dwell = _zone_dwell_breakdown(tracks)
    if "zone_dwell_breakdown" in supported_derived_fields:
        if zone_dwell:
            draft_payload["derived_insights"]["zone_dwell_breakdown"] = zone_dwell
            field_provenance["zone_dwell_breakdown"] = "auto"
            field_confidence["zone_dwell_breakdown"] = _round(
                min(0.99, priors.get("zone_dwell_breakdown", 0.98) * max(coverage, 0.5)),
                4,
            ) or 0.0
            field_evidence_refs["zone_dwell_breakdown"] = [
                {
                    "type": "robot_track_zone",
                    "ref_id": f"zone:{zone_key}",
                    "t_sec": 0.0,
                    "meta": {"zone_key": zone_key, "duration_sec": duration_sec},
                }
                for zone_key, duration_sec in zone_dwell.items()
            ]

    draft_payload["notes_seed"] = _build_notes_seed(draft_payload["derived_insights"])

    status = "ready"
    if missing_reasons or any(
        field_provenance.get(key) == "auto" and float(field_confidence.get(key) or 0.0) < 0.72
        for key in list(supported_form_fields) + list(supported_derived_fields)
    ):
        status = "low_confidence"

    return (
        status,
        _normalize_draft_payload(draft_payload),
        field_confidence,
        field_provenance,
        field_evidence_refs,
        coverage_summary,
        missing_reasons,
    )


def _queue_analysis_if_possible(db: Session, *, match: models.Match) -> tuple[bool, str | None]:
    calibration = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key == match.match_key)
        .one_or_none()
    )
    if calibration is None:
        return False, "no_calibration"
    video = (
        db.query(models.MatchVideo)
        .filter(models.MatchVideo.match_key == match.match_key)
        .order_by(models.MatchVideo.id.asc())
        .first()
    )
    if video is None or not str(video.url or "").strip():
        return False, "no_video"

    perimeter_type, _ = _resolve_event_perimeter_type(db, match.event_key)
    params_hash = build_analysis_params_hash(
        analysis_version=ANALYSIS_VERSION,
        calibration_id=calibration.id,
        perimeter_type=perimeter_type,
    )
    existing = _latest_matching_run(
        db,
        match_key=match.match_key,
        analysis_version=ANALYSIS_VERSION,
        params_hash=params_hash,
        calibration_id=calibration.id,
    )
    if existing is not None:
        run, _ = existing
        return (str(run.status or "").lower() in {"queued", "running", "completed"}), None

    job, _, _ = _enqueue_analysis_job(
        get_queue(),
        match_key=match.match_key,
        analysis_version=ANALYSIS_VERSION,
        params_hash=params_hash,
        calibration_id=calibration.id,
    )
    return (job is not None), None


def _pending_or_failed_draft(
    db: Session,
    *,
    event_key: str,
    match_key: str,
    team_key: str,
    mapper_version: str,
) -> models.AutoScoutDraft | None:
    return (
        db.query(models.AutoScoutDraft)
        .filter(
            models.AutoScoutDraft.event_key == event_key,
            models.AutoScoutDraft.match_key == match_key,
            models.AutoScoutDraft.team_key == team_key,
            models.AutoScoutDraft.mapper_version == mapper_version,
            models.AutoScoutDraft.analysis_run_id.is_(None),
            models.AutoScoutDraft.superseded_at.is_(None),
            models.AutoScoutDraft.status.in_(["pending", "generating", "failed"]),
        )
        .order_by(models.AutoScoutDraft.generated_at.desc(), models.AutoScoutDraft.id.desc())
        .first()
    )


def get_auto_scout_draft(
    db: Session,
    *,
    event_key: str,
    match_key: str,
    team_key: str,
) -> models.AutoScoutDraft | None:
    return _latest_non_superseded_draft(
        db,
        event_key=_normalize_key(event_key),
        match_key=_normalize_key(match_key),
        team_key=_normalize_key(team_key),
    )


def list_event_auto_scout_drafts(
    db: Session,
    *,
    event_key: str,
) -> list[models.AutoScoutDraft]:
    rows = (
        db.query(models.AutoScoutDraft)
        .filter(
            models.AutoScoutDraft.event_key == _normalize_key(event_key),
            models.AutoScoutDraft.superseded_at.is_(None),
        )
        .order_by(
            models.AutoScoutDraft.match_key.asc(),
            models.AutoScoutDraft.team_key.asc(),
            models.AutoScoutDraft.generated_at.desc(),
            models.AutoScoutDraft.id.desc(),
        )
        .all()
    )
    latest_by_slot: dict[tuple[str, str], models.AutoScoutDraft] = {}
    for row in rows:
        key = (row.match_key, row.team_key)
        if key not in latest_by_slot:
            latest_by_slot[key] = row
    return list(latest_by_slot.values())


def get_auto_scout_approval_telemetry(
    db: Session,
    *,
    event_key: str | None = None,
    season_year: int | None = None,
    mapper_version: str | None = None,
    max_rows: int = 2500,
) -> dict[str, Any]:
    normalized_event_key = _normalize_key(event_key) if event_key is not None else None
    mapper_token = str(mapper_version or "").strip()
    bounded_limit = max(100, min(int(max_rows or 2500), 10000))

    query = db.query(models.AutoScoutDraft).filter(models.AutoScoutDraft.status == "approved")
    if normalized_event_key:
        query = query.filter(models.AutoScoutDraft.event_key == normalized_event_key)
    if mapper_token:
        query = query.filter(models.AutoScoutDraft.mapper_version == mapper_token)
    if isinstance(season_year, int):
        query = (
            query.join(models.Event, models.Event.event_key == models.AutoScoutDraft.event_key)
            .filter(models.Event.year == int(season_year))
        )

    rows = (
        query.order_by(
            models.AutoScoutDraft.approved_at.desc().nullslast(),
            models.AutoScoutDraft.id.desc(),
        )
        .limit(bounded_limit)
        .all()
    )
    if not rows:
        return {
            "approved_draft_count": 0,
            "window_rows": 0,
            "filters": {
                "event_key": normalized_event_key,
                "season_year": int(season_year) if isinstance(season_year, int) else None,
                "mapper_version": mapper_token or None,
                "max_rows": bounded_limit,
            },
            "override_rate_by_field": {},
            "override_rate_when_present_by_field": {},
            "time_to_approve_sec": {
                "avg": None,
                "p50": None,
                "p90": None,
                "count": 0,
            },
            "confidence_calibration_by_field": {},
        }

    event_years = _event_year_map(
        db,
        {
            _normalize_key(row.event_key)
            for row in rows
            if _normalize_key(row.event_key)
        },
    )
    fields_by_year: dict[int, tuple[str, ...]] = {
        year: _supported_form_fields(year)
        for year in set(event_years.values())
    }
    all_fields = sorted(
        {
            field
            for year_fields in fields_by_year.values()
            for field in year_fields
        }
    )
    if not all_fields:
        all_fields = sorted(AUTO_SCOUT_FORM_FIELDS)

    override_counts: dict[str, int] = {field: 0 for field in all_fields}
    present_counts: dict[str, int] = {field: 0 for field in all_fields}
    calibration: dict[str, dict[str, dict[str, float]]] = {}
    approval_durations_sec: list[float] = []

    for row in rows:
        row_event_key = _normalize_key(row.event_key)
        row_year = event_years.get(row_event_key)
        row_fields = (
            set(fields_by_year.get(row_year, ()))
            if row_year is not None
            else set(all_fields)
        )
        if not row_fields:
            continue

        auto_form = _form_patch(row.draft_payload)
        approved_form = _form_patch(row.approved_payload)
        overrides = row.field_overrides if isinstance(row.field_overrides, dict) else {}
        provenance = row.field_provenance if isinstance(row.field_provenance, dict) else {}
        confidence = row.field_confidence if isinstance(row.field_confidence, dict) else {}

        if isinstance(row.approved_at, datetime) and isinstance(row.generated_at, datetime):
            delta_sec = max(0.0, (row.approved_at - row.generated_at).total_seconds())
            approval_durations_sec.append(delta_sec)

        for field_name in row_fields:
            if field_name not in override_counts:
                override_counts[field_name] = 0
                present_counts[field_name] = 0
            if field_name in auto_form:
                present_counts[field_name] = int(present_counts.get(field_name) or 0) + 1
            if field_name in overrides:
                override_counts[field_name] = int(override_counts.get(field_name) or 0) + 1

            if str(provenance.get(field_name) or "") != "auto":
                continue
            if field_name not in auto_form:
                continue
            confidence_value = _safe_float(confidence.get(field_name))
            if confidence_value is None:
                continue
            bucket = f"{_clamp(float(confidence_value), 0.0, 1.0):.1f}"
            field_bins = calibration.setdefault(field_name, {})
            bin_row = field_bins.setdefault(
                bucket,
                {"count": 0.0, "accepted_sum": 0.0, "confidence_sum": 0.0},
            )
            auto_value = auto_form.get(field_name)
            approved_value = approved_form.get(field_name, auto_value)
            accepted = 1.0 if approved_value == auto_value else 0.0
            bin_row["count"] = float(bin_row["count"]) + 1.0
            bin_row["accepted_sum"] = float(bin_row["accepted_sum"]) + accepted
            bin_row["confidence_sum"] = float(bin_row["confidence_sum"]) + _clamp(
                float(confidence_value), 0.0, 1.0
            )

    approved_count = len(rows)
    override_rate_by_field = {
        field: _round(float(override_counts.get(field) or 0) / float(max(1, approved_count)), 4)
        for field in sorted(override_counts)
    }
    override_rate_when_present_by_field = {
        field: _round(float(override_counts.get(field) or 0) / float(max(1, present_counts.get(field) or 0)), 4)
        for field in sorted(override_counts)
    }

    sorted_durations = sorted(float(value) for value in approval_durations_sec)
    avg_duration = (
        float(sum(sorted_durations) / float(len(sorted_durations)))
        if sorted_durations
        else None
    )
    confidence_calibration_by_field: dict[str, list[dict[str, Any]]] = {}
    for field_name, bins in calibration.items():
        bucket_rows: list[dict[str, Any]] = []
        for bucket in sorted(bins, key=lambda raw: float(raw)):
            row = bins[bucket]
            count = int(row.get("count") or 0)
            if count <= 0:
                continue
            bucket_rows.append(
                {
                    "bucket": bucket,
                    "samples": count,
                    "avg_confidence_0_1": _round(float(row["confidence_sum"]) / float(count), 4),
                    "approval_rate_0_1": _round(float(row["accepted_sum"]) / float(count), 4),
                }
            )
        confidence_calibration_by_field[field_name] = bucket_rows

    return {
        "approved_draft_count": approved_count,
        "window_rows": approved_count,
        "filters": {
            "event_key": normalized_event_key,
            "season_year": int(season_year) if isinstance(season_year, int) else None,
            "mapper_version": mapper_token or None,
            "max_rows": bounded_limit,
        },
        "override_rate_by_field": override_rate_by_field,
        "override_rate_when_present_by_field": override_rate_when_present_by_field,
        "time_to_approve_sec": {
            "avg": _round(avg_duration, 3),
            "p50": _round(_percentile(sorted_durations, 0.5), 3),
            "p90": _round(_percentile(sorted_durations, 0.9), 3),
            "count": len(sorted_durations),
        },
        "confidence_calibration_by_field": confidence_calibration_by_field,
    }


# Confidence-bucket boundaries: a "high-confidence" prediction is one we currently
# treat as ready (>= 0.72 maps to the 0.7 bucket and up); "low-confidence" is below.
_HIGH_CONFIDENCE_BUCKET_FLOOR = 0.7
_LOW_CONFIDENCE_BUCKET_CEIL = 0.6


def summarize_auto_scout_confidence_calibration(
    db: Session,
    *,
    event_key: str | None = None,
    season_year: int | None = None,
    mapper_version: str | None = None,
    max_rows: int = 2500,
    min_samples: int = 8,
) -> dict[str, Any]:
    # Make calibration visible before retuning any confidence formula. Reads the same
    # approval telemetry humans generate by approving/overriding drafts and reports,
    # per field, where our confidence is lying.
    telemetry = get_auto_scout_approval_telemetry(
        db,
        event_key=event_key,
        season_year=season_year,
        mapper_version=mapper_version,
        max_rows=max_rows,
    )
    calibration_by_field = telemetry.get("confidence_calibration_by_field") or {}
    override_rate_by_field = telemetry.get("override_rate_by_field") or {}
    override_rate_when_present = telemetry.get("override_rate_when_present_by_field") or {}

    fields: dict[str, dict[str, Any]] = {}
    for field_name in sorted(calibration_by_field):
        buckets = calibration_by_field.get(field_name) or []
        total_samples = sum(int(bucket.get("samples") or 0) for bucket in buckets)

        high_samples = 0
        high_accepted = 0.0
        low_samples = 0
        low_accepted = 0.0
        worst_bucket: dict[str, Any] | None = None
        for bucket in buckets:
            samples = int(bucket.get("samples") or 0)
            if samples <= 0:
                continue
            bucket_floor = _safe_float(bucket.get("bucket"))
            approval_rate = _safe_float(bucket.get("approval_rate_0_1"))
            if bucket_floor is None or approval_rate is None:
                continue
            accepted = approval_rate * samples
            if bucket_floor >= _HIGH_CONFIDENCE_BUCKET_FLOOR:
                high_samples += samples
                high_accepted += accepted
            elif bucket_floor < _LOW_CONFIDENCE_BUCKET_CEIL:
                low_samples += samples
                low_accepted += accepted
            if worst_bucket is None or approval_rate < float(worst_bucket["approval_rate_0_1"]):
                worst_bucket = bucket

        high_conf_override_rate = (
            _round(1.0 - (high_accepted / float(high_samples)), 4) if high_samples > 0 else None
        )
        low_conf_approval_rate = (
            _round(low_accepted / float(low_samples), 4) if low_samples > 0 else None
        )

        if total_samples < int(min_samples):
            recommendation = "insufficient_data"
        elif high_conf_override_rate is not None and high_conf_override_rate >= 0.30:
            # Humans keep overriding picks we labelled high-confidence -> we're overconfident.
            recommendation = "lower_confidence"
        elif (
            low_conf_approval_rate is not None
            and low_samples >= max(4, int(min_samples) // 2)
            and low_conf_approval_rate >= 0.80
        ):
            # Our low-confidence picks are usually accepted -> threshold may be too strict.
            recommendation = "eligible_for_threshold_raise"
        else:
            recommendation = "watch"

        fields[field_name] = {
            "samples": total_samples,
            "override_rate_0_1": override_rate_by_field.get(field_name),
            "override_rate_when_present_0_1": override_rate_when_present.get(field_name),
            "high_confidence_samples": high_samples,
            "high_confidence_override_rate_0_1": high_conf_override_rate,
            "low_confidence_samples": low_samples,
            "low_confidence_approval_rate_0_1": low_conf_approval_rate,
            "worst_confidence_bucket": worst_bucket,
            "recommendation": recommendation,
        }

    return {
        "approved_draft_count": telemetry.get("approved_draft_count", 0),
        "filters": telemetry.get("filters", {}),
        "min_samples": int(min_samples),
        "fields": fields,
    }


def _aggregate_profile_field(
    values: list[Any],
    confidences: list[float],
    recent_n: int,
) -> dict[str, Any]:
    # Aggregate one field's machine-filled values across a team's matches. Shape
    # depends on the value type so the UI can render booleans, levels/counts, and
    # categoricals (e.g. endgame_mode) differently. Values are chronological.
    n = len(values)
    avg_conf = _round(mean(confidences), 4) if confidences else None

    if all(isinstance(value, bool) for value in values):
        true_count = sum(1 for value in values if value)
        return {
            "type": "rate",
            "samples": n,
            "true_rate_0_1": _round(true_count / float(n), 4),
            "avg_confidence_0_1": avg_conf,
            "last": values[-1],
        }

    numeric = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if len(numeric) == n and n > 0:
        return {
            "type": "numeric",
            "samples": n,
            "median": _round(median(numeric), 2),
            "mean": _round(mean(numeric), 2),
            "min": _round(min(numeric), 2),
            "max": _round(max(numeric), 2),
            "last": _round(numeric[-1], 2),
            "trend": [_round(value, 2) for value in numeric[-recent_n:]],
            "avg_confidence_0_1": avg_conf,
        }

    distribution: dict[str, int] = {}
    for value in values:
        token = str(value)
        distribution[token] = distribution.get(token, 0) + 1
    mode = max(distribution.items(), key=lambda item: item[1])[0]
    return {
        "type": "categorical",
        "samples": n,
        "mode": mode,
        "distribution": distribution,
        "last": str(values[-1]),
        "avg_confidence_0_1": avg_conf,
    }


def summarize_team_auto_scout_profile(
    db: Session,
    *,
    team_key: str,
    event_key: str | None = None,
    season_year: int | None = None,
    recent_n: int = 5,
) -> dict[str, Any]:
    # Read-only per-team rollup of the video-derived auto-scout fields for the Team
    # Center "robot info" view. Aggregates the team's settled (ready/low_confidence/
    # approved) non-superseded drafts; only auto-filled fields are included. Falls
    # back event -> season -> prior season so a profile still shows early in an event.
    normalized_team_key = _normalize_key(team_key)
    normalized_event_key = _normalize_key(event_key) if event_key else None

    def _query(scope_event_key: str | None, scope_year: int | None) -> list[models.AutoScoutDraft]:
        query = db.query(models.AutoScoutDraft).filter(
            models.AutoScoutDraft.team_key == normalized_team_key,
            models.AutoScoutDraft.superseded_at.is_(None),
            models.AutoScoutDraft.status.in_(["ready", "low_confidence", "approved"]),
        )
        if scope_event_key:
            query = query.filter(models.AutoScoutDraft.event_key == scope_event_key)
        if isinstance(scope_year, int):
            query = query.join(
                models.Event, models.Event.event_key == models.AutoScoutDraft.event_key
            ).filter(models.Event.year == int(scope_year))
        return query.order_by(
            models.AutoScoutDraft.generated_at.asc(), models.AutoScoutDraft.id.asc()
        ).all()

    is_last_season = False
    scope_event_key = normalized_event_key
    scope_year = season_year
    rows = _query(normalized_event_key, season_year)
    if not rows and normalized_event_key and isinstance(season_year, int):
        rows = _query(None, season_year)  # widen from event to whole season
        scope_event_key = None
    if not rows and isinstance(season_year, int):
        rows = _query(None, season_year - 1)  # last-season fallback (disclaim in UI)
        if rows:
            is_last_season = True
            scope_event_key = None
            scope_year = season_year - 1

    base = {
        "team_key": normalized_team_key,
        "scope": {"event_key": scope_event_key, "season_year": scope_year},
        "is_last_season": is_last_season,
        "sample_matches": 0,
        "fields": {},
        "generated_at": _utc_now().isoformat(),
    }
    if not rows:
        base["available"] = False
        return base

    event_years = _event_year_map(
        db, {_normalize_key(row.event_key) for row in rows if _normalize_key(row.event_key)}
    )
    supported_fields: set[str] = set()
    for year in set(event_years.values()):
        supported_fields |= set(_supported_form_fields(year))
    if not supported_fields:
        supported_fields = set(AUTO_SCOUT_FORM_FIELDS)

    values_by_field: dict[str, list[Any]] = defaultdict(list)
    confidence_by_field: dict[str, list[float]] = defaultdict(list)
    match_keys: set[str] = set()
    for row in rows:
        match_keys.add(str(row.match_key))
        form = _form_patch(row.draft_payload)
        provenance = row.field_provenance if isinstance(row.field_provenance, dict) else {}
        confidence = row.field_confidence if isinstance(row.field_confidence, dict) else {}
        for field_name in supported_fields:
            if str(provenance.get(field_name) or "") != "auto":
                continue
            if field_name not in form:
                continue
            value = form.get(field_name)
            if value is None:
                continue
            values_by_field[field_name].append(value)
            confidence_value = _safe_float(confidence.get(field_name))
            if confidence_value is not None:
                confidence_by_field[field_name].append(confidence_value)

    fields = {
        field_name: _aggregate_profile_field(
            values_by_field[field_name], confidence_by_field[field_name], recent_n
        )
        for field_name in sorted(values_by_field)
    }

    base["available"] = bool(fields)
    base["sample_matches"] = len(match_keys)
    base["fields"] = fields
    return base


def generate_auto_scout_draft(
    db: Session,
    *,
    event_key: str,
    match_key: str,
    team_key: str,
    force_regenerate: bool = False,
) -> tuple[models.AutoScoutDraft, bool]:
    normalized_event_key = _normalize_key(event_key)
    normalized_match_key = _normalize_key(match_key)
    normalized_team_key = _normalize_key(team_key)
    match = db.get(models.Match, normalized_match_key)
    team = db.get(models.Team, normalized_team_key)
    event = db.get(models.Event, normalized_event_key)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found.")
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found.")
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found.")
    match_team = (
        db.query(models.MatchTeam)
        .filter(
            models.MatchTeam.match_key == normalized_match_key,
            models.MatchTeam.team_key == normalized_team_key,
        )
        .first()
    )
    if match_team is None:
        raise HTTPException(status_code=400, detail="Selected team is not assigned to the selected match.")

    season_year = int(event.year)
    mapper_version = mapper_version_for_season(season_year)
    support = _draft_support_payload(season_year)
    if not support["supported"]:
        existing_failed = _pending_or_failed_draft(
            db,
            event_key=normalized_event_key,
            match_key=normalized_match_key,
            team_key=normalized_team_key,
            mapper_version=mapper_version,
        )
        row = existing_failed or models.AutoScoutDraft(
            event_key=normalized_event_key,
            match_key=normalized_match_key,
            team_key=normalized_team_key,
            mapper_version=mapper_version,
        )
        row.status = "failed"
        row.analysis_version = ANALYSIS_VERSION
        row.draft_payload = {"form_patch": {}, "notes_seed": "", "derived_insights": {}}
        row.field_confidence = {}
        row.field_provenance = {}
        row.field_evidence_refs = {}
        row.coverage_summary = {"supported": False}
        row.missing_reasons = ["game_year_unsupported"]
        row.updated_at = _utc_now()
        if existing_failed is None:
            db.add(row)
        db.commit()
        db.refresh(row)
        return row, existing_failed is None

    latest = _latest_non_superseded_draft(
        db,
        event_key=normalized_event_key,
        match_key=normalized_match_key,
        team_key=normalized_team_key,
    )

    latest_current_run = _latest_completed_run_for_match(db, match_key=normalized_match_key)
    current_run = None
    current_context = None
    if latest_current_run is not None:
        current_run, current_context = latest_current_run

    # An approved draft is human-confirmed truth — never overwrite it, not even under
    # force_regenerate. Regeneration applies only to machine-owned (ready/pending) slots.
    if latest is not None and latest.status == "approved":
        return latest, False

    if (
        not force_regenerate
        and latest is not None
        and latest.status in READY_STATUSES | {"approved"}
        and (
            current_run is None
            or latest.analysis_run_id == current_run.id
            or latest.status == "approved"
        )
    ):
        return latest, False

    current_analysis_version = (
        str(current_context.analysis_version or current_run.version)
        if current_run is not None and current_context is not None
        else (str(current_run.version) if current_run is not None else ANALYSIS_VERSION)
    )
    run_stale = current_run is not None and current_analysis_version != ANALYSIS_VERSION

    if current_run is None or run_stale:
        queued, queue_missing_reason = _queue_analysis_if_possible(db, match=match)
        missing_reasons: list[str] = []
        status = "generating"
        if run_stale:
            missing_reasons.append("analysis_stale")
        if queue_missing_reason:
            missing_reasons.append(queue_missing_reason)
            status = "failed"
        elif queued:
            missing_reasons.append("analysis_pending")
        placeholder = _pending_or_failed_draft(
            db,
            event_key=normalized_event_key,
            match_key=normalized_match_key,
            team_key=normalized_team_key,
            mapper_version=mapper_version,
        )
        created = placeholder is None
        row = placeholder or models.AutoScoutDraft(
            event_key=normalized_event_key,
            match_key=normalized_match_key,
            team_key=normalized_team_key,
            mapper_version=mapper_version,
        )
        row.status = status
        row.analysis_version = ANALYSIS_VERSION
        row.analysis_run_id = None
        row.draft_payload = {"form_patch": {}, "notes_seed": "", "derived_insights": {}}
        row.field_confidence = {}
        row.field_provenance = {field: "needs_review" for field in AUTO_SCOUT_FORM_FIELDS}
        row.field_evidence_refs = {}
        row.coverage_summary = {
            "calibration_ok": queue_missing_reason != "no_calibration",
            "queued_analysis": queued,
        }
        row.missing_reasons = missing_reasons
        row.updated_at = _utc_now()
        row.mapper_version = mapper_version
        if created:
            db.add(row)
        db.commit()
        db.refresh(row)
        return row, created

    finding = (
        db.query(models.TeamMatchFinding)
        .filter(
            models.TeamMatchFinding.analysis_run_id == current_run.id,
            models.TeamMatchFinding.team_key == normalized_team_key,
        )
        .first()
    )
    throughput = db.get(models.TeamMatchThroughput, finding.id) if finding is not None else None
    quality = db.get(models.AnalysisQuality, current_run.id)
    events = (
        db.query(models.MatchEvent)
        .filter(
            models.MatchEvent.analysis_run_id == current_run.id,
            models.MatchEvent.team_key == normalized_team_key,
        )
        .order_by(models.MatchEvent.time_sec.asc(), models.MatchEvent.id.asc())
        .all()
    )
    tracks = (
        db.query(models.RobotTrack)
        .filter(
            models.RobotTrack.analysis_run_id == current_run.id,
            models.RobotTrack.team_key == normalized_team_key,
        )
        .order_by(models.RobotTrack.time_sec.asc(), models.RobotTrack.id.asc())
        .all()
    )
    if not tracks:
        any_tracks = (
            db.query(models.RobotTrack)
            .filter(models.RobotTrack.analysis_run_id == current_run.id)
            .first()
        )
        missing_reason = "team_not_resolved" if any_tracks is not None else "low_track_coverage"
        # Reuse an existing draft for this run if one is already on file (idempotency),
        # otherwise finalize a pending placeholder, otherwise create a fresh failed row.
        placeholder = (
            db.query(models.AutoScoutDraft)
            .filter(
                models.AutoScoutDraft.match_key == normalized_match_key,
                models.AutoScoutDraft.team_key == normalized_team_key,
                models.AutoScoutDraft.analysis_run_id == current_run.id,
                models.AutoScoutDraft.mapper_version == mapper_version,
                models.AutoScoutDraft.superseded_at.is_(None),
            )
            .first()
            or _pending_or_failed_draft(
                db,
                event_key=normalized_event_key,
                match_key=normalized_match_key,
                team_key=normalized_team_key,
                mapper_version=mapper_version,
            )
        )
        created = placeholder is None
        row = placeholder or models.AutoScoutDraft(
            event_key=normalized_event_key,
            match_key=normalized_match_key,
            team_key=normalized_team_key,
            mapper_version=mapper_version,
        )
        row.status = "failed"
        row.analysis_version = ANALYSIS_VERSION
        row.analysis_run_id = current_run.id
        row.draft_payload = {"form_patch": {}, "notes_seed": "", "derived_insights": {}}
        row.field_confidence = {}
        row.field_provenance = {field: "needs_review" for field in AUTO_SCOUT_FORM_FIELDS}
        row.field_evidence_refs = {}
        row.coverage_summary = {"track_rows": 0}
        row.missing_reasons = [missing_reason]
        row.updated_at = _utc_now()
        if created:
            db.add(row)
        db.commit()
        db.refresh(row)
        return row, created

    status, draft_payload, field_confidence, field_provenance, field_evidence_refs, coverage_summary, missing_reasons = _build_ready_payload(
        db=db,
        season_year=season_year,
        team_key=normalized_team_key,
        run=current_run,
        run_context=current_context,
        finding=finding,
        throughput=throughput,
        quality=quality,
        events=events,
        tracks=tracks,
    )
    existing = (
        db.query(models.AutoScoutDraft)
        .filter(
            models.AutoScoutDraft.match_key == normalized_match_key,
            models.AutoScoutDraft.team_key == normalized_team_key,
            models.AutoScoutDraft.analysis_run_id == current_run.id,
            models.AutoScoutDraft.mapper_version == mapper_version,
        )
        .first()
    )
    created = existing is None
    row = existing or models.AutoScoutDraft(
        event_key=normalized_event_key,
        match_key=normalized_match_key,
        team_key=normalized_team_key,
        analysis_run_id=current_run.id,
        mapper_version=mapper_version,
    )
    if not created and force_regenerate:
        row.draft_version = int(row.draft_version or 0) + 1
    row.status = status
    row.event_key = normalized_event_key
    row.match_key = normalized_match_key
    row.team_key = normalized_team_key
    row.analysis_run_id = current_run.id
    row.analysis_version = current_analysis_version
    row.mapper_version = mapper_version
    row.draft_payload = draft_payload
    row.field_confidence = field_confidence
    row.field_provenance = field_provenance
    row.field_evidence_refs = field_evidence_refs
    row.coverage_summary = coverage_summary
    row.missing_reasons = missing_reasons
    row.approved_payload = row.approved_payload or {}
    row.updated_at = _utc_now()
    row.superseded_at = None
    if created:
        db.add(row)
        db.flush()
    _supersede_other_drafts(
        db,
        event_key=normalized_event_key,
        match_key=normalized_match_key,
        team_key=normalized_team_key,
        keep_id=row.id,
    )
    db.commit()
    db.refresh(row)
    return row, created


def generate_auto_scout_drafts_for_match(
    db: Session,
    *,
    event_key: str,
    match_key: str,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    # Best-effort batch generator: one draft per assigned team. A single bad team
    # (e.g. tracks don't map -> team_not_resolved) yields a failed draft for that
    # slot without blocking the other five. Only an invalid match context raises.
    normalized_event_key = _normalize_key(event_key)
    normalized_match_key = _normalize_key(match_key)

    match_teams = (
        db.query(models.MatchTeam)
        .filter(models.MatchTeam.match_key == normalized_match_key)
        .order_by(models.MatchTeam.alliance.asc(), models.MatchTeam.station.asc(), models.MatchTeam.team_key.asc())
        .all()
    )

    summary: dict[str, Any] = {
        "event_key": normalized_event_key,
        "match_key": normalized_match_key,
        "team_count": len(match_teams),
        "created_count": 0,
        "ready_count": 0,
        "low_confidence_count": 0,
        "failed_count": 0,
        "skipped_existing_count": 0,
        "errors": [],
        "draft_ids_by_team": {},
    }

    for match_team in match_teams:
        team_key = _normalize_key(match_team.team_key)
        try:
            row, created = generate_auto_scout_draft(
                db,
                event_key=normalized_event_key,
                match_key=normalized_match_key,
                team_key=team_key,
                force_regenerate=force_regenerate,
            )
        except HTTPException as exc:
            db.rollback()
            summary["errors"].append({"team_key": team_key, "error": str(exc.detail)})
            continue
        except Exception as exc:  # noqa: BLE001 - isolate per-team failures
            db.rollback()
            summary["errors"].append({"team_key": team_key, "error": str(exc)})
            continue

        summary["draft_ids_by_team"][team_key] = int(row.id)
        if created:
            summary["created_count"] += 1
        else:
            summary["skipped_existing_count"] += 1
        if row.status == "ready":
            summary["ready_count"] += 1
        elif row.status == "low_confidence":
            summary["low_confidence_count"] += 1
        elif row.status == "failed":
            summary["failed_count"] += 1

    return summary


def approve_auto_scout_draft(
    db: Session,
    *,
    draft_id: int,
    draft_version: int,
    approved_by: str,
    edited_payload: dict[str, Any],
) -> tuple[models.AutoScoutDraft, dict[str, dict[str, Any]]]:
    row = db.get(models.AutoScoutDraft, int(draft_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Auto-scout draft not found.")
    if row.status not in READY_STATUSES:
        raise HTTPException(status_code=409, detail="Only ready drafts can be approved.")
    if int(row.draft_version or 0) != int(draft_version):
        raise HTTPException(status_code=409, detail="Draft version is stale. Refresh and review again.")
    approver = _normalize_profile(approved_by)
    if not approver:
        raise HTTPException(status_code=400, detail="approved_by is required.")

    normalized_payload = _normalize_draft_payload(edited_payload)
    overrides = _field_override_diff(row.draft_payload or {}, normalized_payload)
    row.status = "approved"
    row.approved_at = _utc_now()
    row.approved_by = approver
    row.approved_payload = normalized_payload
    row.field_overrides = overrides
    row.updated_at = _utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    telemetry = get_auto_scout_approval_telemetry(
        db,
        event_key=row.event_key,
        mapper_version=row.mapper_version,
        max_rows=2000,
    )
    logger.info(
        "auto_scout_approval_metrics event=%s mapper=%s approved=%s avg_tta_sec=%s p50_tta_sec=%s "
        "override_rate_auto_mobility=%s override_rate_teleop_scored=%s override_rate_offense_level=%s",
        row.event_key,
        row.mapper_version,
        telemetry.get("approved_draft_count"),
        (telemetry.get("time_to_approve_sec") or {}).get("avg"),
        (telemetry.get("time_to_approve_sec") or {}).get("p50"),
        (telemetry.get("override_rate_by_field") or {}).get("auto_mobility"),
        (telemetry.get("override_rate_by_field") or {}).get("teleop_scored"),
        (telemetry.get("override_rate_by_field") or {}).get("offense_level_1_5"),
    )
    return row, overrides


def reject_auto_scout_draft(
    db: Session,
    *,
    draft_id: int,
    draft_version: int,
    reason: str,
) -> models.AutoScoutDraft:
    row = db.get(models.AutoScoutDraft, int(draft_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Auto-scout draft not found.")
    if row.status not in READY_STATUSES:
        raise HTTPException(status_code=409, detail="Only ready drafts can be rejected.")
    if int(row.draft_version or 0) != int(draft_version):
        raise HTTPException(status_code=409, detail="Draft version is stale. Refresh and review again.")
    detail = str(reason or "").strip()[:240]
    if not detail:
        raise HTTPException(status_code=400, detail="Reject reason is required.")

    row.status = "rejected"
    row.rejected_at = _utc_now()
    row.rejected_reason = detail
    row.updated_at = _utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
