# API endpoints for robot tracking data — video replayer & positional heatmaps.

from __future__ import annotations

import logging
import math
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import on_device_sync_identity
from app.db import models
from app.db.session import get_db
# On-device source/version identifiers live in shift_play (the engine layer) so
# every authoritative consumer excludes on-device data by the same constant.
from app.services.auto_scout.shift_play import ON_DEVICE_ANALYSIS_VERSION, ON_DEVICE_SOURCE
from app.services.game_config import load_game_config
from app.services.media.retention import _get_match_video_path
from app.services.vision.video_extraction import media_url_for_path

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

# ── match tracks (for the video replayer) ────────────────────────────

def _serialize_track_row(row: models.RobotTrack) -> dict[str, Any]:
    return {
        "id": row.id,
        "track_id": row.track_id,
        "team_key": row.team_key,
        "frame_index": row.frame_index,
        "time_sec": round(float(row.time_sec), 4),
        "bbox": [
            round(float(row.bbox_x1), 2),
            round(float(row.bbox_y1), 2),
            round(float(row.bbox_x2), 2),
            round(float(row.bbox_y2), 2),
        ],
        "centroid": [round(float(row.centroid_x), 2), round(float(row.centroid_y), 2)],
        "field_x": round(float(row.field_x), 4) if row.field_x is not None else None,
        "field_y": round(float(row.field_y), 4) if row.field_y is not None else None,
        "zone_key": row.zone_key,
        "speed_mps": round(float(row.speed_mps), 3) if row.speed_mps is not None else None,
        "confidence": round(float(row.confidence), 3) if row.confidence is not None else None,
    }

@router.get("/match/{match_key}")
def get_match_tracks(
    match_key: str,
    team_key: str | None = Query(default=None),
    min_time: float | None = Query(default=None, ge=0.0),
    max_time: float | None = Query(default=None, ge=0.0),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=50_000, ge=1, le=200_000),
    include_on_device: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    # Return per-frame tracking rows for a given match.
    #
    # Supports filtering by team, time window, and confidence threshold.
    # Designed to power the interactive video replayer overlay.
    match = db.get(models.Match, match_key)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    q = (
        db.query(models.RobotTrack)
        .filter(models.RobotTrack.match_key == match_key)
    )
    if not include_on_device:
        # On-device PWA tracks are unauthenticated/best-effort; never authoritative
        # in the default replayer view. Opt in explicitly to see them.
        q = q.filter(
            (models.RobotTrack.source != ON_DEVICE_SOURCE)
            | (models.RobotTrack.source.is_(None))
        )
    if team_key:
        q = q.filter(models.RobotTrack.team_key == team_key)
    if min_time is not None:
        q = q.filter(models.RobotTrack.time_sec >= min_time)
    if max_time is not None:
        q = q.filter(models.RobotTrack.time_sec <= max_time)
    if min_confidence > 0.0:
        q = q.filter(
            (models.RobotTrack.confidence >= min_confidence)
            | (models.RobotTrack.confidence.is_(None))
        )

    rows = (
        q.order_by(models.RobotTrack.time_sec.asc(), models.RobotTrack.track_id.asc())
        .limit(limit)
        .all()
    )

    # Gather video info for the match
    video = (
        db.query(models.MatchVideo)
        .filter(models.MatchVideo.match_key == match_key, models.MatchVideo.video_type == "youtube")
        .order_by(models.MatchVideo.id.asc())
        .first()
    )

    # Gather calibration
    calibration = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key == match_key)
        .one_or_none()
    )
    local_video_path = _get_match_video_path(match_key)
    local_video_url = (
        media_url_for_path(local_video_path)
        if local_video_path is not None and local_video_path.exists()
        else None
    )

    # Unique teams present in the tracks
    team_keys = sorted({str(r.team_key) for r in rows if r.team_key})

    return {
        "ok": True,
        "match_key": match_key,
        "event_key": match.event_key,
        "video_url": video.url if video else None,
        "local_video_url": local_video_url,
        "field_length_m": FIELD_LENGTH_M,
        "field_width_m": FIELD_WIDTH_M,
        "calibration": {
            "image_width": calibration.image_width,
            "image_height": calibration.image_height,
            "homography": calibration.homography,
            "image_points": calibration.image_points,
            "field_points": calibration.field_points,
            "frame_time_sec": calibration.frame_time_sec,
        } if calibration else None,
        "team_keys": team_keys,
        "total_rows": len(rows),
        "tracks": [_serialize_track_row(r) for r in rows],
    }

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
    event_key: str = Query(...),
    match_key: str | None = Query(default=None),
    grid_cols: int = Query(default=48, ge=8, le=128),
    grid_rows: int = Query(default=24, ge=4, le=64),
    sigma: float = Query(default=0.6, gt=0.1, le=3.0),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    include_on_device: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    # Generate a positional density heatmap for a team at an event.
    #
    # Returns a normalised 2-D grid (row-major, values 0..1) representing
    # where the robot spent its time on the field, computed from all
    # available RobotTrack rows with non-null field_x/field_y.
    #
    # On-device PWA tracks are unauthenticated/best-effort and excluded by default
    # so they never silently skew the canonical heatmap; opt in with include_on_device.
    on_device_clause = (
        (models.RobotTrack.source != ON_DEVICE_SOURCE)
        | (models.RobotTrack.source.is_(None))
    )
    q = (
        db.query(models.RobotTrack.field_x, models.RobotTrack.field_y)
        .filter(
            models.RobotTrack.team_key == team_key,
            models.RobotTrack.event_key == event_key,
            models.RobotTrack.field_x.isnot(None),
            models.RobotTrack.field_y.isnot(None),
        )
    )
    if not include_on_device:
        q = q.filter(on_device_clause)
    if match_key:
        q = q.filter(models.RobotTrack.match_key == match_key)
    if min_confidence > 0.0:
        q = q.filter(
            (models.RobotTrack.confidence >= min_confidence)
            | (models.RobotTrack.confidence.is_(None))
        )

    raw_points: list[tuple[float, float]] = [
        (float(fx), float(fy)) for fx, fy in q.all() if fx is not None and fy is not None
    ]

    # Count matches contributing data
    match_count_q = (
        db.query(func.count(func.distinct(models.RobotTrack.match_key)))
        .filter(
            models.RobotTrack.team_key == team_key,
            models.RobotTrack.event_key == event_key,
            models.RobotTrack.field_x.isnot(None),
        )
    )
    if not include_on_device:
        match_count_q = match_count_q.filter(on_device_clause)
    if match_key:
        match_count_q = match_count_q.filter(models.RobotTrack.match_key == match_key)
    match_count = int(match_count_q.scalar() or 0)

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
        "include_on_device": include_on_device,
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


def _valid_zone_keys() -> frozenset[str]:
    # The active season's field zone keys; anything else from a client is dropped so
    # arbitrary strings can't pollute the zone-keyed analytics. Empty set = skip the
    # check (config unavailable) rather than nulling every zone.
    try:
        return frozenset(str(z.key) for z in load_game_config().zones)
    except Exception:
        return frozenset()


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _normalize_track_point(raw: Any, valid_zones: frozenset[str]) -> dict[str, Any] | None:
    # Accept the JS TrackPoint shape (camelCase, from trackProduction.ts) and the
    # snake_case mirror (on_device.py), so the contract is robust to either caller.
    if not isinstance(raw, dict):
        return None
    time_sec = _coerce_float(raw.get("timeSec", raw.get("time_sec")))
    if time_sec is None:
        return None  # a point with no timestamp is unusable downstream
    zone_raw = raw.get("zoneKey", raw.get("zone_key"))
    zone_key = str(zone_raw) if isinstance(zone_raw, str) and zone_raw.strip() else None
    if zone_key is not None and valid_zones and zone_key not in valid_zones:
        zone_key = None  # reject unknown/arbitrary zone strings
    return {
        "time_sec": time_sec,
        "field_x": _coerce_float(raw.get("fieldX", raw.get("field_x"))),
        "field_y": _coerce_float(raw.get("fieldY", raw.get("field_y"))),
        "zone_key": zone_key,
        "speed_mps": _coerce_float(raw.get("speedMps", raw.get("speed_mps"))),
    }


def _extract_points_by_team(payload: dict[str, Any], valid_zones: frozenset[str]) -> dict[str, list[dict[str, Any]]]:
    # The session payload is the produced points-by-team map. Prefer an explicit
    # wrapper key; fall back to treating the payload itself as the map.
    raw_map = payload.get("points_by_team") or payload.get("pointsByTeam")
    if not isinstance(raw_map, dict):
        raw_map = payload if isinstance(payload, dict) else {}

    # Bound the payload on raw structure BEFORE normalizing, so a hostile/buggy
    # client can't make us materialize millions of points or DB rows.
    team_items = [(k, v) for k, v in raw_map.items() if isinstance(v, list)]
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
            pt for pt in (_normalize_track_point(p, valid_zones) for p in raw_points) if pt is not None
        ]
        if normalized:
            out[str(team_key).strip().lower()] = normalized
    return out


class OnDeviceSessionRequest(BaseModel):
    # Mirrors StoredSession from the PWA's offlineStore.ts (camelCase aliases),
    # so syncPendingSessions can POST a stored session verbatim.
    id: str | None = None
    event_key: str | None = Field(default=None, alias="eventKey")
    match_key: str | None = Field(default=None, alias="matchKey")
    created_at: float | None = Field(default=None, alias="createdAt")
    synced: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


@router.post("/on-device-session")
def ingest_on_device_session(
    body: OnDeviceSessionRequest, request: Request, db: Session = Depends(get_db)
):
    # Receive a finished on-device match breakdown from the offline PWA and persist
    # its per-team field tracks as RobotTrack rows under a dedicated on-device run,
    # so offline scouting enriches the central heatmap/shift-play dataset.
    #
    # Idempotent per (caller identity, client session id): re-syncing the same
    # session rewrites its own run's tracks and never touches the server
    # video-analysis run for the match. On-device tracks are best-effort and are
    # excluded from the authoritative heatmap/replayer views by default.
    authorized, identity = on_device_sync_identity(request)
    if settings.on_device_sync_require_signed_token and not authorized:
        # Scout-facing, but not anonymous: requires an admin key/session or a valid
        # signed room-access token so random internet clients can't write the dataset.
        raise HTTPException(
            status_code=403,
            detail="on-device sync requires a valid admin key or room access token",
        )

    match_key = str(body.match_key or "").strip()
    if not match_key:
        raise HTTPException(status_code=422, detail="match_key is required")

    match = db.get(models.Match, match_key)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    event_key = match.event_key  # authoritative; client-provided eventKey is ignored

    match_teams = (
        db.query(models.MatchTeam).filter(models.MatchTeam.match_key == match_key).all()
    )
    if not match_teams:
        raise HTTPException(status_code=409, detail="No team links for match; re-run event ingestion")
    # canonical (FK-valid) team key + alliance, looked up case-insensitively
    teams_by_lower = {str(mt.team_key).strip().lower(): mt for mt in match_teams if mt.team_key}

    points_by_team = _extract_points_by_team(body.payload, _valid_zone_keys())
    if not points_by_team:
        raise HTTPException(status_code=422, detail="payload contained no usable track points")

    # Namespace the dedup key by caller identity so one scout cannot overwrite
    # another's synced run by guessing/reusing its client session id. The raw id is
    # sanitized (no control chars) and length-bounded before it touches storage/logs.
    raw_id = _safe_log_token(str(body.id or "").strip(), max_len=120)
    if raw_id:
        session_key = f"{identity}|{raw_id}"
    else:
        session_key = f"{identity}|anon-{uuid.uuid4().hex}"  # no client id → never dedup-collides

    # Find-or-create this session's run (dedup on the namespaced client session id).
    existing = (
        db.query(models.AnalysisRun)
        .join(models.AnalysisRunContext, models.AnalysisRunContext.run_id == models.AnalysisRun.id)
        .filter(
            models.AnalysisRunContext.match_key == match_key,
            models.AnalysisRunContext.analysis_version == ON_DEVICE_ANALYSIS_VERSION,
            models.AnalysisRunContext.params_hash == session_key,
        )
        .order_by(models.AnalysisRun.id.desc())
        .first()
    )
    if existing is not None:
        run = existing
        # rewrite only this on-device run's tracks; leave video-analysis rows intact
        db.query(models.RobotTrack).filter(
            models.RobotTrack.analysis_run_id == run.id
        ).delete(synchronize_session=False)
    else:
        # Cap distinct on-device runs per match so a client cannot mint unbounded
        # runs by varying its session id.
        run_count = (
            db.query(func.count(models.AnalysisRunContext.run_id))
            .filter(
                models.AnalysisRunContext.match_key == match_key,
                models.AnalysisRunContext.analysis_version == ON_DEVICE_ANALYSIS_VERSION,
            )
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

    persisted = 0
    per_team_counts: dict[str, int] = {}
    skipped_unknown_teams: list[str] = []
    for track_index, (team_lower, points) in enumerate(sorted(points_by_team.items())):
        match_team = teams_by_lower.get(team_lower)
        if match_team is None:
            skipped_unknown_teams.append(team_lower)
            continue
        canonical_team_key = str(match_team.team_key)
        ordered = sorted(points, key=lambda p: p["time_sec"])
        for frame_index, point in enumerate(ordered):
            db.add(
                models.RobotTrack(
                    analysis_run_id=run.id,
                    match_key=match_key,
                    event_key=event_key,
                    team_key=canonical_team_key,
                    track_id=track_index,
                    frame_index=frame_index,
                    time_sec=point["time_sec"],
                    bbox_x1=0.0,
                    bbox_y1=0.0,
                    bbox_x2=0.0,
                    bbox_y2=0.0,
                    centroid_x=0.0,
                    centroid_y=0.0,
                    field_x=point["field_x"],
                    field_y=point["field_y"],
                    zone_key=point["zone_key"],
                    speed_mps=point["speed_mps"],
                    confidence=None,
                    source=ON_DEVICE_SOURCE,
                )
            )
        per_team_counts[canonical_team_key] = len(ordered)
        persisted += len(ordered)

    db.commit()

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
        if alliance_by_team:
            shift_play = analyze_match_shift_play(
                points_by_team=engine_points,
                alliance_by_team=alliance_by_team,
            )
    except Exception:
        logger.exception("tracks.on_device_shift_play_failed match_key=%s run_id=%s", match_key, run.id)
        shift_play = None

    return {
        "ok": True,
        "match_key": match_key,
        "event_key": event_key,
        "run_id": run.id,
        "session_key": session_key,
        "reused_run": existing is not None,
        "team_count": len(per_team_counts),
        "points_persisted": persisted,
        "points_by_team": per_team_counts,
        "skipped_unknown_teams": skipped_unknown_teams,
        "shift_play": shift_play,
    }
