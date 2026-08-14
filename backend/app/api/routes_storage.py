# API routes for storage cleanup management.

import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin_access, require_write_access
from app.db.session import get_db
from app.db import models
from app.services.media.storage_cleanup import (
    cleanup_event_media,
    cleanup_match_media,
    cleanup_old_media,
    get_storage_savings,
    VIDEOS_ROOT,
    ANALYSIS_FRAMES_ROOT,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storage", tags=["storage"])

@router.post("/cleanup/match/{match_key}")
def cleanup_match(match_key: str, request: Request, aggressive: bool = True) -> dict:
    # Delete footage and analysis files for a specific match after findings are confirmed.
    #
    # Args:
    # match_key: Match identifier (e.g., "2025gal_qm101")
    # aggressive: Delete analysis frames and sampled frames
    #
    # Safety: Checks that findings exist before deleting anything
    require_admin_access(request, "Match storage cleanup")
    require_write_access("Match storage cleanup")
    if not settings.storage_cleanup_enabled:
        raise HTTPException(status_code=403, detail="Storage cleanup is disabled")

    try:
        result = cleanup_match_media(match_key, aggressive=aggressive)
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result.get("message", "Cleanup failed"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Storage cleanup failed for match %s", match_key)
        raise HTTPException(status_code=500, detail="Storage cleanup failed.") from exc

@router.post("/cleanup/event/{event_key}")
def cleanup_event(event_key: str, request: Request, aggressive: bool = True) -> dict:
    # Delete footage for all matches in an event after analysis is complete.
    #
    # Args:
    # event_key: Event identifier (e.g., "2025gal")
    # aggressive: Delete analysis frames and sampled frames
    require_admin_access(request, "Event storage cleanup")
    require_write_access("Event storage cleanup")
    if not settings.storage_cleanup_enabled:
        raise HTTPException(status_code=403, detail="Storage cleanup is disabled")

    try:
        result = cleanup_event_media(event_key, aggressive=aggressive)
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result.get("message", "Cleanup failed"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Event storage cleanup failed for event %s", event_key)
        raise HTTPException(status_code=500, detail="Event storage cleanup failed.") from exc

@router.post("/cleanup/old")
def cleanup_old(request: Request, days: int = 7, aggressive: bool = True) -> dict:
    # Delete media for matches analyzed more than X days ago.
    #
    # Args:
    # days: Delete media older than this many days (default 7)
    # aggressive: Delete analysis frames and sampled frames
    require_admin_access(request, "Old media storage cleanup")
    require_write_access("Old media storage cleanup")
    if not settings.storage_cleanup_enabled:
        raise HTTPException(status_code=403, detail="Storage cleanup is disabled")

    if days < 1:
        raise HTTPException(status_code=400, detail="days must be >= 1")

    try:
        result = cleanup_old_media(older_than_days=days, aggressive=aggressive)
        return result
    except Exception as exc:
        logger.exception("Old media cleanup failed")
        raise HTTPException(status_code=500, detail="Old media cleanup failed.") from exc

@router.get("/savings/match/{match_key}")
def check_match_savings(match_key: str) -> dict:
    # Estimate storage that would be freed by deleting a match's media.
    #
    # Args:
    # match_key: Match identifier
    #
    # Returns:
    # dict with estimated sizes (video, analysis, frames, total)
    try:
        return get_storage_savings(match_key)
    except Exception as exc:
        logger.exception("Failed to calculate storage savings for match %s", match_key)
        raise HTTPException(status_code=500, detail="Failed to calculate storage savings.") from exc

@router.get("/status")
def get_storage_status(db: Session = Depends(get_db)) -> dict:
    # Get storage status and utilization metrics.
    #
    # Returns:
    # dict with:
    # - total_media_size_bytes/gb: Total video + analysis storage
    # - total_collected_media_count: Number of match videos + analysis dirs tracked
    # - cleanup_enabled: Whether automatic cleanup is active
    # - cleanup_age_threshold_days: Age threshold for scheduled cleanup
    # - sample_counts: Number of analyzed matches that could be cleaned
    try:
        # Calculate media directory sizes
        video_bytes = 0
        video_count = 0
        if VIDEOS_ROOT.exists():
            for video_file in VIDEOS_ROOT.glob("*"):
                if video_file.is_file():
                    try:
                        video_bytes += video_file.stat().st_size
                        video_count += 1
                    except OSError:
                        pass

        analysis_bytes = 0
        analysis_count = 0
        if ANALYSIS_FRAMES_ROOT.exists():
            for analysis_dir in ANALYSIS_FRAMES_ROOT.iterdir():
                if analysis_dir.is_dir():
                    analysis_count += 1
                    for file in analysis_dir.rglob("*"):
                        if file.is_file():
                            with suppress(OSError):
                                analysis_bytes += file.stat().st_size

        # Count analyzed matches
        total_analyzed = db.execute(
            select(func.count(models.AnalysisRun.id)).where(
                models.AnalysisRun.status == "completed"
            )
        ).scalar() or 0

        # Count recent completed matches (for weekly freed calculation)
        one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        recent_completed = db.execute(
            select(func.count(models.AnalysisRun.id)).where(
                models.AnalysisRun.status == "completed",
                models.AnalysisRun.created_at >= one_week_ago,
            )
        ).scalar() or 0

        total_bytes = video_bytes + analysis_bytes

        return {
            "ok": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "storage": {
                "video_bytes": video_bytes,
                "video_gb": round(video_bytes / (1024**3), 2),
                "video_files": video_count,
                "analysis_frames_bytes": analysis_bytes,
                "analysis_frames_gb": round(analysis_bytes / (1024**3), 2),
                "analysis_directories": analysis_count,
                "total_bytes": total_bytes,
                "total_gb": round(total_bytes / (1024**3), 2),
            },
            "cleanup_config": {
                "enabled": settings.storage_cleanup_enabled,
                "post_analysis": settings.storage_cleanup_post_analysis,
                "auto_trigger_age_days": settings.storage_cleanup_age_days_auto,
                "delete_videos": settings.storage_cleanup_delete_videos,
                "delete_analysis_frames": settings.storage_cleanup_delete_analysis_frames,
                "delete_sampled_frames": settings.storage_cleanup_delete_sampled_frames,
            },
            "analysis_summary": {
                "total_completed": int(total_analyzed),
                "completed_this_week": int(recent_completed),
                "estimated_cleanable_matches": int(total_analyzed),  # Rough estimate
            },
        }
    except Exception as exc:
        logger.exception("Failed to get storage status")
        raise HTTPException(status_code=500, detail="Failed to get storage status.") from exc
