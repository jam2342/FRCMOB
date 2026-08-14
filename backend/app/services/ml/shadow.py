from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import logging
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from sqlalchemy import delete, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.auto_scout.ml import (
    AUTO_SCOUT_FIELD_FEATURE_ORDER,
    AUTO_SCOUT_FIELD_MODEL_PREFIX,
    auto_scout_field_model_key,
)
from app.services.auto_scout.training import auto_scout_training_source_version
from app.services.ml.role_ml import (
    ROLE_SIGNAL_FEATURE_ORDER,
    ROLE_SIGNAL_MODEL_PREFIX,
    ROLE_SIGNAL_NAMES,
    normalize_role_signal_name,
    rebuild_role_signal_feature_snapshots,
    role_signal_model_key,
    role_signal_scope,
)
from app.services.ml.synergy_ml import (
    FEATURE_SCOPE_SYNERGY_PAIR,
    SYNERGY_PAIR_FEATURE_ORDER,
    SYNERGY_PAIR_MODEL_KEY,
    rebuild_synergy_pair_feature_snapshots,
)
from app.services.ml.model_eval import (
    binary_log_loss,
    brier_score,
    mean_absolute_error,
    root_mean_squared_error,
    spearman_rank_correlation,
    time_split,
)

try:
    import torch
    from torch import nn
except Exception:  # pragma: no cover - exercised when torch isn't available
    torch = None
    nn = None


logger = logging.getLogger(__name__)
_RUNTIME_CACHE: dict[str, dict[str, Any]] = {}

TEAM_STRENGTH_MODEL_KEY = "team_strength"
MATCH_OUTCOME_MODEL_KEY = "match_outcome"
FEATURE_SCOPE_TEAM_STRENGTH = "team_strength"
FEATURE_SCOPE_MATCH_OUTCOME = "match_outcome"
FEATURE_SCOPE_AUTO_SCOUT_FIELD_PREFIX = AUTO_SCOUT_FIELD_MODEL_PREFIX

TEAM_STRENGTH_FEATURE_ORDER = [
    "rating_0_100",
    "confidence_0_1",
    "throughput_0_100",
    "driver_skill_0_100",
    "robot_level_0_100",
    "endgame_0_100",
    "consistency_0_100",
    "shift_productivity_0_100",
    "capacity_utilization_0_100",
    "auto_contribution_0_100",
    "manual_points_impact_0_100",
    "rp_contribution_0_100",
    "anti_defense_0_100",
    "defense_presence_0_100",
    "penalty_discipline_0_100",
    "expected_net_points",
    "auto_points_est",
    "teleop_points_est",
    "climb_output",
    "uptime_0_1",
    "throughput_trend_delta",
    "reliability_trend_delta",
    "cycle_trend_delta",
    "penalty_trend_delta",
    "throughput_coverage_0_1",
    "avg_analysis_quality_0_1",
    "avg_identity_quality_0_1",
    "coverage_factor_0_1",
    "match_quality_weight_0_1",
    "recent_match_factor_0_1",
    "trend_coverage_factor_0_1",
    "match_count",
    "excluded_match_count",
    "video_findings_count",
    "anti_defense_pressure_coverage_0_1",
    "severe_penalty_rate",
    "event_year",
]

MATCH_OUTCOME_FEATURE_ORDER = [
    "red_rating_mean",
    "blue_rating_mean",
    "rating_margin",
    "red_confidence_mean",
    "blue_confidence_mean",
    "confidence_margin",
    "red_synergy_points",
    "blue_synergy_points",
    "synergy_margin",
    "red_projected_throughput",
    "blue_projected_throughput",
    "projected_margin",
]


def _torch_available() -> bool:
    return torch is not None and nn is not None


def _require_torch() -> None:
    if not _torch_available():
        raise RuntimeError(
            "PyTorch is required for shadow model training/inference. "
            "Install torch (YOLO dependency) and retry."
        )


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isfinite(numeric):
            return numeric
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            numeric = float(raw)
        except ValueError:
            return None
        return numeric if math.isfinite(numeric) else None
    return None


def _event_year_from_key(event_key: object) -> int | None:
    token = str(event_key or "").strip().lower()
    prefix = token[:4]
    if prefix.isdigit():
        return int(prefix)
    return None


def _json_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _team_strength_feature_vector_from_row_payload(row: dict[str, Any]) -> dict[str, float]:
    event_year = _event_year_from_key(row.get("event_key"))
    return {
        "rating_0_100": float(_safe_float(row.get("rating_0_100")) or 50.0),
        "confidence_0_1": float(_safe_float(row.get("confidence_0_1")) or 0.0),
        "throughput_0_100": float(_safe_float(row.get("throughput_0_100")) or 50.0),
        "driver_skill_0_100": float(_safe_float(row.get("driver_skill_0_100")) or 50.0),
        "robot_level_0_100": float(_safe_float(row.get("robot_level_0_100")) or 50.0),
        "endgame_0_100": float(_safe_float(row.get("endgame_0_100")) or 50.0),
        "consistency_0_100": float(_safe_float(row.get("consistency_0_100")) or 50.0),
        "shift_productivity_0_100": float(_safe_float(row.get("shift_productivity_0_100")) or 50.0),
        "capacity_utilization_0_100": float(_safe_float(row.get("capacity_utilization_0_100")) or 50.0),
        "auto_contribution_0_100": float(_safe_float(row.get("auto_contribution_0_100")) or 50.0),
        "manual_points_impact_0_100": float(_safe_float(row.get("manual_points_impact_0_100")) or 50.0),
        "rp_contribution_0_100": float(_safe_float(row.get("rp_contribution_0_100")) or 50.0),
        "anti_defense_0_100": float(_safe_float(row.get("anti_defense_0_100")) or 50.0),
        "defense_presence_0_100": float(_safe_float(row.get("defense_presence_0_100")) or 50.0),
        "penalty_discipline_0_100": float(_safe_float(row.get("penalty_discipline_0_100")) or 50.0),
        "expected_net_points": float(_safe_float(row.get("expected_net_points")) or 0.0),
        "auto_points_est": float(_safe_float(row.get("auto_points_est")) or 0.0),
        "teleop_points_est": float(_safe_float(row.get("teleop_points_est")) or 0.0),
        "climb_output": float(_safe_float(row.get("climb_output")) or 0.0),
        "uptime_0_1": float(_safe_float(row.get("uptime_0_1")) or 0.0),
        "throughput_trend_delta": float(_safe_float(row.get("throughput_trend_delta")) or 0.0),
        "reliability_trend_delta": float(_safe_float(row.get("reliability_trend_delta")) or 0.0),
        "cycle_trend_delta": float(_safe_float(row.get("cycle_trend_delta")) or 0.0),
        "penalty_trend_delta": float(_safe_float(row.get("penalty_trend_delta")) or 0.0),
        "throughput_coverage_0_1": float(_safe_float(row.get("throughput_coverage_0_1")) or 0.0),
        "avg_analysis_quality_0_1": float(_safe_float(row.get("avg_analysis_quality_0_1")) or 0.0),
        "avg_identity_quality_0_1": float(_safe_float(row.get("avg_identity_quality_0_1")) or 0.0),
        "coverage_factor_0_1": float(_safe_float(row.get("coverage_factor_0_1")) or 0.0),
        "match_quality_weight_0_1": float(_safe_float(row.get("match_quality_weight_0_1")) or 0.0),
        "recent_match_factor_0_1": float(_safe_float(row.get("recent_match_factor_0_1")) or 0.0),
        "trend_coverage_factor_0_1": float(_safe_float(row.get("trend_coverage_factor_0_1")) or 0.0),
        "match_count": float(_safe_float(row.get("match_count")) or 0.0),
        "excluded_match_count": float(_safe_float(row.get("excluded_match_count")) or 0.0),
        "video_findings_count": float(_safe_float(row.get("video_findings_count")) or 0.0),
        "anti_defense_pressure_coverage_0_1": float(
            _safe_float(row.get("anti_defense_pressure_coverage_0_1")) or 0.0
        ),
        "severe_penalty_rate": float(_safe_float(row.get("severe_penalty_rate")) or 0.0),
        "event_year": float(event_year or 0),
    }


def build_team_strength_shadow_input_from_rating_row(
    row: models.EventTeamRating,
) -> dict[str, Any]:
    details = _json_dict(row.details_json)
    subscores = _json_dict(details.get("subscores"))
    raw_features = _json_dict(details.get("raw_features"))
    support = _json_dict(details.get("confidence_support"))
    quality_gate = _json_dict(details.get("quality_gate"))
    return {
        "event_key": str(row.event_key or "").strip().lower() or None,
        "team_key": str(row.team_key or "").strip().lower() or None,
        "rating_0_100": row.rating_0_100,
        "confidence_0_1": row.confidence_0_1,
        "throughput_0_100": row.throughput,
        "driver_skill_0_100": row.driver_skill_0_100,
        "robot_level_0_100": row.robot_level_0_100,
        "endgame_0_100": row.endgame,
        "consistency_0_100": row.consistency,
        "shift_productivity_0_100": row.shift_productivity,
        "capacity_utilization_0_100": row.capacity_utilization,
        "auto_contribution_0_100": subscores.get("auto_contribution"),
        "manual_points_impact_0_100": subscores.get("manual_points_impact"),
        "rp_contribution_0_100": subscores.get("rp_contribution"),
        "anti_defense_0_100": subscores.get("anti_defense"),
        "defense_presence_0_100": subscores.get("defense_presence"),
        "penalty_discipline_0_100": subscores.get("penalty_discipline"),
        "expected_net_points": raw_features.get("expected_net_points"),
        "auto_points_est": raw_features.get("auto_points_est"),
        "teleop_points_est": raw_features.get("teleop_points_est"),
        "climb_output": raw_features.get("climb_output"),
        "uptime_0_1": raw_features.get("uptime"),
        "throughput_trend_delta": raw_features.get("throughput_trend_delta"),
        "reliability_trend_delta": raw_features.get("reliability_trend_delta"),
        "cycle_trend_delta": raw_features.get("cycle_trend_delta"),
        "penalty_trend_delta": raw_features.get("penalty_trend_delta"),
        "throughput_coverage_0_1": raw_features.get("throughput_coverage"),
        "avg_analysis_quality_0_1": support.get("avg_analysis_quality"),
        "avg_identity_quality_0_1": support.get("avg_identity_quality"),
        "coverage_factor_0_1": support.get("coverage_factor"),
        "match_quality_weight_0_1": support.get("match_quality_weight_factor"),
        "recent_match_factor_0_1": support.get("recent_match_factor"),
        "trend_coverage_factor_0_1": support.get("trend_coverage_factor"),
        "match_count": quality_gate.get("accepted_match_count", details.get("match_count")),
        "excluded_match_count": quality_gate.get("excluded_match_count"),
        "video_findings_count": raw_features.get("video_findings_count"),
        "anti_defense_pressure_coverage_0_1": raw_features.get("anti_defense_pressure_coverage"),
        "severe_penalty_rate": raw_features.get("severe_penalty_rate"),
    }


def _artifact_dir() -> Path:
    path = Path(str(getattr(settings, "ml_shadow_model_dir", "media/models/shadow") or "media/models/shadow"))
    if not path.is_absolute():
        path = (Path(__file__).resolve().parents[2] / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_artifact_path(raw_path: object) -> Path:
    artifact_path = Path(str(raw_path or "")).expanduser()
    if not artifact_path.is_absolute():
        artifact_path = (Path(__file__).resolve().parents[2] / artifact_path).resolve()
    else:
        artifact_path = artifact_path.resolve()

    allowed_root = _artifact_dir().resolve()
    try:
        artifact_path.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(
            f"Shadow model artifact is outside the configured model directory: {artifact_path}"
        ) from exc

    if artifact_path.suffix != ".pt":
        raise ValueError(f"Shadow model artifact must be a .pt file: {artifact_path}")
    if not artifact_path.exists():
        raise FileNotFoundError(f"Shadow model artifact not found: {artifact_path}")
    return artifact_path


def _feature_source_version() -> str:
    return str(getattr(settings, "ml_shadow_feature_source_version", "shadow_features_v1") or "shadow_features_v1")


def _rollout_ratio() -> float:
    return max(0.0, min(float(getattr(settings, "ml_shadow_rollout_ratio", 0.0) or 0.0), 1.0))


def is_shadow_rollout_active(shard_key: str) -> bool:
    if not bool(getattr(settings, "ml_shadow_enabled", False)):
        return False
    ratio = _rollout_ratio()
    if ratio <= 0.0:
        return False
    if ratio >= 1.0:
        return True
    digest = hashlib.sha1(str(shard_key or "").encode("utf-8"), usedforsecurity=False).hexdigest()
    bucket = int(digest[:8], 16) / float(0xFFFFFFFF)
    return bucket < ratio


def _event_keys_for_snapshot_rebuild(
    db: Session,
    event_key: str | None,
    limit_events: int,
    *,
    season_year: int | None = None,
) -> list[str]:
    if event_key:
        normalized = str(event_key).strip().lower()
        return [normalized] if normalized else []

    capped_limit = max(1, min(int(limit_events or 30), 200))
    query = (
        db.query(models.Event.event_key)
        .join(models.Match, models.Match.event_key == models.Event.event_key)
    )
    if isinstance(season_year, int):
        query = query.filter(models.Event.year == int(season_year))

    rows = (
        query
        .group_by(models.Event.event_key, models.Event.year)
        .order_by(models.Event.year.desc(), models.Event.event_key.asc())
        .limit(capped_limit)
        .all()
    )
    return [str(row[0]).strip().lower() for row in rows if isinstance(row[0], str) and row[0].strip()]


def _team_target_map(db: Session, event_keys: set[str]) -> dict[tuple[str, str], dict[str, float]]:
    if not event_keys:
        return {}
    rows = (
        db.query(
            models.TeamMatchThroughput.event_key,
            models.TeamMatchThroughput.team_key,
            func.avg(models.TeamMatchThroughput.active_bps),
            func.count(models.TeamMatchThroughput.finding_id),
        )
        .filter(
            models.TeamMatchThroughput.event_key.in_(sorted(event_keys)),
            models.TeamMatchThroughput.active_bps.isnot(None),
        )
        .group_by(
            models.TeamMatchThroughput.event_key,
            models.TeamMatchThroughput.team_key,
        )
        .all()
    )
    target_map: dict[tuple[str, str], dict[str, float]] = {}
    for raw_event_key, raw_team_key, avg_active_bps, sample_count in rows:
        event_value = str(raw_event_key or "").strip().lower()
        team_value = str(raw_team_key or "").strip().lower()
        avg_value = _safe_float(avg_active_bps)
        if not event_value or not team_value or avg_value is None:
            continue
        target_map[(event_value, team_value)] = {
            "strength_active_bps": float(avg_value),
            "samples": float(int(sample_count or 0)),
        }
    return target_map


def _match_target_map(db: Session, event_keys: set[str]) -> dict[str, dict[str, float]]:
    if not event_keys:
        return {}
    rows = (
        db.query(
            models.TeamMatchThroughput.match_key,
            models.TeamMatchFinding.alliance,
            func.sum(models.TeamMatchThroughput.active_bps),
            func.count(models.TeamMatchThroughput.finding_id),
        )
        .join(models.TeamMatchFinding, models.TeamMatchFinding.id == models.TeamMatchThroughput.finding_id)
        .filter(
            models.TeamMatchThroughput.event_key.in_(sorted(event_keys)),
            models.TeamMatchThroughput.active_bps.isnot(None),
        )
        .group_by(
            models.TeamMatchThroughput.match_key,
            models.TeamMatchFinding.alliance,
        )
        .all()
    )

    aggregate: dict[str, dict[str, float]] = {}
    for raw_match_key, raw_alliance, sum_active_bps, sample_count in rows:
        match_key = str(raw_match_key or "").strip().lower()
        alliance = str(raw_alliance or "").strip().lower()
        total = _safe_float(sum_active_bps)
        if not match_key or alliance not in {"red", "blue"} or total is None:
            continue
        payload = aggregate.setdefault(match_key, {})
        payload[f"{alliance}_total"] = float(total)
        payload[f"{alliance}_samples"] = float(int(sample_count or 0))

    result: dict[str, dict[str, float]] = {}
    for match_key, payload in aggregate.items():
        if "red_total" not in payload or "blue_total" not in payload:
            continue
        margin = float(payload["red_total"]) - float(payload["blue_total"])
        if abs(margin) < 1e-9:
            continue
        result[match_key] = {
            "red_total": float(payload["red_total"]),
            "blue_total": float(payload["blue_total"]),
            "red_margin": margin,
            "red_win": 1.0 if margin > 0.0 else 0.0,
            "sample_count": float(payload.get("red_samples", 0.0) + payload.get("blue_samples", 0.0)),
        }
    return result


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
        event_value = str(raw_event_key or "").strip().lower()
        if not event_value:
            continue
        try:
            result[event_value] = int(raw_year)
        except (TypeError, ValueError):
            continue
    return result


def _latest_year(year_map: dict[str, int]) -> int | None:
    if not year_map:
        return None
    return max(year_map.values())


def _split_tag_for_year(year: int | None, latest_year: int | None) -> str:
    if latest_year is None or year is None:
        return "train"
    return "holdout" if int(year) >= int(latest_year) else "train"


def _synergy_projection_map(db: Session, event_keys: set[str]) -> dict[tuple[str, str], dict[str, float]]:
    if not event_keys:
        return {}
    rows = (
        db.query(
            models.MatchSynergyProjection.match_key,
            models.MatchSynergyProjection.alliance_color,
            models.MatchSynergyProjection.alliance_synergy_points,
            models.MatchSynergyProjection.projected_throughput,
            models.MatchSynergyProjection.computed_at,
        )
        .filter(models.MatchSynergyProjection.event_key.in_(sorted(event_keys)))
        .order_by(models.MatchSynergyProjection.computed_at.desc(), models.MatchSynergyProjection.model_version.asc())
        .all()
    )
    result: dict[tuple[str, str], dict[str, float]] = {}
    for raw_match_key, raw_alliance, synergy_points, projected_throughput, _computed_at in rows:
        match_key = str(raw_match_key or "").strip().lower()
        alliance = str(raw_alliance or "").strip().lower()
        if not match_key or alliance not in {"red", "blue"}:
            continue
        key = (match_key, alliance)
        if key in result:
            continue
        result[key] = {
            "synergy_points": float(_safe_float(synergy_points) or 0.0),
            "projected_throughput": float(_safe_float(projected_throughput) or 0.0),
        }
    return result


def _rebuild_team_strength_feature_snapshots(
    db: Session,
    *,
    event_keys: set[str],
    source_version: str,
    year_map: dict[str, int],
) -> dict[str, int]:
    if not event_keys:
        return {"rows_written": 0, "skipped_missing_target": 0}

    target_by_team = _team_target_map(db, event_keys)
    latest_year = _latest_year(year_map)

    rating_rows = (
        db.query(models.EventTeamRating)
        .filter(models.EventTeamRating.event_key.in_(sorted(event_keys)))
        .all()
    )

    rows_written = 0
    skipped_missing_target = 0
    for rating in rating_rows:
        event_key = str(rating.event_key or "").strip().lower()
        team_key = str(rating.team_key or "").strip().lower()
        if not event_key or not team_key:
            continue
        target_payload = target_by_team.get((event_key, team_key))
        if target_payload is None:
            skipped_missing_target += 1
            continue

        feature_vector = _team_strength_feature_vector_from_row_payload(
            build_team_strength_shadow_input_from_rating_row(rating)
        )

        split_tag = _split_tag_for_year(year_map.get(event_key), latest_year)
        snapshot_key = f"{FEATURE_SCOPE_TEAM_STRENGTH}:{event_key}:{team_key}:{source_version}"
        db.merge(
            models.MLFeatureSnapshot(
                snapshot_key=snapshot_key,
                scope=FEATURE_SCOPE_TEAM_STRENGTH,
                event_key=event_key,
                team_key=team_key,
                alliance_color=None,
                feature_vector=feature_vector,
                target=target_payload,
                split_tag=split_tag,
                source_version=source_version,
            )
        )
        rows_written += 1

    return {
        "rows_written": rows_written,
        "skipped_missing_target": skipped_missing_target,
    }


def _rebuild_match_outcome_feature_snapshots(
    db: Session,
    *,
    event_keys: set[str],
    source_version: str,
    year_map: dict[str, int],
) -> dict[str, int]:
    if not event_keys:
        return {"rows_written": 0, "skipped_missing_target": 0, "skipped_missing_alliance_data": 0}

    latest_year = _latest_year(year_map)
    match_target_map = _match_target_map(db, event_keys)
    synergy_map = _synergy_projection_map(db, event_keys)

    team_rows = (
        db.query(
            models.Match.match_key,
            models.Match.event_key,
            models.Match.time,
            models.MatchTeam.alliance,
            models.MatchTeam.team_key,
            models.EventTeamRating.rating_0_100,
            models.EventTeamRating.confidence_0_1,
        )
        .join(models.MatchTeam, models.MatchTeam.match_key == models.Match.match_key)
        .outerjoin(
            models.EventTeamRating,
            (models.EventTeamRating.event_key == models.MatchTeam.event_key)
            & (models.EventTeamRating.team_key == models.MatchTeam.team_key),
        )
        .filter(models.Match.event_key.in_(sorted(event_keys)))
        .order_by(models.Match.time.asc().nullslast(), models.Match.match_key.asc())
        .all()
    )

    by_match: dict[str, dict[str, Any]] = {}
    for raw_match_key, raw_event_key, match_time, raw_alliance, raw_team_key, rating, confidence in team_rows:
        match_key = str(raw_match_key or "").strip().lower()
        event_key = str(raw_event_key or "").strip().lower()
        alliance = str(raw_alliance or "").strip().lower()
        team_key = str(raw_team_key or "").strip().lower()
        if not match_key or not event_key or alliance not in {"red", "blue"} or not team_key:
            continue

        payload = by_match.setdefault(
            match_key,
            {
                "event_key": event_key,
                "match_time": int(match_time) if isinstance(match_time, int) else 0,
                "red_ratings": [],
                "blue_ratings": [],
                "red_conf": [],
                "blue_conf": [],
            },
        )

        numeric_rating = float(_safe_float(rating) or 50.0)
        numeric_conf = max(0.0, min(1.0, float(_safe_float(confidence) or 0.0)))
        if alliance == "red":
            payload["red_ratings"].append(numeric_rating)
            payload["red_conf"].append(numeric_conf)
        else:
            payload["blue_ratings"].append(numeric_rating)
            payload["blue_conf"].append(numeric_conf)

    rows_written = 0
    skipped_missing_target = 0
    skipped_missing_alliance_data = 0

    for match_key, payload in by_match.items():
        target = match_target_map.get(match_key)
        if target is None:
            skipped_missing_target += 1
            continue

        red_ratings = [float(value) for value in payload.get("red_ratings", []) if isinstance(value, (int, float))]
        blue_ratings = [float(value) for value in payload.get("blue_ratings", []) if isinstance(value, (int, float))]
        red_conf = [float(value) for value in payload.get("red_conf", []) if isinstance(value, (int, float))]
        blue_conf = [float(value) for value in payload.get("blue_conf", []) if isinstance(value, (int, float))]

        if not red_ratings or not blue_ratings:
            skipped_missing_alliance_data += 1
            continue

        red_rating_mean = sum(red_ratings) / float(len(red_ratings))
        blue_rating_mean = sum(blue_ratings) / float(len(blue_ratings))
        red_conf_mean = (sum(red_conf) / float(len(red_conf))) if red_conf else 0.0
        blue_conf_mean = (sum(blue_conf) / float(len(blue_conf))) if blue_conf else 0.0

        red_synergy = synergy_map.get((match_key, "red"), {})
        blue_synergy = synergy_map.get((match_key, "blue"), {})
        red_synergy_points = float(_safe_float(red_synergy.get("synergy_points")) or 0.0)
        blue_synergy_points = float(_safe_float(blue_synergy.get("synergy_points")) or 0.0)
        red_projected = float(_safe_float(red_synergy.get("projected_throughput")) or 0.0)
        blue_projected = float(_safe_float(blue_synergy.get("projected_throughput")) or 0.0)

        event_key = str(payload.get("event_key") or "").strip().lower()
        match_time = int(payload.get("match_time") or 0)
        split_tag = _split_tag_for_year(year_map.get(event_key), latest_year)

        feature_vector = {
            "red_rating_mean": red_rating_mean,
            "blue_rating_mean": blue_rating_mean,
            "rating_margin": red_rating_mean - blue_rating_mean,
            "red_confidence_mean": red_conf_mean,
            "blue_confidence_mean": blue_conf_mean,
            "confidence_margin": red_conf_mean - blue_conf_mean,
            "red_synergy_points": red_synergy_points,
            "blue_synergy_points": blue_synergy_points,
            "synergy_margin": red_synergy_points - blue_synergy_points,
            "red_projected_throughput": red_projected,
            "blue_projected_throughput": blue_projected,
            "projected_margin": red_projected - blue_projected,
            "match_time_unix": float(match_time),
        }

        snapshot_key = f"{FEATURE_SCOPE_MATCH_OUTCOME}:{match_key}:{source_version}"
        db.merge(
            models.MLFeatureSnapshot(
                snapshot_key=snapshot_key,
                scope=FEATURE_SCOPE_MATCH_OUTCOME,
                event_key=event_key,
                match_key=match_key,
                alliance_color=None,
                feature_vector=feature_vector,
                target=target,
                split_tag=split_tag,
                source_version=source_version,
            )
        )
        rows_written += 1

    return {
        "rows_written": rows_written,
        "skipped_missing_target": skipped_missing_target,
        "skipped_missing_alliance_data": skipped_missing_alliance_data,
    }


def rebuild_feature_snapshots(
    db: Session,
    *,
    event_key: str | None = None,
    limit_events: int = 30,
    season_year: int | None = None,
    replace_existing: bool = True,
    source_version: str | None = None,
) -> dict[str, Any]:
    resolved_source_version = str(source_version or _feature_source_version()).strip() or "shadow_features_v1"
    event_keys = _event_keys_for_snapshot_rebuild(
        db,
        event_key,
        limit_events,
        season_year=season_year,
    )
    event_key_set = {value for value in event_keys if value}
    if not event_key_set:
        return {
            "ok": True,
            "source_version": resolved_source_version,
            "season_year": int(season_year) if isinstance(season_year, int) else None,
            "event_count": 0,
            "event_keys": [],
            "team_strength": {"rows_written": 0, "skipped_missing_target": 0},
            "match_outcome": {
                "rows_written": 0,
                "skipped_missing_target": 0,
                "skipped_missing_alliance_data": 0,
            },
            "synergy_pair": {"rows_written": 0, "skipped_no_prior": 0},
            "role_signal": {"rows_written": 0, "skipped_no_labels": 0},
        }

    if replace_existing:
        db.execute(
            delete(models.MLFeatureSnapshot).where(
                models.MLFeatureSnapshot.source_version == resolved_source_version,
                models.MLFeatureSnapshot.event_key.in_(sorted(event_key_set)),
            )
        )
        db.flush()

    year_map = _event_year_map(db, event_key_set)
    team_result = _rebuild_team_strength_feature_snapshots(
        db,
        event_keys=event_key_set,
        source_version=resolved_source_version,
        year_map=year_map,
    )
    match_result = _rebuild_match_outcome_feature_snapshots(
        db,
        event_keys=event_key_set,
        source_version=resolved_source_version,
        year_map=year_map,
    )
    synergy_pair_result = rebuild_synergy_pair_feature_snapshots(
        db,
        event_keys=event_key_set,
        source_version=resolved_source_version,
        year_map=year_map,
    )
    role_signal_result = rebuild_role_signal_feature_snapshots(
        db,
        event_keys=event_key_set,
        source_version=resolved_source_version,
        year_map=year_map,
    )

    db.commit()

    return {
        "ok": True,
        "source_version": resolved_source_version,
        "season_year": int(season_year) if isinstance(season_year, int) else None,
        "event_count": len(event_key_set),
        "event_keys": sorted(event_key_set),
        "team_strength": team_result,
        "match_outcome": match_result,
        "synergy_pair": synergy_pair_result,
        "role_signal": role_signal_result,
    }


@dataclass
class _PreparedDataset:
    feature_order: list[str]
    train_x: "torch.Tensor"
    train_y: "torch.Tensor"
    val_x: "torch.Tensor"
    val_y: "torch.Tensor"
    mean: "torch.Tensor"
    std: "torch.Tensor"
    train_count: int
    val_count: int


def _prepare_dataset(
    snapshots: list[models.MLFeatureSnapshot],
    *,
    feature_order: list[str],
    target_key: str,
    train_ratio: float = 0.8,
) -> _PreparedDataset:
    _require_torch()

    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        feature_vector = snapshot.feature_vector if isinstance(snapshot.feature_vector, dict) else {}
        target = snapshot.target if isinstance(snapshot.target, dict) else {}
        target_value = _safe_float(target.get(target_key))
        if target_value is None:
            continue

        timestamp_candidate = _safe_float(feature_vector.get("match_time_unix"))
        if timestamp_candidate is None:
            created = snapshot.created_at
            if isinstance(created, datetime):
                timestamp_candidate = created.replace(tzinfo=timezone.utc).timestamp()
            else:
                timestamp_candidate = 0.0

        rows.append(
            {
                "x": [float(_safe_float(feature_vector.get(name)) or 0.0) for name in feature_order],
                "y": float(target_value),
                "timestamp": float(timestamp_candidate),
            }
        )

    if len(rows) < 20:
        raise RuntimeError(f"Not enough labeled snapshot rows (got {len(rows)}).")

    train_rows, val_rows = time_split(rows, timestamp_fn=lambda item: item["timestamp"], train_ratio=train_ratio)
    if not train_rows or not val_rows:
        raise RuntimeError("Time split failed: need both train and validation rows.")

    train_x = torch.tensor([row["x"] for row in train_rows], dtype=torch.float32)
    train_y = torch.tensor([row["y"] for row in train_rows], dtype=torch.float32)
    val_x = torch.tensor([row["x"] for row in val_rows], dtype=torch.float32)
    val_y = torch.tensor([row["y"] for row in val_rows], dtype=torch.float32)

    mean = train_x.mean(dim=0)
    std = train_x.std(dim=0)
    std = torch.where(std < 1e-6, torch.ones_like(std), std)

    train_x = (train_x - mean) / std
    val_x = (val_x - mean) / std

    return _PreparedDataset(
        feature_order=feature_order,
        train_x=train_x,
        train_y=train_y,
        val_x=val_x,
        val_y=val_y,
        mean=mean,
        std=std,
        train_count=int(train_x.shape[0]),
        val_count=int(val_x.shape[0]),
    )


if nn is not None:
    class _ShadowNet(nn.Module):  # type: ignore[misc]
        def __init__(
            self,
            input_dim: int,
            hidden_dim: int = 64,
            dropout: float = 0.15,
            use_batch_norm: bool = True,
        ):
            super().__init__()
            layers: list[nn.Module] = []

            # Layer 1
            layers.append(nn.Linear(input_dim, hidden_dim))
            if use_batch_norm:
                layers.append(nn.BatchNorm1d(hidden_dim))
            layers.append(nn.ReLU())
            if dropout > 0.0:
                layers.append(nn.Dropout(dropout))

            # Layer 2
            layers.append(nn.Linear(hidden_dim, hidden_dim))
            if use_batch_norm:
                layers.append(nn.BatchNorm1d(hidden_dim))
            layers.append(nn.ReLU())
            if dropout > 0.0:
                layers.append(nn.Dropout(dropout))

            # Layer 3 (narrowing)
            narrow_dim = max(8, hidden_dim // 2)
            layers.append(nn.Linear(hidden_dim, narrow_dim))
            layers.append(nn.ReLU())

            # Output
            layers.append(nn.Linear(narrow_dim, 1))

            self.layers = nn.Sequential(*layers)

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            return self.layers(x)
else:  # pragma: no cover - only active when torch is missing
    class _ShadowNet:  # type: ignore[no-redef]
        def __init__(
            self,
            input_dim: int,
            hidden_dim: int = 64,
            dropout: float = 0.15,
            use_batch_norm: bool = True,
        ):
            _require_torch()


_EARLY_STOPPING_PATIENCE = 30
_GRAD_CLIP_NORM = 1.0
_LR_SCHEDULER_PATIENCE = 12
_LR_SCHEDULER_FACTOR = 0.5
_MIN_LR = 1e-6


def _train_regression_model(
    dataset: _PreparedDataset,
    *,
    epochs: int = 300,
    learning_rate: float = 0.003,
) -> tuple[Any, dict[str, Any]]:
    _require_torch()
    model = _ShadowNet(input_dim=len(dataset.feature_order))
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(learning_rate), weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=_LR_SCHEDULER_FACTOR,
        patience=_LR_SCHEDULER_PATIENCE, min_lr=_MIN_LR,
    )
    loss_fn = nn.SmoothL1Loss(beta=0.2)

    best_state = None
    best_val_loss = float("inf")
    best_epoch = 0
    epochs_without_improvement = 0

    for epoch in range(max(60, int(epochs))):
        model.train()
        optimizer.zero_grad()
        predictions = model(dataset.train_x).squeeze(-1)
        loss = loss_fn(predictions, dataset.train_y)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), _GRAD_CLIP_NORM)
        optimizer.step()

        model.eval()
        with torch.no_grad():
            val_predictions = model(dataset.val_x).squeeze(-1)
            val_loss = float(loss_fn(val_predictions, dataset.val_y).item())

        scheduler.step(val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_epoch = epoch
            epochs_without_improvement = 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            epochs_without_improvement += 1

        if epochs_without_improvement >= _EARLY_STOPPING_PATIENCE and epoch >= 60:
            logger.info("Regression early stop at epoch %d (best=%d)", epoch, best_epoch)
            break

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        train_pred = model(dataset.train_x).squeeze(-1).detach().cpu().tolist()
        val_pred = model(dataset.val_x).squeeze(-1).detach().cpu().tolist()

    train_actual = dataset.train_y.detach().cpu().tolist()
    val_actual = dataset.val_y.detach().cpu().tolist()

    metrics = {
        "train_mae": mean_absolute_error(train_actual, train_pred),
        "val_mae": mean_absolute_error(val_actual, val_pred),
        "train_rmse": root_mean_squared_error(train_actual, train_pred),
        "val_rmse": root_mean_squared_error(val_actual, val_pred),
        "val_r2": _r2_score(val_actual, val_pred),
        "train_r2": _r2_score(train_actual, train_pred),
        "val_spearman": spearman_rank_correlation(val_actual, val_pred),
        "best_epoch": best_epoch,
        "final_lr": float(optimizer.param_groups[0]["lr"]),
    }
    return model, metrics


def _r2_score(actual: list[float], predicted: list[float]) -> float | None:
    if not actual or len(actual) != len(predicted) or len(actual) < 2:
        return None
    mean_actual = sum(actual) / float(len(actual))
    ss_res = sum((y - yhat) ** 2 for y, yhat in zip(actual, predicted))
    ss_tot = sum((y - mean_actual) ** 2 for y in actual)
    if ss_tot < 1e-12:
        return None
    return 1.0 - (ss_res / ss_tot)


def _classification_accuracy(y_true: list[float], y_prob: list[float]) -> float | None:
    if not y_true or len(y_true) != len(y_prob):
        return None
    hits = sum(
        1 for target, prob in zip(y_true, y_prob)
        if (float(prob) >= 0.5) == (float(target) >= 0.5)
    )
    return float(hits) / float(len(y_true))


def _expected_calibration_error(
    y_true: list[float],
    y_prob: list[float],
    n_bins: int = 10,
) -> float | None:
    if not y_true or len(y_true) != len(y_prob):
        return None
    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for target, prob in zip(y_true, y_prob):
        idx = min(int(float(prob) * n_bins), n_bins - 1)
        bins[idx].append((float(target), float(prob)))
    total = float(len(y_true))
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        avg_conf = sum(p for _, p in bucket) / float(len(bucket))
        avg_acc = sum(1.0 if t >= 0.5 else 0.0 for t, _ in bucket) / float(len(bucket))
        ece += (float(len(bucket)) / total) * abs(avg_acc - avg_conf)
    return ece


def _train_classification_model(
    dataset: _PreparedDataset,
    *,
    epochs: int = 320,
    learning_rate: float = 0.003,
) -> tuple[Any, dict[str, Any]]:
    _require_torch()
    model = _ShadowNet(input_dim=len(dataset.feature_order))
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(learning_rate), weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=_LR_SCHEDULER_FACTOR,
        patience=_LR_SCHEDULER_PATIENCE, min_lr=_MIN_LR,
    )
    loss_fn = nn.BCEWithLogitsLoss()

    best_state = None
    best_val_loss = float("inf")
    best_epoch = 0
    epochs_without_improvement = 0

    for epoch in range(max(60, int(epochs))):
        model.train()
        optimizer.zero_grad()
        logits = model(dataset.train_x).squeeze(-1)
        loss = loss_fn(logits, dataset.train_y)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), _GRAD_CLIP_NORM)
        optimizer.step()

        model.eval()
        with torch.no_grad():
            val_logits = model(dataset.val_x).squeeze(-1)
            val_loss = float(loss_fn(val_logits, dataset.val_y).item())

        scheduler.step(val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_epoch = epoch
            epochs_without_improvement = 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            epochs_without_improvement += 1

        if epochs_without_improvement >= _EARLY_STOPPING_PATIENCE and epoch >= 60:
            logger.info("Classification early stop at epoch %d (best=%d)", epoch, best_epoch)
            break

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        train_probs = torch.sigmoid(model(dataset.train_x).squeeze(-1)).detach().cpu().tolist()
        val_probs = torch.sigmoid(model(dataset.val_x).squeeze(-1)).detach().cpu().tolist()

    train_actual = dataset.train_y.detach().cpu().tolist()
    val_actual = dataset.val_y.detach().cpu().tolist()

    metrics = {
        "train_logloss": binary_log_loss(train_actual, train_probs),
        "val_logloss": binary_log_loss(val_actual, val_probs),
        "train_brier": brier_score(train_actual, train_probs),
        "val_brier": brier_score(val_actual, val_probs),
        "train_accuracy": _classification_accuracy(train_actual, train_probs),
        "val_accuracy": _classification_accuracy(val_actual, val_probs),
        "val_ece": _expected_calibration_error(val_actual, val_probs),
        "best_epoch": best_epoch,
        "final_lr": float(optimizer.param_groups[0]["lr"]),
    }
    return model, metrics


def _save_model_artifact(
    *,
    model: Any,
    model_key: str,
    model_version: str,
    feature_order: list[str],
    dataset: _PreparedDataset,
    task: str,
    train_metrics: dict[str, Any],
    params: dict[str, Any],
) -> str:
    _require_torch()
    artifact_dir = _artifact_dir()
    artifact_path = artifact_dir / f"{model_version}.pt"

    payload = {
        "model_key": model_key,
        "model_version": model_version,
        "task": task,
        "feature_order": feature_order,
        "state_dict": model.state_dict(),
        "mean": dataset.mean.detach().cpu().tolist(),
        "std": dataset.std.detach().cpu().tolist(),
        "input_dim": len(feature_order),
        "hidden_dim": 64,
        "dropout": 0.15,
        "use_batch_norm": True,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "metrics": train_metrics,
        "params": params,
    }
    # Inference workers must see either a complete previous artifact or the
    # complete new artifact, never a partially written file after a crash.
    temp_path: Path | None = None
    try:
        fd, raw_temp_path = tempfile.mkstemp(
            dir=str(artifact_dir),
            prefix=f".{model_version}.",
            suffix=".part",
        )
        temp_path = Path(raw_temp_path)
        with os.fdopen(fd, "wb") as handle:
            torch.save(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, artifact_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return artifact_path.as_posix()


def _register_model_version(
    db: Session,
    *,
    model_key: str,
    model_version: str,
    artifact_path: str,
    input_schema: dict[str, Any],
    metrics: dict[str, Any],
    params: dict[str, Any],
    activate: bool,
) -> models.MLModelRegistry:
    now = datetime.now(timezone.utc)
    existing = (
        db.query(models.MLModelRegistry)
        .filter(
            models.MLModelRegistry.model_key == model_key,
            models.MLModelRegistry.model_version == model_version,
        )
        .one_or_none()
    )

    if activate:
        (
            db.query(models.MLModelRegistry)
            .filter(models.MLModelRegistry.model_key == model_key)
            .update({models.MLModelRegistry.is_active: False}, synchronize_session=False)
        )

    if existing is None:
        existing = models.MLModelRegistry(
            model_key=model_key,
            model_version=model_version,
            framework="torch",
            artifact_path=artifact_path,
            input_schema=input_schema,
            metrics=metrics,
            params=params,
            is_active=bool(activate),
            trained_at=now,
            activated_at=(now if activate else None),
        )
        db.add(existing)
    else:
        existing.framework = "torch"
        existing.artifact_path = artifact_path
        existing.input_schema = input_schema
        existing.metrics = metrics
        existing.params = params
        existing.trained_at = now
        if activate:
            existing.is_active = True
            existing.activated_at = now

    db.commit()
    return existing


def _default_model_version(model_key: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{model_key}_torch_{stamp}"


def train_team_strength_shadow_model(
    db: Session,
    *,
    model_version: str | None = None,
    source_version: str | None = None,
    activate: bool = True,
    train_ratio: float = 0.8,
    epochs: int = 300,
    learning_rate: float = 0.003,
) -> dict[str, Any]:
    _require_torch()
    resolved_source_version = str(source_version or _feature_source_version()).strip() or "shadow_features_v1"
    resolved_version = str(model_version or _default_model_version(TEAM_STRENGTH_MODEL_KEY)).strip()

    snapshots = (
        db.query(models.MLFeatureSnapshot)
        .filter(
            models.MLFeatureSnapshot.scope == FEATURE_SCOPE_TEAM_STRENGTH,
            models.MLFeatureSnapshot.source_version == resolved_source_version,
        )
        .all()
    )

    dataset = _prepare_dataset(
        snapshots,
        feature_order=TEAM_STRENGTH_FEATURE_ORDER,
        target_key="strength_active_bps",
        train_ratio=train_ratio,
    )

    model, metrics = _train_regression_model(
        dataset,
        epochs=epochs,
        learning_rate=learning_rate,
    )
    artifact_path = _save_model_artifact(
        model=model,
        model_key=TEAM_STRENGTH_MODEL_KEY,
        model_version=resolved_version,
        feature_order=TEAM_STRENGTH_FEATURE_ORDER,
        dataset=dataset,
        task="regression",
        train_metrics=metrics,
        params={
            "train_ratio": float(train_ratio),
            "source_version": resolved_source_version,
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
        },
    )

    record = _register_model_version(
        db,
        model_key=TEAM_STRENGTH_MODEL_KEY,
        model_version=resolved_version,
        artifact_path=artifact_path,
        input_schema={"feature_order": TEAM_STRENGTH_FEATURE_ORDER},
        metrics={
            **metrics,
            "train_count": dataset.train_count,
            "val_count": dataset.val_count,
        },
        params={
            "train_ratio": float(train_ratio),
            "source_version": resolved_source_version,
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
        },
        activate=activate,
    )

    return {
        "ok": True,
        "model_key": TEAM_STRENGTH_MODEL_KEY,
        "model_version": record.model_version,
        "artifact_path": record.artifact_path,
        "is_active": bool(record.is_active),
        "train_count": dataset.train_count,
        "val_count": dataset.val_count,
        "metrics": record.metrics,
    }


def train_match_outcome_shadow_model(
    db: Session,
    *,
    model_version: str | None = None,
    source_version: str | None = None,
    activate: bool = True,
    train_ratio: float = 0.8,
    epochs: int = 320,
    learning_rate: float = 0.003,
) -> dict[str, Any]:
    _require_torch()
    resolved_source_version = str(source_version or _feature_source_version()).strip() or "shadow_features_v1"
    resolved_version = str(model_version or _default_model_version(MATCH_OUTCOME_MODEL_KEY)).strip()

    snapshots = (
        db.query(models.MLFeatureSnapshot)
        .filter(
            models.MLFeatureSnapshot.scope == FEATURE_SCOPE_MATCH_OUTCOME,
            models.MLFeatureSnapshot.source_version == resolved_source_version,
        )
        .all()
    )

    dataset = _prepare_dataset(
        snapshots,
        feature_order=MATCH_OUTCOME_FEATURE_ORDER,
        target_key="red_win",
        train_ratio=train_ratio,
    )

    model, metrics = _train_classification_model(
        dataset,
        epochs=epochs,
        learning_rate=learning_rate,
    )
    artifact_path = _save_model_artifact(
        model=model,
        model_key=MATCH_OUTCOME_MODEL_KEY,
        model_version=resolved_version,
        feature_order=MATCH_OUTCOME_FEATURE_ORDER,
        dataset=dataset,
        task="classification",
        train_metrics=metrics,
        params={
            "train_ratio": float(train_ratio),
            "source_version": resolved_source_version,
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
        },
    )

    record = _register_model_version(
        db,
        model_key=MATCH_OUTCOME_MODEL_KEY,
        model_version=resolved_version,
        artifact_path=artifact_path,
        input_schema={"feature_order": MATCH_OUTCOME_FEATURE_ORDER},
        metrics={
            **metrics,
            "train_count": dataset.train_count,
            "val_count": dataset.val_count,
        },
        params={
            "train_ratio": float(train_ratio),
            "source_version": resolved_source_version,
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
        },
        activate=activate,
    )

    return {
        "ok": True,
        "model_key": MATCH_OUTCOME_MODEL_KEY,
        "model_version": record.model_version,
        "artifact_path": record.artifact_path,
        "is_active": bool(record.is_active),
        "train_count": dataset.train_count,
        "val_count": dataset.val_count,
        "metrics": record.metrics,
    }


def _normalize_auto_scout_field_name(field_name: object) -> str:
    return str(field_name or "").strip().lower()


def _auto_scout_field_scope(field_name: object) -> str:
    normalized = _normalize_auto_scout_field_name(field_name)
    return f"{FEATURE_SCOPE_AUTO_SCOUT_FIELD_PREFIX}:{normalized}"


def train_auto_scout_field_shadow_model(
    db: Session,
    *,
    field_name: str,
    model_version: str | None = None,
    source_version: str | None = None,
    activate: bool = True,
    train_ratio: float = 0.8,
    epochs: int = 260,
    learning_rate: float = 0.003,
) -> dict[str, Any]:
    _require_torch()
    normalized_field = _normalize_auto_scout_field_name(field_name)
    if not normalized_field:
        raise RuntimeError("field_name is required for auto_scout_field training")

    model_key = auto_scout_field_model_key(normalized_field)
    scope = _auto_scout_field_scope(normalized_field)
    resolved_source_version = (
        str(source_version or auto_scout_training_source_version()).strip()
        or auto_scout_training_source_version()
    )
    resolved_version = str(model_version or _default_model_version(model_key)).strip()

    snapshots = (
        db.query(models.MLFeatureSnapshot)
        .filter(
            models.MLFeatureSnapshot.scope == scope,
            models.MLFeatureSnapshot.source_version == resolved_source_version,
        )
        .all()
    )

    dataset = _prepare_dataset(
        snapshots,
        feature_order=AUTO_SCOUT_FIELD_FEATURE_ORDER,
        target_key="field_value",
        train_ratio=train_ratio,
    )

    model, metrics = _train_regression_model(
        dataset,
        epochs=epochs,
        learning_rate=learning_rate,
    )
    all_targets = dataset.train_y.detach().cpu().tolist() + dataset.val_y.detach().cpu().tolist()
    target_min = min(float(value) for value in all_targets) if all_targets else 0.0
    target_max = max(float(value) for value in all_targets) if all_targets else 0.0
    target_mean = (sum(float(value) for value in all_targets) / float(len(all_targets))) if all_targets else 0.0
    artifact_path = _save_model_artifact(
        model=model,
        model_key=model_key,
        model_version=resolved_version,
        feature_order=AUTO_SCOUT_FIELD_FEATURE_ORDER,
        dataset=dataset,
        task="regression",
        train_metrics=metrics,
        params={
            "train_ratio": float(train_ratio),
            "source_version": resolved_source_version,
            "field_name": normalized_field,
            "target_min": target_min,
            "target_max": target_max,
            "target_mean": target_mean,
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
        },
    )

    record = _register_model_version(
        db,
        model_key=model_key,
        model_version=resolved_version,
        artifact_path=artifact_path,
        input_schema={
            "feature_order": AUTO_SCOUT_FIELD_FEATURE_ORDER,
            "field_name": normalized_field,
        },
        metrics={
            **metrics,
            "train_count": dataset.train_count,
            "val_count": dataset.val_count,
            "target_min": target_min,
            "target_max": target_max,
            "target_mean": target_mean,
        },
        params={
            "train_ratio": float(train_ratio),
            "source_version": resolved_source_version,
            "field_name": normalized_field,
            "target_min": target_min,
            "target_max": target_max,
            "target_mean": target_mean,
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
        },
        activate=activate,
    )

    return {
        "ok": True,
        "model_key": model_key,
        "field_name": normalized_field,
        "model_version": record.model_version,
        "artifact_path": record.artifact_path,
        "is_active": bool(record.is_active),
        "train_count": dataset.train_count,
        "val_count": dataset.val_count,
        "metrics": record.metrics,
    }


def train_synergy_pair_shadow_model(
    db: Session,
    *,
    model_version: str | None = None,
    source_version: str | None = None,
    activate: bool = True,
    train_ratio: float = 0.8,
    epochs: int = 280,
    learning_rate: float = 0.003,
) -> dict[str, Any]:
    _require_torch()
    resolved_source_version = str(source_version or _feature_source_version()).strip() or "shadow_features_v1"
    resolved_version = str(model_version or _default_model_version(SYNERGY_PAIR_MODEL_KEY)).strip()

    snapshots = (
        db.query(models.MLFeatureSnapshot)
        .filter(
            models.MLFeatureSnapshot.scope == FEATURE_SCOPE_SYNERGY_PAIR,
            models.MLFeatureSnapshot.source_version == resolved_source_version,
        )
        .all()
    )

    dataset = _prepare_dataset(
        snapshots,
        feature_order=SYNERGY_PAIR_FEATURE_ORDER,
        target_key="synergy_points",
        train_ratio=train_ratio,
    )

    model, metrics = _train_regression_model(
        dataset,
        epochs=epochs,
        learning_rate=learning_rate,
    )
    artifact_path = _save_model_artifact(
        model=model,
        model_key=SYNERGY_PAIR_MODEL_KEY,
        model_version=resolved_version,
        feature_order=SYNERGY_PAIR_FEATURE_ORDER,
        dataset=dataset,
        task="regression",
        train_metrics=metrics,
        params={
            "train_ratio": float(train_ratio),
            "source_version": resolved_source_version,
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
        },
    )

    record = _register_model_version(
        db,
        model_key=SYNERGY_PAIR_MODEL_KEY,
        model_version=resolved_version,
        artifact_path=artifact_path,
        input_schema={"feature_order": SYNERGY_PAIR_FEATURE_ORDER},
        metrics={
            **metrics,
            "train_count": dataset.train_count,
            "val_count": dataset.val_count,
        },
        params={
            "train_ratio": float(train_ratio),
            "source_version": resolved_source_version,
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
        },
        activate=activate,
    )

    return {
        "ok": True,
        "model_key": SYNERGY_PAIR_MODEL_KEY,
        "model_version": record.model_version,
        "artifact_path": record.artifact_path,
        "is_active": bool(record.is_active),
        "train_count": dataset.train_count,
        "val_count": dataset.val_count,
        "metrics": record.metrics,
    }


def infer_synergy_pair_shadow_from_rows(
    db: Session,
    *,
    rows: list[dict[str, Any]],
    model_version: str | None = None,
) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = [row for row in rows if isinstance(row, dict)]
    if not normalized_rows:
        return {
            "ok": True,
            "model_key": SYNERGY_PAIR_MODEL_KEY,
            "model_version": None,
            "prediction_count": 0,
            "predictions": [],
        }

    record = _load_model_registry_row(
        db,
        model_key=SYNERGY_PAIR_MODEL_KEY,
        model_version=model_version,
    )
    if record is None:
        return {
            "ok": False,
            "model_key": SYNERGY_PAIR_MODEL_KEY,
            "model_version": None,
            "reason": "model_not_found",
            "prediction_count": 0,
            "predictions": [],
        }

    try:
        runtime = _load_model_runtime_cached(record)
    except Exception as exc:
        return {
            "ok": False,
            "model_key": SYNERGY_PAIR_MODEL_KEY,
            "model_version": record.model_version,
            "reason": "runtime_unavailable",
            "detail": str(exc),
            "prediction_count": 0,
            "predictions": [],
        }

    feature_rows = [
        _FeatureVectorRow(
            feature_vector={
                name: float(_safe_float(row.get(name)) or 0.0)
                for name in SYNERGY_PAIR_FEATURE_ORDER
            }
        )
        for row in normalized_rows
    ]
    predictions = _predict_batch(runtime=runtime, rows=feature_rows)

    output_rows: list[dict[str, Any]] = []
    for row, value in zip(normalized_rows, predictions):
        numeric_prediction = _safe_float(value)
        output_rows.append(
            {
                "team_key_a": str(row.get("team_key_a") or "").strip().lower() or None,
                "team_key_b": str(row.get("team_key_b") or "").strip().lower() or None,
                "event_key": str(row.get("event_key") or "").strip().lower() or None,
                "synergy_points_pred": float(numeric_prediction) if numeric_prediction is not None else None,
            }
        )

    return {
        "ok": True,
        "model_key": SYNERGY_PAIR_MODEL_KEY,
        "model_version": record.model_version,
        "prediction_count": len(output_rows),
        "predictions": output_rows,
    }


def _is_role_signal_model_key(model_key: object) -> bool:
    token = str(model_key or "").strip().lower()
    return token.startswith(f"{ROLE_SIGNAL_MODEL_PREFIX}:")


def _signal_name_from_role_model_key(model_key: object) -> str | None:
    if not _is_role_signal_model_key(model_key):
        return None
    _, raw_name = str(model_key or "").strip().split(":", 1)
    normalized = normalize_role_signal_name(raw_name)
    return normalized if normalized in ROLE_SIGNAL_NAMES else None


def train_role_signal_shadow_model(
    db: Session,
    *,
    signal_name: str,
    model_version: str | None = None,
    source_version: str | None = None,
    activate: bool = True,
    train_ratio: float = 0.8,
    epochs: int = 240,
    learning_rate: float = 0.003,
) -> dict[str, Any]:
    _require_torch()
    normalized_signal = normalize_role_signal_name(signal_name)
    if not normalized_signal or normalized_signal not in ROLE_SIGNAL_NAMES:
        raise RuntimeError(f"Invalid role signal name: {signal_name}. Must be one of {ROLE_SIGNAL_NAMES}")

    model_key = role_signal_model_key(normalized_signal)
    scope = role_signal_scope(normalized_signal)
    resolved_source_version = str(source_version or _feature_source_version()).strip() or "shadow_features_v1"
    resolved_version = str(model_version or _default_model_version(model_key)).strip()

    snapshots = (
        db.query(models.MLFeatureSnapshot)
        .filter(
            models.MLFeatureSnapshot.scope == scope,
            models.MLFeatureSnapshot.source_version == resolved_source_version,
        )
        .all()
    )

    dataset = _prepare_dataset(
        snapshots,
        feature_order=ROLE_SIGNAL_FEATURE_ORDER,
        target_key="signal_value",
        train_ratio=train_ratio,
    )

    model, metrics = _train_regression_model(
        dataset,
        epochs=epochs,
        learning_rate=learning_rate,
    )
    artifact_path = _save_model_artifact(
        model=model,
        model_key=model_key,
        model_version=resolved_version,
        feature_order=ROLE_SIGNAL_FEATURE_ORDER,
        dataset=dataset,
        task="regression",
        train_metrics=metrics,
        params={
            "train_ratio": float(train_ratio),
            "source_version": resolved_source_version,
            "signal_name": normalized_signal,
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
        },
    )

    record = _register_model_version(
        db,
        model_key=model_key,
        model_version=resolved_version,
        artifact_path=artifact_path,
        input_schema={
            "feature_order": ROLE_SIGNAL_FEATURE_ORDER,
            "signal_name": normalized_signal,
        },
        metrics={
            **metrics,
            "train_count": dataset.train_count,
            "val_count": dataset.val_count,
        },
        params={
            "train_ratio": float(train_ratio),
            "source_version": resolved_source_version,
            "signal_name": normalized_signal,
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
        },
        activate=activate,
    )

    return {
        "ok": True,
        "model_key": model_key,
        "signal_name": normalized_signal,
        "model_version": record.model_version,
        "artifact_path": record.artifact_path,
        "is_active": bool(record.is_active),
        "train_count": dataset.train_count,
        "val_count": dataset.val_count,
        "metrics": record.metrics,
    }


def infer_role_signals_shadow(
    db: Session,
    *,
    rating: models.EventTeamRating | None,
    event_year: int | None = None,
) -> dict[str, Any]:
    if rating is None:
        return {
            "ok": False,
            "reason": "missing_rating",
            "predictions": {f"{signal_name}_signal": None for signal_name in ROLE_SIGNAL_NAMES},
        }

    from app.services.ml.role_ml import build_role_signal_feature_vector

    feature_vector = build_role_signal_feature_vector(rating, event_year=event_year)
    predictions: dict[str, float | None] = {}
    any_ok = False

    for signal_name in ROLE_SIGNAL_NAMES:
        model_key = role_signal_model_key(signal_name)
        record = _load_model_registry_row(db, model_key=model_key)
        if record is None:
            predictions[f"{signal_name}_signal"] = None
            continue
        try:
            runtime = _load_model_runtime_cached(record)
        except Exception:
            predictions[f"{signal_name}_signal"] = None
            continue

        feature_rows = [_FeatureVectorRow(feature_vector=feature_vector)]
        batch = _predict_batch(runtime=runtime, rows=feature_rows)
        if batch:
            raw_value = _safe_float(batch[0])
            predictions[f"{signal_name}_signal"] = max(0.0, min(100.0, float(raw_value))) if raw_value is not None else None
            any_ok = True
        else:
            predictions[f"{signal_name}_signal"] = None

    return {
        "ok": any_ok,
        "predictions": predictions,
    }


def _preferred_model_version_from_settings(model_key: str) -> str | None:
    if model_key == TEAM_STRENGTH_MODEL_KEY:
        candidate = str(getattr(settings, "ml_shadow_team_strength_model_version", "") or "").strip()
    elif model_key == MATCH_OUTCOME_MODEL_KEY:
        candidate = str(getattr(settings, "ml_shadow_match_outcome_model_version", "") or "").strip()
    else:
        candidate = ""
    return candidate or None


def _load_model_registry_row(
    db: Session,
    *,
    model_key: str,
    model_version: str | None = None,
) -> models.MLModelRegistry | None:
    preferred_version = str(model_version or "").strip() or _preferred_model_version_from_settings(model_key)
    query = db.query(models.MLModelRegistry).filter(models.MLModelRegistry.model_key == model_key)
    if preferred_version:
        return query.filter(models.MLModelRegistry.model_version == preferred_version).one_or_none()
    active = query.filter(models.MLModelRegistry.is_active.is_(True)).order_by(models.MLModelRegistry.trained_at.desc()).first()
    if active is not None:
        return active
    return query.order_by(models.MLModelRegistry.trained_at.desc()).first()


def _load_model_runtime(record: models.MLModelRegistry) -> dict[str, Any]:
    _require_torch()
    artifact_path = _resolve_artifact_path(record.artifact_path)

    load_kwargs: dict[str, Any] = {"map_location": "cpu"}
    try:
        if "weights_only" in inspect.signature(torch.load).parameters:
            load_kwargs["weights_only"] = True
    except (TypeError, ValueError):
        pass
    payload = torch.load(artifact_path.as_posix(), **load_kwargs)  # nosec B614
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid shadow model artifact payload: {artifact_path}")

    feature_order = payload.get("feature_order")
    if not isinstance(feature_order, list) or not feature_order:
        raise RuntimeError(f"Invalid feature order in artifact: {artifact_path}")

    input_dim = int(payload.get("input_dim") or len(feature_order))
    hidden_dim = int(payload.get("hidden_dim") or 64)
    dropout = float(payload.get("dropout") or 0.15)
    use_batch_norm = bool(payload.get("use_batch_norm", True))
    model = _ShadowNet(
        input_dim=input_dim,
        hidden_dim=hidden_dim,
        dropout=dropout,
        use_batch_norm=use_batch_norm,
    )
    state_dict = payload.get("state_dict")
    if not isinstance(state_dict, dict):
        raise RuntimeError(f"Invalid state_dict in artifact: {artifact_path}")
    model.load_state_dict(state_dict)
    model.eval()

    mean = payload.get("mean")
    std = payload.get("std")
    if not isinstance(mean, list) or not isinstance(std, list):
        raise RuntimeError(f"Invalid normalization vectors in artifact: {artifact_path}")

    return {
        "model": model,
        "task": str(payload.get("task") or "regression"),
        "feature_order": [str(name) for name in feature_order],
        "mean": torch.tensor([float(value) for value in mean], dtype=torch.float32),
        "std": torch.tensor([max(1e-6, float(value)) for value in std], dtype=torch.float32),
    }


def _load_model_runtime_cached(record: models.MLModelRegistry) -> dict[str, Any]:
    cache_key = f"{record.model_key}:{record.model_version}:{record.artifact_path}"
    cached = _RUNTIME_CACHE.get(cache_key)
    if cached is not None:
        return cached
    runtime = _load_model_runtime(record)
    _RUNTIME_CACHE[cache_key] = runtime
    if len(_RUNTIME_CACHE) > 16:
        stale_key = next(iter(_RUNTIME_CACHE))
        if stale_key != cache_key:
            _RUNTIME_CACHE.pop(stale_key, None)
    return runtime


@dataclass
class _FeatureVectorRow:
    feature_vector: dict[str, Any]


def _predict_batch(
    *,
    runtime: dict[str, Any],
    rows: list[Any],
) -> list[float]:
    _require_torch()
    feature_order = runtime["feature_order"]
    matrix: list[list[float]] = []
    for row in rows:
        feature_vector = row.feature_vector if isinstance(row.feature_vector, dict) else {}
        matrix.append([float(_safe_float(feature_vector.get(name)) or 0.0) for name in feature_order])

    if not matrix:
        return []

    x = torch.tensor(matrix, dtype=torch.float32)
    x = (x - runtime["mean"]) / runtime["std"]
    model = runtime["model"]
    with torch.no_grad():
        logits = model(x).squeeze(-1)
        if str(runtime.get("task") or "").lower() == "classification":
            predictions = torch.sigmoid(logits)
        else:
            predictions = logits
    return [float(value) for value in predictions.detach().cpu().tolist()]


def infer_team_strength_shadow_from_rows(
    db: Session,
    *,
    rows: list[dict[str, Any]],
    model_version: str | None = None,
) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        team_key = str(row.get("team_key") or "").strip().lower()
        if not team_key:
            continue
        normalized_rows.append(row)

    if not normalized_rows:
        return {
            "ok": True,
            "model_key": TEAM_STRENGTH_MODEL_KEY,
            "model_version": None,
            "prediction_count": 0,
            "predictions": [],
        }

    record = _load_model_registry_row(
        db,
        model_key=TEAM_STRENGTH_MODEL_KEY,
        model_version=model_version,
    )
    if record is None:
        return {
            "ok": False,
            "model_key": TEAM_STRENGTH_MODEL_KEY,
            "model_version": None,
            "reason": "model_not_found",
            "prediction_count": 0,
            "predictions": [],
        }

    try:
        runtime = _load_model_runtime_cached(record)
    except Exception as exc:
        return {
            "ok": False,
            "model_key": TEAM_STRENGTH_MODEL_KEY,
            "model_version": record.model_version,
            "reason": "runtime_unavailable",
            "detail": str(exc),
            "prediction_count": 0,
            "predictions": [],
        }

    feature_rows = [
        _FeatureVectorRow(feature_vector=_team_strength_feature_vector_from_row_payload(row))
        for row in normalized_rows
    ]
    predictions = _predict_batch(runtime=runtime, rows=feature_rows)

    output_rows: list[dict[str, Any]] = []
    for row, value in zip(normalized_rows, predictions):
        numeric_prediction = _safe_float(value)
        output_rows.append(
            {
                "event_key": str(row.get("event_key") or "").strip().lower() or None,
                "team_key": str(row.get("team_key") or "").strip().lower(),
                "strength_active_bps_pred": (
                    float(numeric_prediction) if numeric_prediction is not None else None
                ),
            }
        )

    return {
        "ok": True,
        "model_key": TEAM_STRENGTH_MODEL_KEY,
        "model_version": record.model_version,
        "prediction_count": len(output_rows),
        "predictions": output_rows,
    }


def infer_match_outcome_shadow_from_rows(
    db: Session,
    *,
    rows: list[dict[str, Any]],
    model_version: str | None = None,
) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        match_key = str(row.get("match_key") or "").strip().lower()
        if not match_key:
            continue
        normalized_rows.append(row)

    if not normalized_rows:
        return {
            "ok": True,
            "model_key": MATCH_OUTCOME_MODEL_KEY,
            "model_version": None,
            "prediction_count": 0,
            "predictions": [],
        }

    record = _load_model_registry_row(
        db,
        model_key=MATCH_OUTCOME_MODEL_KEY,
        model_version=model_version,
    )
    if record is None:
        return {
            "ok": False,
            "model_key": MATCH_OUTCOME_MODEL_KEY,
            "model_version": None,
            "reason": "model_not_found",
            "prediction_count": 0,
            "predictions": [],
        }

    try:
        runtime = _load_model_runtime_cached(record)
    except Exception as exc:
        return {
            "ok": False,
            "model_key": MATCH_OUTCOME_MODEL_KEY,
            "model_version": record.model_version,
            "reason": "runtime_unavailable",
            "detail": str(exc),
            "prediction_count": 0,
            "predictions": [],
        }

    feature_rows = [
        _FeatureVectorRow(
            feature_vector={
                "red_rating_mean": float(_safe_float(row.get("red_rating_mean")) or 50.0),
                "blue_rating_mean": float(_safe_float(row.get("blue_rating_mean")) or 50.0),
                "rating_margin": float(_safe_float(row.get("rating_margin")) or 0.0),
                "red_confidence_mean": float(_safe_float(row.get("red_confidence_mean")) or 0.0),
                "blue_confidence_mean": float(_safe_float(row.get("blue_confidence_mean")) or 0.0),
                "confidence_margin": float(_safe_float(row.get("confidence_margin")) or 0.0),
                "red_synergy_points": float(_safe_float(row.get("red_synergy_points")) or 0.0),
                "blue_synergy_points": float(_safe_float(row.get("blue_synergy_points")) or 0.0),
                "synergy_margin": float(_safe_float(row.get("synergy_margin")) or 0.0),
                "red_projected_throughput": float(
                    _safe_float(row.get("red_projected_throughput")) or 0.0
                ),
                "blue_projected_throughput": float(
                    _safe_float(row.get("blue_projected_throughput")) or 0.0
                ),
                "projected_margin": float(_safe_float(row.get("projected_margin")) or 0.0),
            }
        )
        for row in normalized_rows
    ]
    predictions = _predict_batch(runtime=runtime, rows=feature_rows)

    output_rows: list[dict[str, Any]] = []
    for row, value in zip(normalized_rows, predictions):
        probability = _safe_float(value)
        probability_value = max(0.0, min(1.0, float(probability))) if probability is not None else None
        output_rows.append(
            {
                "event_key": str(row.get("event_key") or "").strip().lower() or None,
                "match_key": str(row.get("match_key") or "").strip().lower(),
                "red_win_prob": probability_value,
            }
        )

    return {
        "ok": True,
        "model_key": MATCH_OUTCOME_MODEL_KEY,
        "model_version": record.model_version,
        "prediction_count": len(output_rows),
        "predictions": output_rows,
    }


def _confidence_from_regression_metrics(
    *,
    metrics: dict[str, Any],
    params: dict[str, Any],
    feature_vector: dict[str, Any],
) -> float:
    val_mae = _safe_float(metrics.get("val_mae"))
    if val_mae is None:
        val_mae = _safe_float(metrics.get("val_rmse"))
    target_min = _safe_float(params.get("target_min"))
    target_max = _safe_float(params.get("target_max"))
    target_range = (
        max(1.0, float(target_max) - float(target_min))
        if target_min is not None and target_max is not None
        else 5.0
    )
    model_quality = 0.55 if val_mae is None else max(0.0, min(1.0, 1.0 - (float(val_mae) / target_range)))
    track_coverage = max(0.0, min(1.0, float(_safe_float(feature_vector.get("track_coverage_0_1")) or 0.0)))
    throughput_coverage = max(0.0, min(1.0, float(_safe_float(feature_vector.get("throughput_coverage_0_1")) or 0.0)))
    row_quality = (0.55 * track_coverage) + (0.45 * throughput_coverage)
    return max(0.0, min(1.0, (0.62 * model_quality) + (0.38 * row_quality)))


def infer_auto_scout_field_shadow_from_rows(
    db: Session,
    *,
    field_name: str,
    rows: list[dict[str, Any]],
    model_version: str | None = None,
) -> dict[str, Any]:
    normalized_field = _normalize_auto_scout_field_name(field_name)
    if not normalized_field:
        return {
            "ok": False,
            "model_key": None,
            "field_name": "",
            "model_version": None,
            "reason": "invalid_field_name",
            "prediction_count": 0,
            "predictions": [],
        }

    normalized_rows: list[dict[str, Any]] = [row for row in rows if isinstance(row, dict)]
    if not normalized_rows:
        return {
            "ok": True,
            "model_key": auto_scout_field_model_key(normalized_field),
            "field_name": normalized_field,
            "model_version": None,
            "prediction_count": 0,
            "predictions": [],
        }

    model_key = auto_scout_field_model_key(normalized_field)
    record = _load_model_registry_row(
        db,
        model_key=model_key,
        model_version=model_version,
    )
    if record is None:
        return {
            "ok": False,
            "model_key": model_key,
            "field_name": normalized_field,
            "model_version": None,
            "reason": "model_not_found",
            "prediction_count": 0,
            "predictions": [],
        }

    try:
        runtime = _load_model_runtime_cached(record)
    except Exception as exc:
        return {
            "ok": False,
            "model_key": model_key,
            "field_name": normalized_field,
            "model_version": record.model_version,
            "reason": "runtime_unavailable",
            "detail": str(exc),
            "prediction_count": 0,
            "predictions": [],
        }

    feature_vectors: list[dict[str, Any]] = []
    for row in normalized_rows:
        raw_feature_vector = row.get("feature_vector")
        feature_vector = raw_feature_vector if isinstance(raw_feature_vector, dict) else row
        feature_vectors.append(
            {
                name: float(_safe_float(feature_vector.get(name)) or 0.0)
                for name in AUTO_SCOUT_FIELD_FEATURE_ORDER
            }
        )

    feature_rows = [_FeatureVectorRow(feature_vector=vector) for vector in feature_vectors]
    predictions = _predict_batch(runtime=runtime, rows=feature_rows)
    metrics = record.metrics if isinstance(record.metrics, dict) else {}
    params = record.params if isinstance(record.params, dict) else {}

    output_rows: list[dict[str, Any]] = []
    for source_row, feature_vector, value in zip(normalized_rows, feature_vectors, predictions):
        numeric_prediction = _safe_float(value)
        confidence = _confidence_from_regression_metrics(
            metrics=metrics,
            params=params,
            feature_vector=feature_vector,
        )
        output_rows.append(
            {
                "event_key": str(source_row.get("event_key") or "").strip().lower() or None,
                "match_key": str(source_row.get("match_key") or "").strip().lower() or None,
                "team_key": str(source_row.get("team_key") or "").strip().lower() or None,
                "field_name": normalized_field,
                "field_value_pred": float(numeric_prediction) if numeric_prediction is not None else None,
                "confidence_0_1": float(confidence),
            }
        )

    return {
        "ok": True,
        "model_key": model_key,
        "field_name": normalized_field,
        "model_version": record.model_version,
        "prediction_count": len(output_rows),
        "predictions": output_rows,
    }


def materialize_shadow_predictions_for_event(
    db: Session,
    *,
    event_key: str,
    model_key: str = "all",
    team_strength_model_version: str | None = None,
    match_outcome_model_version: str | None = None,
    replace_existing: bool = True,
) -> dict[str, Any]:
    _require_torch()
    normalized_event_key = str(event_key or "").strip().lower()
    if not normalized_event_key:
        raise RuntimeError("event_key is required")

    model_keys: list[str]
    if model_key in {"all", "*", ""}:
        model_keys = [TEAM_STRENGTH_MODEL_KEY, MATCH_OUTCOME_MODEL_KEY]
    else:
        model_keys = [str(model_key).strip().lower()]

    output: dict[str, Any] = {
        "ok": True,
        "event_key": normalized_event_key,
        "predictions": {},
    }

    for current_key in model_keys:
        if current_key == TEAM_STRENGTH_MODEL_KEY:
            record = _load_model_registry_row(
                db,
                model_key=TEAM_STRENGTH_MODEL_KEY,
                model_version=team_strength_model_version,
            )
            scope = FEATURE_SCOPE_TEAM_STRENGTH
            target_key = "strength_active_bps_pred"
        elif current_key == MATCH_OUTCOME_MODEL_KEY:
            record = _load_model_registry_row(
                db,
                model_key=MATCH_OUTCOME_MODEL_KEY,
                model_version=match_outcome_model_version,
            )
            scope = FEATURE_SCOPE_MATCH_OUTCOME
            target_key = "red_win_prob"
        else:
            output["predictions"][current_key] = {
                "ok": False,
                "reason": "unsupported_model_key",
            }
            continue

        if record is None:
            output["predictions"][current_key] = {
                "ok": False,
                "reason": "model_not_found",
            }
            continue

        runtime = _load_model_runtime_cached(record)

        snapshots = (
            db.query(models.MLFeatureSnapshot)
            .filter(
                models.MLFeatureSnapshot.scope == scope,
                models.MLFeatureSnapshot.event_key == normalized_event_key,
            )
            .order_by(models.MLFeatureSnapshot.snapshot_key.asc())
            .all()
        )
        if not snapshots:
            output["predictions"][current_key] = {
                "ok": True,
                "model_version": record.model_version,
                "rows": 0,
                "reason": "no_snapshots",
            }
            continue

        predictions = _predict_batch(runtime=runtime, rows=snapshots)

        if replace_existing:
            db.execute(
                delete(models.MLShadowPrediction).where(
                    models.MLShadowPrediction.event_key == normalized_event_key,
                    models.MLShadowPrediction.model_key == current_key,
                    models.MLShadowPrediction.model_version == record.model_version,
                    models.MLShadowPrediction.target_key == target_key,
                )
            )

        inserts = 0
        for snapshot, predicted in zip(snapshots, predictions):
            db.add(
                models.MLShadowPrediction(
                    model_key=current_key,
                    model_version=record.model_version,
                    event_key=normalized_event_key,
                    match_key=snapshot.match_key,
                    team_key=snapshot.team_key,
                    target_key=target_key,
                    prediction_value=float(predicted),
                    prediction_json={
                        "scope": scope,
                        "snapshot_key": snapshot.snapshot_key,
                        "task": str(runtime.get("task") or ""),
                    },
                    feature_snapshot_key=snapshot.snapshot_key,
                )
            )
            inserts += 1

        output["predictions"][current_key] = {
            "ok": True,
            "model_version": record.model_version,
            "rows": inserts,
            "target_key": target_key,
            "scope": scope,
        }

    db.commit()
    return output


# ── Quality gates for auto-training ──────────────────────────────────

_REGRESSION_MAX_VAL_MAE = 1.5
_REGRESSION_MIN_VAL_R2 = -0.5
_CLASSIFICATION_MAX_VAL_BRIER = 0.35
_CLASSIFICATION_MIN_VAL_ACCURACY = 0.45


def _quality_gate_regression(metrics: dict[str, Any]) -> dict[str, Any]:
    val_mae = _safe_float(metrics.get("val_mae"))
    val_r2 = _safe_float(metrics.get("val_r2"))
    reasons: list[str] = []
    if val_mae is not None and val_mae > _REGRESSION_MAX_VAL_MAE:
        reasons.append(f"val_mae={val_mae:.4f} > {_REGRESSION_MAX_VAL_MAE}")
    if val_r2 is not None and val_r2 < _REGRESSION_MIN_VAL_R2:
        reasons.append(f"val_r2={val_r2:.4f} < {_REGRESSION_MIN_VAL_R2}")
    return {
        "passed": len(reasons) == 0,
        "reason": "; ".join(reasons) if reasons else "ok",
        "thresholds": {
            "max_val_mae": _REGRESSION_MAX_VAL_MAE,
            "min_val_r2": _REGRESSION_MIN_VAL_R2,
        },
    }


def _quality_gate_classification(metrics: dict[str, Any]) -> dict[str, Any]:
    val_brier = _safe_float(metrics.get("val_brier"))
    val_accuracy = _safe_float(metrics.get("val_accuracy"))
    reasons: list[str] = []
    if val_brier is not None and val_brier > _CLASSIFICATION_MAX_VAL_BRIER:
        reasons.append(f"val_brier={val_brier:.4f} > {_CLASSIFICATION_MAX_VAL_BRIER}")
    if val_accuracy is not None and val_accuracy < _CLASSIFICATION_MIN_VAL_ACCURACY:
        reasons.append(f"val_accuracy={val_accuracy:.4f} < {_CLASSIFICATION_MIN_VAL_ACCURACY}")
    return {
        "passed": len(reasons) == 0,
        "reason": "; ".join(reasons) if reasons else "ok",
        "thresholds": {
            "max_val_brier": _CLASSIFICATION_MAX_VAL_BRIER,
            "min_val_accuracy": _CLASSIFICATION_MIN_VAL_ACCURACY,
        },
    }


def _activate_model_version(db: Session, *, model_key: str, model_version: object) -> None:
    version_str = str(model_version or "").strip()
    if not version_str:
        return
    (
        db.query(models.MLModelRegistry)
        .filter(models.MLModelRegistry.model_key == model_key)
        .update({models.MLModelRegistry.is_active: False}, synchronize_session=False)
    )
    (
        db.query(models.MLModelRegistry)
        .filter(
            models.MLModelRegistry.model_key == model_key,
            models.MLModelRegistry.model_version == version_str,
        )
        .update(
            {
                models.MLModelRegistry.is_active: True,
                models.MLModelRegistry.activated_at: datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
    )
    db.flush()


def auto_train_shadow_models_for_event_breakdown(
    db: Session,
    *,
    event_key: str,
    limit_events: int = 30,
    source_version: str | None = None,
    activate: bool = True,
    replace_predictions: bool = True,
) -> dict[str, Any]:
    normalized_event_key = str(event_key or "").strip().lower()
    resolved_source_version = str(source_version or _feature_source_version()).strip() or "shadow_features_v1"
    current_season_only = bool(getattr(settings, "ml_shadow_auto_train_current_season_only", True))
    current_season_year = datetime.now(timezone.utc).year if current_season_only else None
    effective_source_version = resolved_source_version
    if isinstance(current_season_year, int):
        season_token = f"_season_{int(current_season_year)}"
        if season_token not in effective_source_version:
            effective_source_version = f"{effective_source_version}{season_token}"
    bounded_limit_events = max(1, min(int(limit_events or 30), 250))
    result: dict[str, Any] = {
        "triggered": True,
        "event_key": normalized_event_key,
        "source_version": effective_source_version,
        "source_version_base": resolved_source_version,
        "current_season_only": current_season_only,
        "season_year": int(current_season_year) if isinstance(current_season_year, int) else None,
        "limit_events": bounded_limit_events,
        "activate": bool(activate),
        "ok": False,
        "detail": None,
        "snapshots": None,
        "snapshot_rows_written": 0,
        "train": {
            TEAM_STRENGTH_MODEL_KEY: {"ok": False, "detail": "not_started"},
            MATCH_OUTCOME_MODEL_KEY: {"ok": False, "detail": "not_started"},
        },
        "predict": None,
    }
    if not normalized_event_key:
        result["detail"] = "event_key is required"
        return result

    try:
        snapshots = rebuild_feature_snapshots(
            db,
            event_key=None,
            limit_events=bounded_limit_events,
            season_year=current_season_year,
            replace_existing=True,
            source_version=effective_source_version,
        )
    except Exception as exc:
        result["detail"] = f"snapshot_rebuild_failed: {exc}"
        return result
    result["snapshots"] = snapshots
    team_snapshot_rows = int(((snapshots or {}).get("team_strength") or {}).get("rows_written") or 0)
    match_snapshot_rows = int(((snapshots or {}).get("match_outcome") or {}).get("rows_written") or 0)
    snapshot_rows_written = max(0, team_snapshot_rows) + max(0, match_snapshot_rows)
    result["snapshot_rows_written"] = snapshot_rows_written
    if snapshot_rows_written <= 0:
        result["detail"] = "no_snapshot_rows"
        return result

    train_results: dict[str, dict[str, Any]] = {
        TEAM_STRENGTH_MODEL_KEY: {"ok": False, "detail": "not_attempted"},
        MATCH_OUTCOME_MODEL_KEY: {"ok": False, "detail": "not_attempted"},
    }
    try:
        ts_result = train_team_strength_shadow_model(
            db,
            source_version=effective_source_version,
            activate=False,
        )
        ts_metrics = ts_result.get("metrics") or {}
        ts_gate = _quality_gate_regression(ts_metrics)
        ts_result["quality_gate"] = ts_gate
        if activate and ts_gate["passed"]:
            _activate_model_version(db, model_key=TEAM_STRENGTH_MODEL_KEY, model_version=ts_result.get("model_version"))
            ts_result["activated"] = True
        else:
            ts_result["activated"] = False
            if not ts_gate["passed"]:
                logger.warning("Team strength model failed quality gate: %s", ts_gate.get("reason"))
        train_results[TEAM_STRENGTH_MODEL_KEY] = ts_result
    except Exception as exc:
        train_results[TEAM_STRENGTH_MODEL_KEY] = {"ok": False, "detail": str(exc)}

    try:
        mo_result = train_match_outcome_shadow_model(
            db,
            source_version=effective_source_version,
            activate=False,
        )
        mo_metrics = mo_result.get("metrics") or {}
        mo_gate = _quality_gate_classification(mo_metrics)
        mo_result["quality_gate"] = mo_gate
        if activate and mo_gate["passed"]:
            _activate_model_version(db, model_key=MATCH_OUTCOME_MODEL_KEY, model_version=mo_result.get("model_version"))
            mo_result["activated"] = True
        else:
            mo_result["activated"] = False
            if not mo_gate["passed"]:
                logger.warning("Match outcome model failed quality gate: %s", mo_gate.get("reason"))
        train_results[MATCH_OUTCOME_MODEL_KEY] = mo_result
    except Exception as exc:
        train_results[MATCH_OUTCOME_MODEL_KEY] = {"ok": False, "detail": str(exc)}

    result["train"] = train_results

    try:
        prediction_payload = materialize_shadow_predictions_for_event(
            db,
            event_key=normalized_event_key,
            model_key="all",
            replace_existing=replace_predictions,
        )
    except Exception as exc:
        result["predict"] = {"ok": False, "detail": str(exc)}
        result["detail"] = "prediction_materialization_failed"
        return result
    result["predict"] = prediction_payload

    train_ok_count = sum(
        1
        for payload in train_results.values()
        if isinstance(payload, dict) and bool(payload.get("ok"))
    )
    prediction_ok_count = 0
    if isinstance(prediction_payload, dict):
        for payload in (prediction_payload.get("predictions") or {}).values():
            if isinstance(payload, dict) and bool(payload.get("ok")):
                prediction_ok_count += 1

    result["ok"] = train_ok_count > 0 and prediction_ok_count > 0
    if not result["ok"]:
        if train_ok_count <= 0:
            result["detail"] = "training_failed"
        elif prediction_ok_count <= 0:
            result["detail"] = "prediction_materialization_failed"
    return result


def get_shadow_pipeline_status(db: Session, *, event_key: str | None = None) -> dict[str, Any]:
    normalized_event_key = str(event_key or "").strip().lower()

    snapshot_query = db.query(models.MLFeatureSnapshot.scope, func.count(models.MLFeatureSnapshot.snapshot_key))
    if normalized_event_key:
        snapshot_query = snapshot_query.filter(models.MLFeatureSnapshot.event_key == normalized_event_key)
    snapshot_counts = {
        str(scope): int(count or 0)
        for scope, count in snapshot_query.group_by(models.MLFeatureSnapshot.scope).all()
    }

    prediction_query = db.query(models.MLShadowPrediction.model_key, func.count(models.MLShadowPrediction.id))
    if normalized_event_key:
        prediction_query = prediction_query.filter(models.MLShadowPrediction.event_key == normalized_event_key)
    prediction_counts = {
        str(model_key): int(count or 0)
        for model_key, count in prediction_query.group_by(models.MLShadowPrediction.model_key).all()
    }

    registry_rows = (
        db.query(models.MLModelRegistry)
        .order_by(
            models.MLModelRegistry.model_key.asc(),
            models.MLModelRegistry.is_active.desc(),
            models.MLModelRegistry.trained_at.desc(),
        )
        .all()
    )
    model_registry: list[dict[str, Any]] = []
    for row in registry_rows:
        model_registry.append(
            {
                "model_key": row.model_key,
                "model_version": row.model_version,
                "framework": row.framework,
                "is_active": bool(row.is_active),
                "trained_at": row.trained_at.isoformat() if isinstance(row.trained_at, datetime) else None,
                "activated_at": row.activated_at.isoformat() if isinstance(row.activated_at, datetime) else None,
                "artifact_path": row.artifact_path,
                "metrics": row.metrics if isinstance(row.metrics, dict) else {},
            }
        )

    return {
        "ok": True,
        "torch_available": _torch_available(),
        "ml_shadow_enabled": bool(getattr(settings, "ml_shadow_enabled", False)),
        "ml_shadow_rollout_ratio": _rollout_ratio(),
        "event_key": normalized_event_key or None,
        "feature_source_version": _feature_source_version(),
        "snapshot_counts": snapshot_counts,
        "prediction_counts": prediction_counts,
        "model_registry": model_registry,
    }
