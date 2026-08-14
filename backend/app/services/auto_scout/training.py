from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.auto_scout.ml import (
    AUTO_SCOUT_ROUND2_ML_FIELDS,
    auto_scout_field_scope,
    auto_scout_field_target_to_float,
    build_auto_scout_field_feature_vector,
)
from app.services.game_config import load_game_config


@dataclass(slots=True)
class _DraftContext:
    coverage: float
    throughput_coverage: float | None
    finding: models.TeamMatchFinding | None
    throughput: models.TeamMatchThroughput | None
    quality: models.AnalysisQuality | None
    events: list[models.MatchEvent]
    tracks: list[models.RobotTrack]


def auto_scout_training_source_version() -> str:
    configured = str(getattr(settings, "ml_auto_scout_feature_source_version", "") or "").strip()
    if configured:
        return configured
    return "auto_scout_field_features_v1"


def _safe_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _match_total_sec() -> float:
    try:
        return float(load_game_config().phases.total_sec)
    except Exception:
        return 160.0


def _approx_track_coverage(tracks: list[models.RobotTrack]) -> tuple[float, float]:
    if not tracks:
        return 0.0, 0.0
    total_sec = max(_match_total_sec(), 1.0)
    min_time = min(float(track.time_sec) for track in tracks)
    max_time = max(float(track.time_sec) for track in tracks)
    tracked_sec = max(0.0, max_time - min_time)
    coverage = _clamp(tracked_sec / total_sec, 0.0, 1.0)
    return coverage, tracked_sec


def _load_draft_context(
    db: Session,
    *,
    analysis_run_id: int | None,
    team_key: str,
    cache: dict[tuple[int, str], _DraftContext | None],
) -> _DraftContext | None:
    if analysis_run_id is None:
        return None
    key = (int(analysis_run_id), str(team_key or "").strip().lower())
    if key in cache:
        return cache[key]

    finding = (
        db.query(models.TeamMatchFinding)
        .filter(
            models.TeamMatchFinding.analysis_run_id == int(analysis_run_id),
            models.TeamMatchFinding.team_key == key[1],
        )
        .first()
    )
    throughput = db.get(models.TeamMatchThroughput, finding.id) if finding is not None else None
    quality = db.get(models.AnalysisQuality, int(analysis_run_id))
    events = (
        db.query(models.MatchEvent)
        .filter(
            models.MatchEvent.analysis_run_id == int(analysis_run_id),
            models.MatchEvent.team_key == key[1],
        )
        .order_by(models.MatchEvent.time_sec.asc(), models.MatchEvent.id.asc())
        .all()
    )
    tracks = (
        db.query(models.RobotTrack)
        .filter(
            models.RobotTrack.analysis_run_id == int(analysis_run_id),
            models.RobotTrack.team_key == key[1],
        )
        .order_by(models.RobotTrack.time_sec.asc(), models.RobotTrack.id.asc())
        .all()
    )
    coverage, _tracked_sec = _approx_track_coverage(tracks)
    throughput_coverage = (
        _safe_float((throughput.metric_coverage or {}).get("coverage_score"))
        if throughput is not None and isinstance(throughput.metric_coverage, dict)
        else None
    )
    context = _DraftContext(
        coverage=coverage,
        throughput_coverage=throughput_coverage,
        finding=finding,
        throughput=throughput,
        quality=quality,
        events=events,
        tracks=tracks,
    )
    cache[key] = context
    return context


def _event_year_map(db: Session, event_keys: set[str]) -> dict[str, int]:
    if not event_keys:
        return {}
    rows = (
        db.query(models.Event.event_key, models.Event.year)
        .filter(models.Event.event_key.in_(sorted(event_keys)))
        .all()
    )
    result: dict[str, int] = {}
    for raw_event_key, raw_year in rows:
        event_key = str(raw_event_key or "").strip().lower()
        if not event_key:
            continue
        try:
            result[event_key] = int(raw_year)
        except (TypeError, ValueError):
            continue
    return result


def export_auto_scout_training_snapshots(
    db: Session,
    *,
    source_version: str | None = None,
    season_year: int | None = None,
    replace_existing: bool = False,
    max_drafts: int | None = None,
    field_names: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    resolved_source_version = str(source_version or auto_scout_training_source_version()).strip()
    if not resolved_source_version:
        resolved_source_version = auto_scout_training_source_version()

    selected_fields = tuple(
        sorted(
            {
                str(field or "").strip().lower()
                for field in (
                    field_names
                    if isinstance(field_names, (list, tuple)) and field_names
                    else AUTO_SCOUT_ROUND2_ML_FIELDS
                )
                if str(field or "").strip()
            }
        )
    )
    if not selected_fields:
        raise RuntimeError("No fields selected for auto-scout training export.")
    unknown_fields = sorted(set(selected_fields) - set(AUTO_SCOUT_ROUND2_ML_FIELDS))
    if unknown_fields:
        raise RuntimeError(
            "Unsupported auto-scout field(s) for export: "
            + ", ".join(unknown_fields)
        )

    query = db.query(models.AutoScoutDraft).filter(models.AutoScoutDraft.status == "approved")
    if isinstance(season_year, int):
        query = (
            query.join(models.Event, models.Event.event_key == models.AutoScoutDraft.event_key)
            .filter(models.Event.year == int(season_year))
        )
    query = query.order_by(models.AutoScoutDraft.approved_at.desc().nullslast(), models.AutoScoutDraft.id.desc())
    if isinstance(max_drafts, int) and max_drafts > 0:
        query = query.limit(int(max_drafts))
    approved_rows = query.all()

    if not approved_rows:
        return {
            "ok": True,
            "source_version": resolved_source_version,
            "field_names": list(selected_fields),
            "approved_drafts": 0,
            "rows_written": 0,
            "skipped_missing_context": 0,
            "skipped_missing_target": 0,
            "per_field_rows": {field: 0 for field in selected_fields},
        }

    event_keys = {str(row.event_key or "").strip().lower() for row in approved_rows if str(row.event_key or "").strip()}
    event_years = _event_year_map(db, event_keys)
    latest_year = max(event_years.values()) if event_years else None

    if replace_existing:
        scopes = [auto_scout_field_scope(field) for field in selected_fields]
        db.execute(
            delete(models.MLFeatureSnapshot).where(
                models.MLFeatureSnapshot.source_version == resolved_source_version,
                models.MLFeatureSnapshot.scope.in_(scopes),
                models.MLFeatureSnapshot.event_key.in_(sorted(event_keys)),
            )
        )
        db.flush()

    context_cache: dict[tuple[int, str], _DraftContext | None] = {}
    per_field_rows = {field: 0 for field in selected_fields}
    rows_written = 0
    skipped_missing_context = 0
    skipped_missing_target = 0

    for row in approved_rows:
        event_key = str(row.event_key or "").strip().lower()
        match_key = str(row.match_key or "").strip().lower()
        team_key = str(row.team_key or "").strip().lower()
        approved_payload = row.approved_payload if isinstance(row.approved_payload, dict) else {}
        approved_form = approved_payload.get("form_patch") if isinstance(approved_payload.get("form_patch"), dict) else {}
        if not event_key or not match_key or not team_key or not approved_form:
            continue

        context = _load_draft_context(
            db,
            analysis_run_id=row.analysis_run_id,
            team_key=team_key,
            cache=context_cache,
        )
        if context is None:
            skipped_missing_context += 1
            continue

        feature_vector = build_auto_scout_field_feature_vector(
            season_year=int(event_years.get(event_key) or season_year or 0),
            coverage=context.coverage,
            throughput_coverage=context.throughput_coverage,
            events=context.events,
            tracks=context.tracks,
            finding=context.finding,
            throughput=context.throughput,
            quality=context.quality,
        )
        split_tag = "holdout" if latest_year is not None and event_years.get(event_key) == latest_year else "train"

        for field_name in selected_fields:
            if field_name not in approved_form:
                continue
            target_value = auto_scout_field_target_to_float(field_name, approved_form.get(field_name))
            if target_value is None:
                skipped_missing_target += 1
                continue

            scope = auto_scout_field_scope(field_name)
            snapshot_key = f"{scope}:{match_key}:{team_key}:{row.id}:{resolved_source_version}"
            db.merge(
                models.MLFeatureSnapshot(
                    snapshot_key=snapshot_key,
                    scope=scope,
                    event_key=event_key,
                    match_key=match_key,
                    team_key=team_key,
                    alliance_color=None,
                    feature_vector=feature_vector,
                    target={
                        "field_value": float(target_value),
                        "field_name": field_name,
                        "draft_id": int(row.id),
                        "approved_at": (
                            row.approved_at.isoformat()
                            if isinstance(row.approved_at, datetime)
                            else None
                        ),
                    },
                    split_tag=split_tag,
                    source_version=resolved_source_version,
                )
            )
            rows_written += 1
            per_field_rows[field_name] = int(per_field_rows.get(field_name) or 0) + 1

    db.commit()
    return {
        "ok": True,
        "source_version": resolved_source_version,
        "field_names": list(selected_fields),
        "approved_drafts": len(approved_rows),
        "rows_written": rows_written,
        "skipped_missing_context": skipped_missing_context,
        "skipped_missing_target": skipped_missing_target,
        "per_field_rows": per_field_rows,
        "latest_year": latest_year,
    }
