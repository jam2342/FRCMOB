# Robot tracking data: positional heatmaps, shift play, and on-device session sync.

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    on_device_sync_identity,
    require_admin_access,
    require_write_access,
    room_access_payload_from_request,
)
from app.db import models
from app.db.session import get_db
from app.services.analysis.runs import best_on_device_run
from app.services.auto_scout.scouting import withdraw_drafts_for_run
from app.services.analysis.runs import RUN_KIND_ON_DEVICE
# On-device source/version identifiers live in shift_play (the engine layer) so
# every authoritative consumer excludes on-device data by the same constant.
from app.services.auto_scout.shift_play import ON_DEVICE_ANALYSIS_VERSION, ON_DEVICE_SOURCE
from app.services.game_config import classify_point, load_game_config
from app.services.workspaces import (
    WORKSPACE_REVOKED_DETAIL,
    lock_workspace_actor,
    resolve_workspace_actor,
    workspace_access_token_from_request,
)

router = APIRouter(prefix="/tracks", tags=["tracks"])
logger = logging.getLogger(__name__)

# FRC field dimensions (metres) — sourced from the active season's game_config
# (field.length_m / field.width_m) so a season swap needs no code change here.
# Falls back to the FRC standard 2024–2026 perimeter if config is unavailable.
def _field_dims() -> tuple[float, float]:
    try:
        field = load_game_config().field
        return float(field.length_m), float(field.width_m)
    except Exception:
        return 16.541, 8.0693


FIELD_LENGTH_M, FIELD_WIDTH_M = _field_dims()

# ── heatmap generation ───────────────────────────────────────────────

def _build_heatmap_grid(
    field_points: list[tuple[float, float]],
    grid_cols: int,
    grid_rows: int,
    field_length: float,
    field_width: float,
    sigma: float,
) -> list[list[float]]:
    # Build a 2-D density grid from field-coordinate points using Gaussian KDE.
    #
    # Returns a row-major grid[row][col] with values normalised to [0..1].
    grid = [[0.0] * grid_cols for _ in range(grid_rows)]
    if not field_points:
        return grid

    cell_w = field_length / grid_cols
    cell_h = field_width / grid_rows
    two_sigma_sq = 2.0 * sigma * sigma

    for fx, fy in field_points:
        # Determine which cells fall within ~3σ to avoid O(n*cells) cost
        cx_center = fx / cell_w
        cy_center = fy / cell_h
        radius_cells = max(1, int(math.ceil(3.0 * sigma / min(cell_w, cell_h))))

        c_min = max(0, int(cx_center) - radius_cells)
        c_max = min(grid_cols - 1, int(cx_center) + radius_cells)
        r_min = max(0, int(cy_center) - radius_cells)
        r_max = min(grid_rows - 1, int(cy_center) + radius_cells)

        for r in range(r_min, r_max + 1):
            gy = (r + 0.5) * cell_h
            dy = fy - gy
            for c in range(c_min, c_max + 1):
                gx = (c + 0.5) * cell_w
                dx = fx - gx
                dist_sq = dx * dx + dy * dy
                grid[r][c] += math.exp(-dist_sq / two_sigma_sq)

    # Normalise to [0..1]
    peak = max(max(row) for row in grid)
    if peak > 0.0:
        for r in range(grid_rows):
            for c in range(grid_cols):
                grid[r][c] = round(grid[r][c] / peak, 4)

    return grid

@router.get("/heatmap/{team_key}")
def get_team_heatmap(
    team_key: str,
    request: Request,
    event_key: str = Query(...),
    match_key: str | None = Query(default=None),
    grid_cols: int = Query(default=48, ge=8, le=128),
    grid_rows: int = Query(default=24, ge=4, le=64),
    sigma: float = Query(default=0.6, gt=0.1, le=3.0),
    include_unreviewed: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    # Where a robot spent its time, as a normalised density grid (row-major, 0..1)
    # built from phone recordings. Only operator-accepted sessions count unless an
    # operator asks for the unreviewed ones too.
    if include_unreviewed:
        require_admin_access(request, "Unreviewed on-device tracks")
    statuses = ("accepted", "provisional") if include_unreviewed else ("accepted",)
    base = (
        db.query(models.RobotTrack)
        .join(
            models.OnDeviceSession,
            models.OnDeviceSession.analysis_run_id == models.RobotTrack.analysis_run_id,
        )
        .filter(
            models.RobotTrack.team_key == team_key,
            models.RobotTrack.event_key == event_key,
            models.RobotTrack.field_x.isnot(None),
            models.RobotTrack.field_y.isnot(None),
            models.OnDeviceSession.status.in_(statuses),
        )
    )
    if match_key:
        base = base.filter(models.RobotTrack.match_key == match_key)
    # One recording per match, the same best-quality pick Attack vs Defense uses: two
    # scouts filming one match used to be blended (and counted twice) here.
    match_keys = [row[0] for row in base.with_entities(models.RobotTrack.match_key).distinct().all() if row[0]]
    chosen_runs = []
    for key in match_keys:
        best = best_on_device_run(db, match_key=key, team_key=team_key, statuses=statuses)
        if best is not None:
            chosen_runs.append(best[0].id)
    base = base.filter(models.RobotTrack.analysis_run_id.in_(chosen_runs or [-1]))

    raw_points: list[tuple[float, float]] = [
        (float(fx), float(fy))
        for fx, fy in base.with_entities(models.RobotTrack.field_x, models.RobotTrack.field_y).all()
        if fx is not None and fy is not None
    ]
    match_count = int(
        base.with_entities(func.count(func.distinct(models.RobotTrack.match_key))).scalar() or 0
    )

    grid = _build_heatmap_grid(
        field_points=raw_points,
        grid_cols=grid_cols,
        grid_rows=grid_rows,
        field_length=FIELD_LENGTH_M,
        field_width=FIELD_WIDTH_M,
        sigma=sigma,
    )

    return {
        "ok": True,
        "team_key": team_key,
        "event_key": event_key,
        "match_key": match_key,
        "total_points": len(raw_points),
        "match_count": match_count,
        "include_unreviewed": include_unreviewed,
        "field_length_m": FIELD_LENGTH_M,
        "field_width_m": FIELD_WIDTH_M,
        "grid_cols": grid_cols,
        "grid_rows": grid_rows,
        "sigma": sigma,
        "grid": grid,
    }


@router.get("/shift-play/{team_key}")
def get_team_shift_play(
    team_key: str,
    event_key: str = Query(...),
    db: Session = Depends(get_db),
):
    # Shift-based offense/defense + attack/defense heat maps for a team at an event,
    # aggregated across the team's analyzed matches using the season shift schedule.
    from app.services.auto_scout.shift_play import summarize_team_shift_play

    return summarize_team_shift_play(
        db,
        team_key=str(team_key).strip().lower(),
        event_key=str(event_key).strip().lower(),
    )


# ── on-device session sync (offline PWA → central dataset) ────────────

# Tracks produced on-device carry only field coordinates (the floor-contact point
# after in-browser homography). They have no server-side pixel bbox/centroid, so
# those columns are stored as 0.0 — the heatmap and shift-play queries only read
# field_x/field_y/zone_key/speed_mps/time_sec, which sync intact.

# Strip control chars/newlines from client-supplied strings before they reach logs
# (log-forging defence) and bound their length.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _safe_log_token(value: Any, *, max_len: int = 120) -> str:
    return _CONTROL_CHARS_RE.sub("", str(value or ""))[:max_len]


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


class OnDeviceTrackPointRequest(BaseModel):
    time_sec: float = Field(
        validation_alias=AliasChoices("timeSec", "time_sec"),
        ge=0.0,
        le=300.0,
    )
    field_x: float = Field(validation_alias=AliasChoices("fieldX", "field_x"))
    field_y: float = Field(validation_alias=AliasChoices("fieldY", "field_y"))
    zone_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("zoneKey", "zone_key"),
        max_length=96,
    )
    speed_mps: float | None = Field(
        default=None,
        validation_alias=AliasChoices("speedMps", "speed_mps"),
        ge=0.0,
        le=12.0,
    )


class OnDevicePayloadRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    points_by_team: dict[str, list[OnDeviceTrackPointRequest]] = Field(
        validation_alias=AliasChoices("pointsByTeam", "points_by_team")
    )
    schema_version: str = Field(
        default="on_device_session_v1_legacy",
        validation_alias=AliasChoices("schemaVersion", "schema_version"),
        max_length=64,
    )
    model_version: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelVersion", "model_version"),
        max_length=128,
    )
    calibration_version: str | None = Field(
        default=None,
        validation_alias=AliasChoices("calibrationVersion", "calibration_version"),
        max_length=128,
    )
    calibration_rmse_m: float | None = Field(
        default=None,
        validation_alias=AliasChoices("calibrationRmseM", "calibration_rmse_m"),
        ge=0.0,
        le=10.0,
    )
    calibration_verified: bool = Field(
        default=False,
        validation_alias=AliasChoices("calibrationVerified", "calibration_verified"),
    )
    capture_source: Literal["camera", "video", "unknown"] = Field(
        default="unknown",
        validation_alias=AliasChoices("captureSource", "capture_source"),
    )
    pose_source: Literal["optical_flow", "static", "mixed", "unknown"] = Field(
        default="unknown",
        validation_alias=AliasChoices("poseSource", "pose_source"),
    )
    pose_fallback_ratio: float | None = Field(
        default=None,
        validation_alias=AliasChoices("poseFallbackRatio", "pose_fallback_ratio"),
        ge=0.0,
        le=1.0,
    )
    identity_confidence: float | None = Field(
        default=None,
        validation_alias=AliasChoices("identityConfidence", "identity_confidence"),
        ge=0.0,
        le=1.0,
    )
    identity_source: Literal["ocr", "manual", "mixed", "unknown"] = Field(
        default="unknown",
        validation_alias=AliasChoices("identitySource", "identity_source"),
    )
    timing_source: Literal["match_clock", "video_offset", "manual", "unknown"] = Field(
        default="unknown",
        validation_alias=AliasChoices("timingSource", "timing_source"),
    )
    capture_to_match_offset_sec: float | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "captureToMatchOffsetSec", "capture_to_match_offset_sec"
        ),
        ge=-300.0,
        le=300.0,
    )
    shift1_active_alliance: Literal["red", "blue"] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "shift1ActiveAlliance", "shift1_active_alliance"
        ),
    )
    shift1_source: str | None = Field(
        default=None,
        validation_alias=AliasChoices("shift1Source", "shift1_source"),
        max_length=96,
    )
    execution_provider: str | None = Field(
        default=None,
        validation_alias=AliasChoices("executionProvider", "execution_provider"),
        max_length=64,
    )
    sampled_frame_count: int = Field(
        default=0,
        validation_alias=AliasChoices("sampledFrameCount", "sampled_frame_count"),
        ge=0,
        le=100_000,
    )
    inference_median_ms: float | None = Field(
        default=None,
        validation_alias=AliasChoices("inferenceMedianMs", "inference_median_ms"),
        ge=0.0,
        le=60_000.0,
    )
    inference_p90_ms: float | None = Field(
        default=None,
        validation_alias=AliasChoices("inferenceP90Ms", "inference_p90_ms"),
        ge=0.0,
        le=60_000.0,
    )
    thermal_drift_pct: float | None = Field(
        default=None,
        validation_alias=AliasChoices("thermalDriftPct", "thermal_drift_pct"),
        ge=-100.0,
        le=10_000.0,
    )


def _normalize_track_point(
    raw: OnDeviceTrackPointRequest,
    *,
    field_length_m: float,
    field_width_m: float,
    match_duration_sec: float,
) -> dict[str, Any] | None:
    time_sec = _coerce_float(raw.time_sec)
    field_x = _coerce_float(raw.field_x)
    field_y = _coerce_float(raw.field_y)
    if (
        time_sec is None
        or field_x is None
        or field_y is None
        or time_sec > match_duration_sec + 5.0
        or not 0.0 <= field_x <= field_length_m
        or not 0.0 <= field_y <= field_width_m
    ):
        return None
    zone = classify_point(field_x, field_y)
    return {
        "time_sec": time_sec,
        "field_x": field_x,
        "field_y": field_y,
        "zone_key": str(zone.key) if zone is not None else None,
        "speed_mps": _coerce_float(raw.speed_mps),
    }


def _extract_points_by_team(
    payload: OnDevicePayloadRequest,
    *,
    field_length_m: float,
    field_width_m: float,
    match_duration_sec: float,
) -> dict[str, list[dict[str, Any]]]:
    raw_map = payload.points_by_team
    # Bound the payload on raw structure BEFORE normalizing, so a hostile/buggy
    # client can't make us materialize millions of points or DB rows.
    team_items = list(raw_map.items())
    if len(team_items) > settings.on_device_sync_max_teams:
        raise HTTPException(
            status_code=413,
            detail=f"too many teams in payload (max {settings.on_device_sync_max_teams})",
        )
    raw_total = sum(len(v) for _, v in team_items)
    if raw_total > settings.on_device_sync_max_total_points:
        raise HTTPException(
            status_code=413,
            detail=f"too many track points in payload (max {settings.on_device_sync_max_total_points})",
        )

    out: dict[str, list[dict[str, Any]]] = {}
    for team_key, raw_points in team_items:
        if len(raw_points) > settings.on_device_sync_max_points_per_team:
            raise HTTPException(
                status_code=413,
                detail=f"too many points for one team (max {settings.on_device_sync_max_points_per_team})",
            )
        normalized = [
            pt
            for pt in (
                _normalize_track_point(
                    p,
                    field_length_m=field_length_m,
                    field_width_m=field_width_m,
                    match_duration_sec=match_duration_sec,
                )
                for p in raw_points
            )
            if pt is not None
        ]
        if normalized:
            out[str(team_key).strip().lower()] = normalized
    return out


class OnDeviceSessionRequest(BaseModel):
    # Mirrors StoredSession from the PWA's offlineStore.ts (camelCase aliases),
    # so syncPendingSessions can POST a stored session verbatim.
    id: str = Field(min_length=1, max_length=120)
    event_key: str | None = Field(default=None, alias="eventKey")
    match_key: str | None = Field(default=None, alias="matchKey")
    created_at: float | None = Field(default=None, alias="createdAt")
    workspace_id: int | None = Field(default=None, alias="workspaceId", ge=1)
    synced: bool = False
    payload: OnDevicePayloadRequest

    model_config = {"populate_by_name": True}


class OnDeviceSessionReviewRequest(BaseModel):
    status: Literal["accepted", "rejected"]
    note: str | None = Field(default=None, max_length=1000)
    force: bool = False


class OnDeviceSessionSyncResponse(BaseModel):
    ok: bool
    match_key: str
    event_key: str
    run_id: int
    on_device_session_id: int
    session_key: str
    reused_run: bool
    status: Literal["provisional", "accepted", "rejected"]
    quality_score: float
    quality: dict[str, Any]
    shift1_active_alliance: Literal["red", "blue"] | None
    shift1_source: str | None
    team_count: int
    points_persisted: int
    points_by_team: dict[str, int]
    skipped_unknown_teams: list[str]
    shift_play: dict[str, Any] | None
    shift_play_missing_reason: str | None


class OnDeviceSessionReviewResponse(BaseModel):
    ok: bool
    on_device_session_id: int
    run_id: int
    status: Literal["accepted", "rejected"]
    quality_score: float
    forced: bool


def _opaque_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _on_device_quality(payload: OnDevicePayloadRequest) -> tuple[float, dict[str, Any]]:
    minimum = float(settings.on_device_sync_min_quality_score)
    telemetry = {
        "calibration_verified": bool(payload.calibration_verified),
        "capture_source": payload.capture_source,
        "pose_source": payload.pose_source,
        "execution_provider": payload.execution_provider,
        "sampled_frame_count": payload.sampled_frame_count,
        "inference_median_ms": payload.inference_median_ms,
        "inference_p90_ms": payload.inference_p90_ms,
        "thermal_drift_pct": payload.thermal_drift_pct,
        "capture_to_match_offset_sec": payload.capture_to_match_offset_sec,
    }
    if payload.schema_version != "on_device_session_v2":
        # Backward compatibility is ingestion-only. A legacy client did not send
        # enough provenance to establish quality, even if it happens to include a
        # subset of newer fields. It therefore requires an explicit forced review.
        return 0.0, {
            "calibration_score": 0.0,
            "pose_score": 0.0,
            "identity_score": 0.0,
            "timing_score": 0.0,
            "eligible_for_review": False,
            "minimum_quality_score": minimum,
            "legacy_payload": True,
            "acceptance_requires_force": True,
            "ratings_eligible": False,
            "training_eligible": False,
            "promotion_blocker": "legacy_or_unrecognized_schema",
            **telemetry,
        }

    # Four-tap homography fits have a mathematically exact training RMSE, so that
    # number alone is not independent accuracy evidence. Credit a scout-confirmed
    # verification overlay conservatively; non-manual calibrations can use RMSE.
    if str(payload.calibration_version or "").startswith("manual_corners"):
        calibration = 0.75 if payload.calibration_verified else 0.25
    elif payload.calibration_rmse_m is not None:
        calibration = max(0.0, 1.0 - (float(payload.calibration_rmse_m) / 1.0))
    else:
        calibration = 0.0

    fallback_ratio = float(payload.pose_fallback_ratio or 0.0)
    if payload.pose_source in {"optical_flow", "mixed"}:
        pose = 1.0 - fallback_ratio
    elif payload.pose_source == "static" and payload.capture_source == "video":
        pose = 0.9
    elif payload.pose_source == "static" and payload.capture_source == "camera":
        pose = 0.25
    else:
        pose = 0.0
    identity = float(payload.identity_confidence or 0.0)
    timing = {
        "match_clock": 1.0,
        "manual": 0.65,
        "video_offset": 0.5,
        "unknown": 0.0,
    }[payload.timing_source]
    score = round(
        max(0.0, min(1.0, 0.35 * calibration + 0.25 * pose + 0.3 * identity + 0.1 * timing)),
        4,
    )
    details = {
        "calibration_score": round(calibration, 4),
        "pose_score": round(pose, 4),
        "identity_score": round(identity, 4),
        "timing_score": round(timing, 4),
        "eligible_for_review": score >= minimum,
        "minimum_quality_score": minimum,
        "legacy_payload": False,
        "acceptance_requires_force": score < minimum,
        # Accepted phone sessions remain quarantined from both downstream paths.
        # The local browser evidence proves transport/runtime compatibility, not
        # field-coordinate or identity accuracy on a physical handheld device.
        "ratings_eligible": False,
        "training_eligible": False,
        "promotion_blocker": "real_phone_ground_truth_validation_pending",
        **telemetry,
    }
    return score, details


def _stored_shift1_active_alliance(match_key: str) -> tuple[str | None, str | None]:
    # REBUILT: the alliance that scores more fuel in auto sits out shift 1, so the
    # official score breakdown settles which hub opens first.
    from app.services.scoring.breakdown import (
        _fetch_match_payload_from_tba,
        _infer_rebuilt_shift1_active_alliance,
    )

    payload = _fetch_match_payload_from_tba(match_key)
    if not isinstance(payload, dict):
        return None, None
    alliance, source = _infer_rebuilt_shift1_active_alliance(payload)
    if alliance in {"red", "blue"}:
        return alliance, f"tba_score_breakdown:{source}"
    return None, None


@router.post("/on-device-session", response_model=OnDeviceSessionSyncResponse)
def ingest_on_device_session(
    body: OnDeviceSessionRequest, request: Request, db: Session = Depends(get_db)
):
    # Receive a finished on-device match breakdown from the offline PWA and persist
    # its per-team field tracks as RobotTrack rows under a dedicated on-device run,
    # so offline scouting enriches the central heatmap/shift-play dataset.
    #
    # Idempotent per (caller identity, client session id). Reviewed sessions are
    # immutable; provisional retries rewrite only their own on-device run.
    require_write_access("On-device session sync")
    authorized, identity = on_device_sync_identity(request)
    sender = resolve_workspace_actor(request, db)
    if sender is None and workspace_access_token_from_request(request):
        # A removed member's queued recording: say so (401), so the app asks them to
        # rejoin instead of reporting the recording as rejected.
        raise HTTPException(status_code=401, detail=WORKSPACE_REVOKED_DETAIL)
    if sender is not None:
        lock_workspace_actor(db, sender)
    workspace_id = sender.workspace_id if sender is not None else None
    if body.workspace_id is not None and body.workspace_id != workspace_id:
        raise HTTPException(
            status_code=409,
            detail="session workspace does not match the caller; switch back and retry",
        )
    if not authorized and sender is not None:
        # The PWA syncs as whichever team member recorded the run. Namespaced per
        # member, so one scout can't overwrite another's run by reusing its id.
        authorized, identity = True, f"w:{sender.workspace_id}:{sender.member.member_key}"
    if settings.on_device_sync_require_signed_token and not authorized:
        # Scout-facing, but not anonymous: requires an admin key/session, a team
        # workspace member, or a valid signed room-access token.
        raise HTTPException(
            status_code=403,
            detail="on-device sync requires a team workspace, an admin key, or a room access token",
        )

    match_key = str(body.match_key or "").strip()
    if not match_key:
        raise HTTPException(status_code=422, detail="match_key is required")

    # Lock the match row so concurrent retries for this match serialize around
    # the unique session insert and per-match cap.
    match = (
        db.query(models.Match)
        .filter(models.Match.match_key == match_key)
        .with_for_update()
        .one_or_none()
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    event_key = match.event_key  # authoritative; client-provided eventKey is ignored

    room_payload = room_access_payload_from_request(request)
    room_key: str | None = None
    if identity.startswith("r:") and room_payload is not None:
        room_key = str(room_payload.get("room_key") or "").strip().lower() or None
        room = db.get(models.ScoutingRoom, room_key) if room_key else None
        # A room token outlives removal from the team, so the sync also needs a
        # current member of the workspace that owns the room.
        if (
            room is None
            or sender is None
            or room.workspace_id != sender.workspace_id
            or bool(room.archived)
            or not room.event_key
            or str(room.event_key).strip().lower() != str(event_key).strip().lower()
        ):
            raise HTTPException(
                status_code=403,
                detail="room access token is not valid for this match's event",
            )

    match_teams = (
        db.query(models.MatchTeam).filter(models.MatchTeam.match_key == match_key).all()
    )
    if not match_teams:
        raise HTTPException(status_code=409, detail="No team links for match; re-run event ingestion")
    # canonical (FK-valid) team key + alliance, looked up case-insensitively
    teams_by_lower = {str(mt.team_key).strip().lower(): mt for mt in match_teams if mt.team_key}

    config = load_game_config()
    points_by_team = _extract_points_by_team(
        body.payload,
        field_length_m=float(config.field.length_m),
        field_width_m=float(config.field.width_m),
        match_duration_sec=float(config.phases.total_sec),
    )
    if not points_by_team:
        raise HTTPException(status_code=422, detail="payload contained no usable track points")

    skipped_unknown_teams = sorted(
        team_key for team_key in points_by_team if team_key not in teams_by_lower
    )
    points_by_team = {
        team_key: points
        for team_key, points in points_by_team.items()
        if team_key in teams_by_lower
    }
    if not points_by_team:
        raise HTTPException(
            status_code=422,
            detail="payload contained no track points for teams assigned to this match",
        )

    # Namespace the dedup key by caller identity so one scout cannot overwrite
    # another's synced run by guessing/reusing its client session id. The raw id is
    # sanitized (no control chars) and length-bounded before it touches storage/logs.
    raw_id = _safe_log_token(body.id.strip(), max_len=120)
    if not raw_id:
        raise HTTPException(status_code=422, detail="id must contain a printable character")
    principal_hash = _opaque_hash(identity)
    client_session_id_hash = _opaque_hash(raw_id)
    session_key = f"od2:{_opaque_hash(f'{principal_hash}|{client_session_id_hash}')[:32]}"
    quality_score, quality_details = _on_device_quality(body.payload)
    payload_fingerprint = _opaque_hash(
        json.dumps(body.payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )
    quality_details.update(
        {
            "client_created_at": _coerce_float(body.created_at),
            "valid_team_count": len(points_by_team),
            "valid_point_count": sum(len(points) for points in points_by_team.values()),
            "skipped_unknown_teams": skipped_unknown_teams,
            "payload_fingerprint": payload_fingerprint,
        }
    )
    shift1_active_alliance = body.payload.shift1_active_alliance
    shift1_source = body.payload.shift1_source
    if shift1_active_alliance is None:
        shift1_active_alliance, shift1_source = _stored_shift1_active_alliance(match_key)

    # Find-or-create this session's run (dedup on the namespaced client session id).
    existing_session = (
        db.query(models.OnDeviceSession)
        .filter(
            models.OnDeviceSession.principal_hash == principal_hash,
            models.OnDeviceSession.client_session_id_hash == client_session_id_hash,
        )
        .first()
    )
    reused_run = existing_session is not None
    preserve_reviewed = False
    if existing_session is not None:
        if existing_session.match_key != match_key:
            raise HTTPException(
                status_code=409,
                detail="client session id is already associated with another match",
            )
        run = existing_session.analysis_run
        if existing_session.status in {"accepted", "rejected"}:
            stored_fingerprint = (existing_session.quality_details or {}).get(
                "payload_fingerprint"
            )
            if stored_fingerprint != payload_fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="reviewed on-device session is immutable; use a new session id",
                )
            preserve_reviewed = True
            quality_score = existing_session.quality_score
            quality_details = existing_session.quality_details
        else:
            # rewrite only this on-device run's tracks; leave video-analysis rows intact
            db.query(models.RobotTrack).filter(
                models.RobotTrack.analysis_run_id == run.id
            ).delete(synchronize_session=False)
            existing_session.schema_version = body.payload.schema_version
            existing_session.model_version = body.payload.model_version
            existing_session.calibration_version = body.payload.calibration_version
            existing_session.calibration_rmse_m = body.payload.calibration_rmse_m
            existing_session.pose_fallback_ratio = body.payload.pose_fallback_ratio
            existing_session.identity_confidence = body.payload.identity_confidence
            existing_session.identity_source = body.payload.identity_source
            existing_session.timing_source = body.payload.timing_source
            existing_session.capture_to_match_offset_sec = (
                body.payload.capture_to_match_offset_sec
            )
            existing_session.shift1_active_alliance = shift1_active_alliance
            existing_session.shift1_source = shift1_source
            existing_session.quality_score = quality_score
            existing_session.quality_details = quality_details
        session_row = existing_session
    else:
        # Cap distinct on-device runs per match so a client cannot mint unbounded
        # runs by varying its session id.
        run_count = (
            db.query(func.count(models.OnDeviceSession.id))
            .filter(models.OnDeviceSession.match_key == match_key)
            .scalar()
        ) or 0
        if run_count >= settings.on_device_sync_max_runs_per_match:
            raise HTTPException(
                status_code=429,
                detail="too many on-device runs for this match; try again later",
            )
        run = models.AnalysisRun(
            match_key=match_key,
            version=ON_DEVICE_ANALYSIS_VERSION,
            run_kind=RUN_KIND_ON_DEVICE,
            status="completed",
        )
        db.add(run)
        db.flush()
        db.add(
            models.AnalysisRunContext(
                run_id=run.id,
                match_key=match_key,
                event_key=event_key,
                analysis_version=ON_DEVICE_ANALYSIS_VERSION,
                params_hash=session_key,
                calibration_id=None,
            )
        )
        session_row = models.OnDeviceSession(
            analysis_run_id=run.id,
            match_key=match_key,
            event_key=event_key,
            room_key=room_key,
            workspace_id=workspace_id,
            principal_hash=principal_hash,
            client_session_id_hash=client_session_id_hash,
            schema_version=body.payload.schema_version,
            model_version=body.payload.model_version,
            calibration_version=body.payload.calibration_version,
            calibration_rmse_m=body.payload.calibration_rmse_m,
            pose_fallback_ratio=body.payload.pose_fallback_ratio,
            identity_confidence=body.payload.identity_confidence,
            identity_source=body.payload.identity_source,
            timing_source=body.payload.timing_source,
            capture_to_match_offset_sec=body.payload.capture_to_match_offset_sec,
            shift1_active_alliance=shift1_active_alliance,
            shift1_source=shift1_source,
            quality_score=quality_score,
            quality_details=quality_details,
            status="provisional",
        )
        db.add(session_row)

    persisted = 0
    per_team_counts: dict[str, int] = {}
    track_rows: list[dict[str, Any]] = []
    for track_index, (team_lower, points) in enumerate(sorted(points_by_team.items())):
        match_team = teams_by_lower.get(team_lower)
        if match_team is None:  # guarded above; keeps the type narrowing explicit
            continue
        canonical_team_key = str(match_team.team_key)
        ordered = sorted(points, key=lambda p: p["time_sec"])
        for frame_index, point in enumerate(ordered):
            track_rows.append(
                {
                    "analysis_run_id": run.id,
                    "match_key": match_key,
                    "event_key": event_key,
                    "team_key": canonical_team_key,
                    "track_id": track_index,
                    "frame_index": frame_index,
                    "time_sec": point["time_sec"],
                    "bbox_x1": 0.0,
                    "bbox_y1": 0.0,
                    "bbox_x2": 0.0,
                    "bbox_y2": 0.0,
                    "centroid_x": 0.0,
                    "centroid_y": 0.0,
                    "field_x": point["field_x"],
                    "field_y": point["field_y"],
                    "zone_key": point["zone_key"],
                    "speed_mps": point["speed_mps"],
                    "confidence": None,
                    "source": ON_DEVICE_SOURCE,
                }
            )
        per_team_counts[canonical_team_key] = len(ordered)
        persisted += len(ordered)

    if not preserve_reviewed:
        db.bulk_insert_mappings(models.RobotTrack, track_rows)

    db.commit()
    db.refresh(session_row)

    logger.info(
        "tracks.on_device_session_synced match_key=%s event_key=%s run_id=%s session=%s "
        "team_count=%s points=%s skipped_unknown=%s",
        _safe_log_token(match_key),
        _safe_log_token(event_key),
        run.id,
        _safe_log_token(session_key),
        len(per_team_counts),
        persisted,
        len(skipped_unknown_teams),
    )

    # Best-effort confirmation: run the shift-play engine over the submitted points so
    # the client gets immediate offense/defense feedback. Never fail the sync on this.
    shift_play: dict[str, Any] | None = None
    shift_play_missing_reason: str | None = None
    try:
        from app.services.auto_scout.shift_play import (
            TrackPoint as ShiftTrackPoint,
            analyze_match_shift_play,
        )

        alliance_by_team: dict[str, str] = {}
        engine_points: dict[str, list[Any]] = {}
        for team_lower, points in points_by_team.items():
            match_team = teams_by_lower.get(team_lower)
            if match_team is None:
                continue
            alliance = str(match_team.alliance or "").strip().lower()
            if alliance not in ("red", "blue"):
                continue
            canonical_team_key = str(match_team.team_key)
            alliance_by_team[canonical_team_key] = alliance
            engine_points[canonical_team_key] = [
                ShiftTrackPoint(
                    time_sec=p["time_sec"],
                    field_x=p["field_x"],
                    field_y=p["field_y"],
                    zone_key=p["zone_key"],
                    speed_mps=p["speed_mps"],
                )
                for p in points
            ]
        if alliance_by_team and shift1_active_alliance in {"red", "blue"}:
            shift_play = analyze_match_shift_play(
                points_by_team=engine_points,
                alliance_by_team=alliance_by_team,
                shift1_active_alliance=shift1_active_alliance,
            )
        elif shift1_active_alliance not in {"red", "blue"}:
            shift_play_missing_reason = "shift1_active_alliance_unavailable"
    except Exception:
        logger.exception("tracks.on_device_shift_play_failed match_key=%s run_id=%s", match_key, run.id)
        shift_play = None
        shift_play_missing_reason = "analysis_failed"

    return {
        "ok": True,
        "match_key": match_key,
        "event_key": event_key,
        "run_id": run.id,
        "on_device_session_id": session_row.id,
        "session_key": session_key,
        "reused_run": reused_run,
        "status": session_row.status,
        "quality_score": session_row.quality_score,
        "quality": session_row.quality_details,
        "shift1_active_alliance": shift1_active_alliance,
        "shift1_source": shift1_source,
        "team_count": len(per_team_counts),
        "points_persisted": persisted,
        "points_by_team": per_team_counts,
        "skipped_unknown_teams": skipped_unknown_teams,
        "shift_play": shift_play,
        "shift_play_missing_reason": shift_play_missing_reason,
    }


@router.post(
    "/on-device-session/{session_id}/review",
    response_model=OnDeviceSessionReviewResponse,
)
def review_on_device_session(
    session_id: int,
    body: OnDeviceSessionReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin_access(request, "On-device session review")
    require_write_access("On-device session review")
    session_row = db.get(models.OnDeviceSession, session_id)
    if session_row is None:
        raise HTTPException(status_code=404, detail="On-device session not found")
    minimum = float(settings.on_device_sync_min_quality_score)
    if (
        body.status == "accepted"
        and float(session_row.quality_score or 0.0) < minimum
        and not body.force
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"session quality {float(session_row.quality_score or 0.0):.3f} "
                f"is below the acceptance threshold {minimum:.3f}"
            ),
        )
    session_row.status = body.status
    session_row.review_note = str(body.note or "").strip() or None
    session_row.reviewed_at = datetime.now(timezone.utc)
    if body.status == "rejected" and session_row.analysis_run_id is not None:
        withdraw_drafts_for_run(db, session_row.analysis_run_id)
    db.commit()
    return {
        "ok": True,
        "on_device_session_id": session_row.id,
        "run_id": session_row.analysis_run_id,
        "status": session_row.status,
        "quality_score": session_row.quality_score,
        "forced": bool(body.force),
    }


@router.get("/on-device-sessions")
def list_on_device_sessions(
    request: Request,
    event_key: str | None = Query(default=None),
    status: Literal["provisional", "accepted", "rejected"] | None = Query(
        default="provisional"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    require_admin_access(request, "On-device session review queue")
    query = db.query(models.OnDeviceSession)
    if event_key:
        query = query.filter(
            models.OnDeviceSession.event_key == str(event_key).strip().lower()
        )
    if status:
        query = query.filter(models.OnDeviceSession.status == status)
    rows = query.order_by(models.OnDeviceSession.created_at.desc()).limit(limit).all()
    return {
        "ok": True,
        "count": len(rows),
        "sessions": [
            {
                "id": row.id,
                "run_id": row.analysis_run_id,
                "event_key": row.event_key,
                "match_key": row.match_key,
                "room_key": row.room_key,
                "schema_version": row.schema_version,
                "model_version": row.model_version,
                "quality_score": row.quality_score,
                "quality": row.quality_details,
                "status": row.status,
                "capture_to_match_offset_sec": row.capture_to_match_offset_sec,
                "shift1_active_alliance": row.shift1_active_alliance,
                "shift1_source": row.shift1_source,
                "created_at": row.created_at,
                "reviewed_at": row.reviewed_at,
                "review_note": row.review_note,
            }
            for row in rows
        ],
    }
