import logging

from fastapi import APIRouter, Depends, HTTPException
import numpy as np
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_write_access, sanitize_external_error
from app.db import models
from app.db.session import get_db
from app.services.calibration.auto import (
    AutoCalibrationError,
    auto_calibrate_from_video,
    normalize_perimeter_for_layout,
)
from app.services.calibration.homography import compute_homography
from app.services.vision.perimeter_resolver import resolve_perimeter_type_for_event_profile
from app.services.vision.video_extraction import (
    MEDIA_ROOT,
    VideoExtractionError,
    cleanup_prepared_youtube_source,
    prepare_youtube_video_source,
    extract_youtube_frame,
    media_url_for_path,
    probe_image_dimensions,
)

router = APIRouter(prefix="/calibrations", tags=["calibrations"])
logger = logging.getLogger(__name__)


class Point(BaseModel):
    x: float
    y: float


class CalibrationUpsertRequest(BaseModel):
    event_key: str
    frame_time_sec: float | None = None
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    image_points: list[Point] = Field(min_length=4)
    field_points: list[Point] = Field(min_length=4)

    @model_validator(mode="after")
    def validate_points(self):
        if len(self.image_points) != len(self.field_points):
            raise ValueError("image_points and field_points must have equal length")
        return self


class YoutubeCalibrationRequest(BaseModel):
    frame_time_sec: float = Field(ge=0)
    image_points: list[Point] = Field(min_length=4)
    field_points: list[Point] = Field(min_length=4)

    @model_validator(mode="after")
    def validate_points(self):
        if len(self.image_points) != len(self.field_points):
            raise ValueError("image_points and field_points must have equal length")
        return self


class AutoCalibrationRequest(BaseModel):
    frame_time_sec: float | None = Field(default=None, ge=0)
    sample_count: int = Field(default=18, ge=1, le=80)
    min_inliers: int = Field(default=4, ge=4, le=64)
    ransac_reproj_threshold_px: float = Field(default=3.0, gt=0.1, le=25.0)
    perimeter_type_override: str | None = None


def serialize_calibration(calibration: models.FieldCalibration):
    return {
        "id": calibration.id,
        "match_key": calibration.match_key,
        "event_key": calibration.event_key,
        "frame_time_sec": calibration.frame_time_sec,
        "image_width": calibration.image_width,
        "image_height": calibration.image_height,
        "image_points": calibration.image_points,
        "field_points": calibration.field_points,
        "homography": calibration.homography,
        "created_at": calibration.created_at,
        "updated_at": calibration.updated_at,
    }


def get_match_or_404(db: Session, match_key: str) -> models.Match:
    match = db.get(models.Match, match_key)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


def get_youtube_video_for_match(db: Session, match_key: str) -> models.MatchVideo:
    video = (
        db.query(models.MatchVideo)
        .filter(models.MatchVideo.match_key == match_key, models.MatchVideo.video_type == "youtube")
        .order_by(models.MatchVideo.id.asc())
        .first()
    )
    if video is None or not video.url:
        raise HTTPException(status_code=404, detail="No YouTube video found for this match")
    return video


def upsert_calibration_record(
    db: Session,
    match_key: str,
    event_key: str,
    frame_time_sec: float | None,
    image_width: int,
    image_height: int,
    image_points: list[Point],
    field_points: list[Point],
) -> models.FieldCalibration:
    image_pairs = [(point.x, point.y) for point in image_points]
    field_pairs = [(point.x, point.y) for point in field_points]

    try:
        homography = compute_homography(image_pairs, field_pairs)
    except (np.linalg.LinAlgError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    calibration = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key == match_key)
        .one_or_none()
    )

    if calibration is None:
        calibration = models.FieldCalibration(
            match_key=match_key,
            event_key=event_key,
            frame_time_sec=frame_time_sec,
            image_width=image_width,
            image_height=image_height,
            image_points=[point.model_dump() for point in image_points],
            field_points=[point.model_dump() for point in field_points],
            homography=homography,
        )
        db.add(calibration)
    else:
        calibration.event_key = event_key
        calibration.frame_time_sec = frame_time_sec
        calibration.image_width = image_width
        calibration.image_height = image_height
        calibration.image_points = [point.model_dump() for point in image_points]
        calibration.field_points = [point.model_dump() for point in field_points]
        calibration.homography = homography

    db.commit()
    db.refresh(calibration)
    return calibration


@router.post("/{match_key}")
def upsert_calibration(
    match_key: str,
    payload: CalibrationUpsertRequest,
    db: Session = Depends(get_db),
):
    require_write_access("Calibration upsert")
    logger.info("calibrations.upsert match=%s", match_key)
    match = get_match_or_404(db, match_key)
    if payload.event_key != match.event_key:
        raise HTTPException(status_code=400, detail="event_key must match the match event")

    calibration = upsert_calibration_record(
        db=db,
        match_key=match_key,
        event_key=payload.event_key,
        frame_time_sec=payload.frame_time_sec,
        image_width=payload.image_width,
        image_height=payload.image_height,
        image_points=payload.image_points,
        field_points=payload.field_points,
    )
    return {"ok": True, "calibration": serialize_calibration(calibration)}


@router.post("/youtube/{match_key}")
def upsert_youtube_calibration(
    match_key: str,
    payload: YoutubeCalibrationRequest,
    db: Session = Depends(get_db),
):
    require_write_access("YouTube calibration upsert")
    match = get_match_or_404(db, match_key)
    video = get_youtube_video_for_match(db, match_key)

    try:
        frame_path = extract_youtube_frame(match_key, video.url, payload.frame_time_sec)
        image_width, image_height = probe_image_dimensions(frame_path)
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=502,
            detail=sanitize_external_error(exc, default="Unable to extract calibration frame from YouTube video."),
        ) from exc

    calibration = upsert_calibration_record(
        db=db,
        match_key=match_key,
        event_key=match.event_key,
        frame_time_sec=payload.frame_time_sec,
        image_width=image_width,
        image_height=image_height,
        image_points=payload.image_points,
        field_points=payload.field_points,
    )
    frame_relative = frame_path.relative_to(MEDIA_ROOT)
    frame_url = f"/media/{frame_relative.as_posix()}"

    return {
        "ok": True,
        "video_url": video.url,
        "frame_url": frame_url,
        "calibration": serialize_calibration(calibration),
    }


@router.post("/auto/{match_key}")
def upsert_auto_calibration(
    match_key: str,
    payload: AutoCalibrationRequest,
    db: Session = Depends(get_db),
):
    require_write_access("Auto calibration")
    match = get_match_or_404(db, match_key)
    video = get_youtube_video_for_match(db, match_key)
    event_profile = db.get(models.EventProfile, match.event_key)
    resolved_perimeter_type, perimeter_source = resolve_perimeter_type_for_event_profile(event_profile)
    perimeter_type = (
        normalize_perimeter_for_layout(payload.perimeter_type_override)
        if payload.perimeter_type_override
        else resolved_perimeter_type
    )

    prepared_source = None
    try:
        prepared_source = prepare_youtube_video_source(
            match_key=match_key,
            youtube_url=str(video.url),
            force_download_refresh=False,
        )
        video_metadata = prepared_source.video_metadata
        auto_payload = auto_calibrate_from_video(
            video_path=prepared_source.video_source,
            perimeter_type=perimeter_type,
            duration_sec=float(video_metadata.get("duration_sec") or 0.0),
            sample_count=int(payload.sample_count),
            focus_time_sec=payload.frame_time_sec,
            min_inliers=int(payload.min_inliers),
            ransac_reproj_threshold_px=float(payload.ransac_reproj_threshold_px),
        )
    except VideoExtractionError as exc:
        cleanup_prepared_youtube_source(prepared_source)
        raise HTTPException(
            status_code=502,
            detail=sanitize_external_error(exc, default="Unable to prepare video for auto calibration."),
        ) from exc
    except AutoCalibrationError as exc:
        cleanup_prepared_youtube_source(prepared_source)
        raise HTTPException(
            status_code=422,
            detail=sanitize_external_error(exc, default="Auto calibration failed."),
        ) from exc

    keep_fallback_download = not bool(getattr(settings, "video_extraction_cleanup_fallback_download", True))
    local_video_url = (
        media_url_for_path(prepared_source.local_video_path)
        if (
            keep_fallback_download
            and prepared_source is not None
            and prepared_source.local_video_path is not None
        )
        else None
    )
    image_points = [Point.model_validate(item) for item in auto_payload["image_points"]]
    field_points = [Point.model_validate(item) for item in auto_payload["field_points"]]

    calibration = upsert_calibration_record(
        db=db,
        match_key=match_key,
        event_key=match.event_key,
        frame_time_sec=float(auto_payload["frame_time_sec"]),
        image_width=int(auto_payload["image_width"]),
        image_height=int(auto_payload["image_height"]),
        image_points=image_points,
        field_points=field_points,
    )

    response = {
        "ok": True,
        "mode": "auto_apriltag_homography",
        "match_key": match_key,
        "event_key": match.event_key,
        "video_url": video.url,
        "video_source_mode": (prepared_source.source_mode if prepared_source is not None else None),
        "stream_fallback_reason": (prepared_source.stream_error if prepared_source is not None else None),
        "local_video_url": local_video_url,
        "video_metadata": video_metadata,
        "perimeter_type": perimeter_type,
        "perimeter_source": perimeter_source,
        "diagnostics": auto_payload["diagnostics"],
        "calibration": serialize_calibration(calibration),
    }
    cleanup_prepared_youtube_source(prepared_source)
    return response


@router.get("/{match_key}")
def get_calibration(match_key: str, db: Session = Depends(get_db)):
    calibration = (
        db.query(models.FieldCalibration)
        .filter(models.FieldCalibration.match_key == match_key)
        .one_or_none()
    )
    if calibration is None:
        raise HTTPException(status_code=404, detail="Calibration not found")

    return {"ok": True, "calibration": serialize_calibration(calibration)}
