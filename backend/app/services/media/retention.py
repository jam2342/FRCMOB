from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.db.session import SessionLocal
from app.services.utils import (
    ANALYSIS_FRAMES_ROOT,
    FRAMES_ROOT,
    MEDIA_ROOT,
    VIDEOS_ROOT,
)

logger = logging.getLogger(__name__)

CLEANUP_LOCK_KEY = "media:cleanup:lock"
CLEANUP_LOCK_TTL_SEC = 600  

def _to_gb(value: int) -> float:
    return round(float(value) / float(1024**3), 3)

def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]

def _dir_size_bytes(root: Path) -> int:
    total = 0
    for path in _iter_files(root):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total

def _cleanup_empty_dirs(root: Path) -> int:
    if not root.exists():
        return 0
    removed = 0
    for path in sorted([p for p in root.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
        try:
            path.rmdir()
            removed += 1
        except OSError:
            continue
    return removed

def _delete_path(path: Path) -> int:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return 0
    return size

def _prune_by_age(root: Path, older_than_sec: int, now_ts: float) -> tuple[int, int]:
    if older_than_sec <= 0:
        return 0, 0
    cutoff = now_ts - float(older_than_sec)
    deleted_files = 0
    deleted_bytes = 0
    for path in _iter_files(root):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        reclaimed = _delete_path(path)
        if reclaimed >= 0:
            deleted_files += 1
            deleted_bytes += reclaimed
    return deleted_files, deleted_bytes

def media_usage_snapshot() -> dict:
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    VIDEOS_ROOT.mkdir(parents=True, exist_ok=True)
    ANALYSIS_FRAMES_ROOT.mkdir(parents=True, exist_ok=True)
    FRAMES_ROOT.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(str(MEDIA_ROOT))
    videos_bytes = _dir_size_bytes(VIDEOS_ROOT)
    analysis_bytes = _dir_size_bytes(ANALYSIS_FRAMES_ROOT)
    frames_bytes = _dir_size_bytes(FRAMES_ROOT)
    total_bytes = videos_bytes + analysis_bytes + frames_bytes
    return {
        "media_root": str(MEDIA_ROOT),
        "total_bytes": total_bytes,
        "total_gb": _to_gb(total_bytes),
        "videos_bytes": videos_bytes,
        "videos_gb": _to_gb(videos_bytes),
        "analysis_frames_bytes": analysis_bytes,
        "analysis_frames_gb": _to_gb(analysis_bytes),
        "frames_bytes": frames_bytes,
        "frames_gb": _to_gb(frames_bytes),
        "disk_total_bytes": int(disk.total),
        "disk_total_gb": _to_gb(int(disk.total)),
        "disk_used_bytes": int(disk.used),
        "disk_used_gb": _to_gb(int(disk.used)),
        "disk_free_bytes": int(disk.free),
        "disk_free_gb": _to_gb(int(disk.free)),
    }

def run_media_retention(*, force: bool = False, reason: str | None = None) -> dict:
    # Run media retention cleanup with distributed locking for multi-instance safety.
    now_ts = time.time()
    interval_sec = max(60, int(settings.media_cleanup_min_interval_sec))

    if not settings.media_cleanup_enabled:
        return {
            "ok": True,
            "ran": False,
            "reason": "disabled",
            "usage": media_usage_snapshot(),
        }

    # Try to acquire distributed lock
    lock_acquired = False
    redis_client = None
    try:
        if not force:
            try:
                redis_client = redis.from_url(settings.redis_url, decode_responses=True)
                # SET with EX (expire) and NX (only if not exists) for distributed lock
                lock_acquired = redis_client.set(
                    CLEANUP_LOCK_KEY,
                    str(now_ts),
                    ex=CLEANUP_LOCK_TTL_SEC,
                    nx=True,
                )
            except Exception as e:
                logger.warning(f"Could not acquire Redis lock for media cleanup: {e}, skipping cleanup")
                return {
                    "ok": True,
                    "ran": False,
                    "reason": "lock_acquisition_failed",
                    "usage": media_usage_snapshot(),
                }

            if not lock_acquired:
                return {
                    "ok": True,
                    "ran": False,
                    "reason": "lock_held_by_other_instance",
                    "usage": media_usage_snapshot(),
                }
        else:
            lock_acquired = True  # Force doesn't require lock

        # Cleanup logic runs within lock ownership
        usage_before = media_usage_snapshot()

        video_retention_sec = max(0, int(settings.media_retention_days_videos)) * 86400
        analysis_retention_sec = max(0, int(settings.media_retention_days_analysis_frames)) * 86400
        protect_recent_sec = max(0, int(settings.media_cleanup_protect_recent_minutes)) * 60
        protected_cutoff = now_ts - float(protect_recent_sec)

        deleted_by_age_files = 0
        deleted_by_age_bytes = 0
        deleted_count, deleted_bytes = _prune_by_age(VIDEOS_ROOT, video_retention_sec, now_ts)
        deleted_by_age_files += deleted_count
        deleted_by_age_bytes += deleted_bytes
        deleted_count, deleted_bytes = _prune_by_age(ANALYSIS_FRAMES_ROOT, analysis_retention_sec, now_ts)
        deleted_by_age_files += deleted_count
        deleted_by_age_bytes += deleted_bytes

        usage_mid = media_usage_snapshot()
        total_bytes = int(usage_mid["total_bytes"])
        free_bytes = int(usage_mid["disk_free_bytes"])
        min_free_bytes = int(float(settings.media_cleanup_min_free_gb) * (1024**3))
        max_total_bytes = int(float(settings.media_cleanup_max_total_gb) * (1024**3))

        deleted_by_budget_files = 0
        deleted_by_budget_bytes = 0
        budget_roots = [VIDEOS_ROOT, ANALYSIS_FRAMES_ROOT, FRAMES_ROOT]
        budget_candidates: list[tuple[float, Path]] = []
        for root in budget_roots:
            for path in _iter_files(root):
                try:
                    mtime = float(path.stat().st_mtime)
                except OSError:
                    continue
                if mtime > protected_cutoff:
                    continue
                budget_candidates.append((mtime, path))
        budget_candidates.sort(key=lambda item: item[0])

        for _, path in budget_candidates:
            over_media_budget = total_bytes > max_total_bytes
            under_free_space = free_bytes < min_free_bytes
            if not over_media_budget and not under_free_space:
                break
            reclaimed = _delete_path(path)
            if reclaimed <= 0:
                continue
            deleted_by_budget_files += 1
            deleted_by_budget_bytes += reclaimed
            total_bytes = max(0, total_bytes - reclaimed)
            free_bytes += reclaimed

        removed_empty_dirs = 0
        removed_empty_dirs += _cleanup_empty_dirs(ANALYSIS_FRAMES_ROOT)
        removed_empty_dirs += _cleanup_empty_dirs(VIDEOS_ROOT)

        usage_after = media_usage_snapshot()
        reclaimed_bytes = int(usage_before["total_bytes"]) - int(usage_after["total_bytes"])

        return {
            "ok": True,
            "ran": True,
            "reason": reason or "unspecified",
            "deleted_by_age_files": deleted_by_age_files,
            "deleted_by_age_bytes": deleted_by_age_bytes,
            "deleted_by_budget_files": deleted_by_budget_files,
            "deleted_by_budget_bytes": deleted_by_budget_bytes,
            "removed_empty_dirs": removed_empty_dirs,
            "reclaimed_bytes": reclaimed_bytes,
            "reclaimed_gb": _to_gb(reclaimed_bytes),
            "thresholds": {
                "video_retention_days": int(settings.media_retention_days_videos),
                "analysis_retention_days": int(settings.media_retention_days_analysis_frames),
                "min_free_gb": float(settings.media_cleanup_min_free_gb),
                "max_total_gb": float(settings.media_cleanup_max_total_gb),
                "protect_recent_minutes": int(settings.media_cleanup_protect_recent_minutes),
                "min_interval_sec": interval_sec,
            },
            "usage_before": usage_before,
            "usage_after": usage_after,
        }
    finally:
        # Always release lock if acquired
        if lock_acquired and redis_client:
            try:
                redis_client.delete(CLEANUP_LOCK_KEY)
            except Exception as e:
                logger.warning(f"Could not release Redis lock: {e}")

# ── Match / event-level cleanup ─────────────────────────────────────────────

_VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov")

def _get_match_video_path(match_key: str) -> Path | None:
    if not VIDEOS_ROOT.exists():
        return None
    for ext in _VIDEO_EXTENSIONS:
        files = list(VIDEOS_ROOT.glob(f"*{match_key}*{ext}"))
        if files:
            return files[0]
    for pattern in [f"*{match_key}*", f"{match_key}*"]:
        for ext in _VIDEO_EXTENSIONS:
            files = list(VIDEOS_ROOT.glob(f"{pattern}{ext}"))
            if files:
                return files[0]
    return None

def _get_analysis_frames_dir(match_key: str) -> Path | None:
    if not ANALYSIS_FRAMES_ROOT.exists():
        return None
    potential_dir = ANALYSIS_FRAMES_ROOT / match_key
    if potential_dir.exists() and potential_dir.is_dir():
        return potential_dir
    for subdir in ANALYSIS_FRAMES_ROOT.iterdir():
        if subdir.is_dir() and match_key in subdir.name:
            return subdir
    return None

def _verify_paths_removed(paths: list[str]) -> list[str]:
    failures: list[str] = []
    for path_str in paths:
        if not path_str:
            continue
        try:
            if Path(path_str).exists():
                failures.append(path_str)
        except Exception:
            failures.append(path_str)
    return failures

def _has_findings(db: Session, match_key: str) -> bool:
    for model_cls in (models.RobotTrack, models.MatchEvent, models.TeamMatchFinding):
        row = db.execute(
            select(model_cls).where(model_cls.match_key == match_key).limit(1)
        ).first()
        if row:
            return True
    return False

def cleanup_match_media(
    match_key: str, aggressive: bool = True, require_findings: bool = True,
) -> dict:
    # Delete video / frames for a single match after analysis is done.
    db = SessionLocal()
    try:
        has_findings = _has_findings(db, match_key)
        if require_findings and not has_findings:
            logger.warning("No findings found for %s, skipping cleanup", match_key)
            return {
                "ok": False,
                "match_key": match_key,
                "reason": "no_findings_found",
                "message": "Cannot delete media without analysis findings",
                "has_findings": has_findings,
            }

        deleted_files = 0
        deleted_bytes = 0
        skipped_files: list[str] = []
        deleted_paths: list[str] = []

        if settings.storage_cleanup_delete_videos:
            video_path = _get_match_video_path(match_key)
            if video_path and video_path.exists():
                try:
                    size = video_path.stat().st_size
                    video_path.unlink()
                    deleted_files += 1
                    deleted_bytes += size
                    deleted_paths.append(str(video_path))
                except Exception as e:
                    logger.error("Failed to delete video %s: %s", video_path, e)
                    skipped_files.append(str(video_path))

        if aggressive and settings.storage_cleanup_delete_analysis_frames:
            analysis_dir = _get_analysis_frames_dir(match_key)
            if analysis_dir and analysis_dir.exists():
                try:
                    size = sum(f.stat().st_size for f in analysis_dir.rglob("*") if f.is_file())
                    shutil.rmtree(analysis_dir)
                    deleted_files += 1
                    deleted_bytes += size
                    deleted_paths.append(str(analysis_dir))
                except Exception as e:
                    logger.error("Failed to delete analysis frames %s: %s", analysis_dir, e)
                    skipped_files.append(str(analysis_dir))

        if aggressive and settings.storage_cleanup_delete_sampled_frames and FRAMES_ROOT.exists():
            for frame_file in FRAMES_ROOT.glob(f"{match_key}*.jpg"):
                try:
                    size = frame_file.stat().st_size
                    frame_file.unlink()
                    deleted_files += 1
                    deleted_bytes += size
                    deleted_paths.append(str(frame_file))
                except Exception as e:
                    logger.warning("Failed to delete frame %s: %s", frame_file, e)
                    skipped_files.append(str(frame_file))

        verification_failures = _verify_paths_removed(deleted_paths)
        ok = len(verification_failures) == 0
        if verification_failures:
            logger.warning(
                "Post-delete verification failed for %s (%s paths still present)",
                match_key, len(verification_failures),
            )
        return {
            "ok": ok,
            "match_key": match_key,
            "reason": "cleanup_complete" if ok else "cleanup_verification_failed",
            "deleted_files": deleted_files,
            "deleted_bytes": deleted_bytes,
            "deleted_gb": round(deleted_bytes / (1024**3), 3),
            "skipped_files": skipped_files,
            "verification_failures": verification_failures,
            "has_findings": has_findings,
        }
    finally:
        db.close()

def cleanup_event_media(event_key: str, aggressive: bool = True) -> dict:
    # Delete media for all matches in an event.
    db = SessionLocal()
    try:
        matches = db.execute(
            select(models.Match).where(models.Match.event_key == event_key)
        ).scalars().all()
        if not matches:
            return {
                "ok": False,
                "event_key": event_key,
                "reason": "no_matches_found",
                "message": "No matches found for this event",
            }
        total_deleted = 0
        total_bytes = 0
        skipped: list[str] = []
        for match in matches:
            result = cleanup_match_media(match.match_key, aggressive=aggressive)
            if result["ok"]:
                total_deleted += result.get("deleted_files", 0)
                total_bytes += result.get("deleted_bytes", 0)
            if result.get("skipped_files"):
                skipped.extend(result["skipped_files"])
        return {
            "ok": True,
            "event_key": event_key,
            "matches_processed": len(matches),
            "total_deleted_files": total_deleted,
            "total_deleted_bytes": total_bytes,
            "total_deleted_gb": round(total_bytes / (1024**3), 3),
            "skipped_files": skipped,
        }
    finally:
        db.close()

def cleanup_old_media(older_than_days: int = 7, aggressive: bool = True) -> dict:
    # Delete media older than *older_than_days* for completed analysis runs.
    db = SessionLocal()
    try:
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        old_runs = db.execute(
            select(models.AnalysisRun).where(models.AnalysisRun.created_at < cutoff_time)
        ).scalars().all()
        if not old_runs:
            return {
                "ok": True,
                "reason": "no_old_media_found",
                "matches_processed": 0,
                "days_threshold": older_than_days,
            }
        total_deleted = 0
        total_bytes = 0
        skipped: list[str] = []
        match_keys: set[str] = set()
        for run in old_runs:
            if run.match_key in match_keys:
                continue
            match_keys.add(run.match_key)
            result = cleanup_match_media(run.match_key, aggressive=aggressive)
            if result["ok"]:
                total_deleted += result.get("deleted_files", 0)
                total_bytes += result.get("deleted_bytes", 0)
            if result.get("skipped_files"):
                skipped.extend(result["skipped_files"])
        return {
            "ok": True,
            "reason": "old_media_cleanup",
            "matches_processed": len(match_keys),
            "total_deleted_files": total_deleted,
            "total_deleted_bytes": total_bytes,
            "total_deleted_gb": round(total_bytes / (1024**3), 3),
            "days_threshold": older_than_days,
            "skipped_files": skipped,
        }
    finally:
        db.close()

def get_storage_savings(match_key: str) -> dict:
    # Estimate space that would be reclaimed by cleaning a match's media.
    video_size = 0
    analysis_size = 0
    frames_size = 0
    video_path = _get_match_video_path(match_key)
    if video_path and video_path.exists():
        video_size = video_path.stat().st_size
    analysis_dir = _get_analysis_frames_dir(match_key)
    if analysis_dir and analysis_dir.exists():
        analysis_size = sum(f.stat().st_size for f in analysis_dir.rglob("*") if f.is_file())
    if FRAMES_ROOT.exists():
        frames_size = sum(
            f.stat().st_size for f in FRAMES_ROOT.glob(f"{match_key}*.jpg") if f.is_file()
        )
    total = video_size + analysis_size + frames_size
    return {
        "match_key": match_key,
        "video_bytes": video_size,
        "video_gb": round(video_size / (1024**3), 3),
        "analysis_frames_bytes": analysis_size,
        "analysis_frames_gb": round(analysis_size / (1024**3), 3),
        "sampled_frames_bytes": frames_size,
        "sampled_frames_gb": round(frames_size / (1024**3), 3),
        "total_bytes": total,
        "total_gb": round(total / (1024**3), 3),
    }