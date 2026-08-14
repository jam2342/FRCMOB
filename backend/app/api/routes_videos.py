from __future__ import annotations

import csv
import logging
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
import yt_dlp

from app.core.security import require_write_access, request_has_admin_session, sanitize_external_error
from app.db import models
from app.db.session import get_db
from app.services.vision.video_extraction import (
    VideoExtractionError,
    download_youtube_video,
    extract_youtube_frame,
    media_url_for_path,
    probe_video_metadata,
)

router = APIRouter(prefix="/videos", tags=["videos"])
logger = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
MANUAL_VIDEOS_DIR = BACKEND_ROOT / "manual_videos"

COMP_LEVEL_LABEL: dict[str, str] = {
    "qm": "qualification",
    "ef": "eighthfinal",
    "qf": "quarterfinal",
    "sf": "semifinal",
    "f": "final",
}


def _event_season_and_code(event_key: str) -> tuple[int | None, str]:
    value = (event_key or "").strip().lower()
    match = re.fullmatch(r"(\d{4})([a-z0-9]{3,})", value)
    if not match:
        return None, value
    return int(match.group(1)), match.group(2)


def _match_search_label(match: models.Match) -> str:
    level = (match.comp_level or "").lower()
    level_label = COMP_LEVEL_LABEL.get(level, "match")
    if level == "qm":
        return f"{level_label} {match.match_number}"
    return f"{level_label} {match.set_number} match {match.match_number}"


def _youtube_url_from_id(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _extract_youtube_id(url_or_id: str) -> str | None:
    raw = (url_or_id or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
        return raw

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if "youtu.be" in host:
        candidate = parsed.path.lstrip("/").split("/", 1)[0]
        return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else None
    query = parse_qs(parsed.query)
    candidate = (query.get("v") or [None])[0]
    if candidate and re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        return candidate
    return None


def _candidate_score(
    *,
    title: str,
    season: int | None,
    event_code: str,
    event_name: str,
    match: models.Match,
    duration: int | None,
) -> int:
    value = title.lower()
    score = 0
    if event_code and event_code in value:
        score += 6
    if season is not None and str(season) in value:
        score += 2
    if "frc" in value or "first" in value:
        score += 2

    level = (match.comp_level or "").lower()
    level_label = COMP_LEVEL_LABEL.get(level, "")
    if level_label and level_label in value:
        score += 3
    if level == "qm":
        if re.search(rf"\b(qm|qual(ification)?)\s*#?\s*0*{match.match_number}\b", value):
            score += 6
    elif re.search(rf"\b{match.match_number}\b", value):
        score += 2

    for token in (event_name or "").lower().split():
        if len(token) >= 4 and token in value:
            score += 1

    if duration is not None:
        if duration < 60:
            score -= 5
        elif duration > 1800:
            score -= 5
        elif 90 <= duration <= 900:
            score += 2
    return score


def _search_youtube_for_match(
    *,
    season: int | None,
    event_code: str,
    event_name: str,
    event_key: str,
    match: models.Match,
    max_results_per_query: int,
) -> dict | None:
    search_label = _match_search_label(match)
    queries = [
        f"FRC {season or ''} {event_code} {search_label}".strip(),
        f"FIRST Robotics Competition {season or ''} {event_name} {search_label}".strip(),
        f"{event_key} {search_label}",
    ]
    queries = [query for query in queries if query]

    best: dict | None = None
    best_score = -10_000
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "socket_timeout": 8,
        "retries": 1,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for query in queries:
            info = ydl.extract_info(f"ytsearch{max_results_per_query}:{query}", download=False)
            entries = info.get("entries") if isinstance(info, dict) else None
            if not entries:
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                video_id = _extract_youtube_id(str(entry.get("id") or entry.get("url") or ""))
                if not video_id:
                    continue
                title = str(entry.get("title") or "").strip()
                duration_raw = entry.get("duration")
                duration = int(duration_raw) if isinstance(duration_raw, (int, float)) else None
                score = _candidate_score(
                    title=title,
                    season=season,
                    event_code=event_code,
                    event_name=event_name,
                    match=match,
                    duration=duration,
                )
                if score > best_score:
                    best_score = score
                    best = {
                        "video_id": video_id,
                        "url": _youtube_url_from_id(video_id),
                        "title": title,
                        "duration": duration,
                        "score": score,
                        "query": query,
                    }
    return best


def _default_manual_csv_path(event_key: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", event_key.strip().lower())
    return MANUAL_VIDEOS_DIR / f"{safe}.csv"


@router.get("/status/{match_key}")
def get_video_processing_status(
    request: Request,
    match_key: str,
    run_limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
):
    is_admin = request_has_admin_session(request)
    match = db.get(models.Match, match_key)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    runs = (
        db.query(models.AnalysisRun)
        .filter(models.AnalysisRun.match_key == match_key)
        .order_by(models.AnalysisRun.created_at.desc(), models.AnalysisRun.id.desc())
        .limit(run_limit)
        .all()
    )
    if not runs:
        return {
            "ok": True,
            "match_key": match_key,
            "event_key": match.event_key,
            "status": "not_started",
            "latest_run": None,
            "runs": [],
        }

    run_ids = [int(run.id) for run in runs]
    context_rows = (
        db.query(models.AnalysisRunContext)
        .filter(models.AnalysisRunContext.run_id.in_(run_ids))
        .all()
    )
    context_by_run = {int(row.run_id): row for row in context_rows}
    quality_rows = (
        db.query(models.AnalysisQuality)
        .filter(models.AnalysisQuality.run_id.in_(run_ids))
        .all()
    )
    quality_by_run = {int(row.run_id): row for row in quality_rows}
    finding_counts = {
        int(run_id): int(count or 0)
        for run_id, count in (
            db.query(
                models.TeamMatchFinding.analysis_run_id,
                func.count(models.TeamMatchFinding.id),
            )
            .filter(models.TeamMatchFinding.analysis_run_id.in_(run_ids))
            .group_by(models.TeamMatchFinding.analysis_run_id)
            .all()
        )
    }
    throughput_counts = {
        int(run_id): int(count or 0)
        for run_id, count in (
            db.query(
                models.TeamMatchThroughput.analysis_run_id,
                func.count(models.TeamMatchThroughput.finding_id),
            )
            .filter(models.TeamMatchThroughput.analysis_run_id.in_(run_ids))
            .group_by(models.TeamMatchThroughput.analysis_run_id)
            .all()
        )
    }

    run_payload: list[dict] = []
    for run in runs:
        context = context_by_run.get(int(run.id))
        quality = quality_by_run.get(int(run.id))
        quality_details = quality.details if quality is not None and isinstance(quality.details, dict) else {}
        quality_gate = quality_details.get("quality_gate") if isinstance(quality_details.get("quality_gate"), dict) else {}
        run_payload.append(
            {
                "run_id": int(run.id),
                "status": str(run.status or "unknown"),
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "analysis_version": context.analysis_version if context is not None else run.version,
                "params_hash": (context.params_hash if context is not None else None) if is_admin else None,
                "calibration_id": (context.calibration_id if context is not None else None) if is_admin else None,
                "quality": {
                    "overall_score_0_1": (
                        round(float(quality.overall_quality_score), 4)
                        if quality is not None and quality.overall_quality_score is not None
                        else None
                    ),
                    "tracking_score_0_1": (
                        round(float(quality.tracking_quality_score), 4)
                        if quality is not None and quality.tracking_quality_score is not None
                        else None
                    ),
                    "identity_score_0_1": (
                        round(float(quality.identity_quality_score), 4)
                        if quality is not None and quality.identity_quality_score is not None
                        else None
                    ),
                    "quality_gate": quality_gate,
                },
                "outputs": {
                    "team_findings": int(finding_counts.get(int(run.id), 0)),
                    "throughput_rows": int(throughput_counts.get(int(run.id), 0)),
                },
            }
        )

    latest = run_payload[0]
    latest_status = str(latest.get("status") or "unknown").lower()
    normalized_status = latest_status
    latest_quality_gate = (
        latest.get("quality", {}).get("quality_gate")
        if isinstance(latest.get("quality"), dict)
        else {}
    )
    if latest_status == "failed" and isinstance(latest_quality_gate, dict) and latest_quality_gate.get("rejected"):
        normalized_status = "failed_quality_gate"

    return {
        "ok": True,
        "match_key": match_key,
        "event_key": match.event_key,
        "status": normalized_status,
        "latest_run": latest,
        "runs": run_payload,
    }


@router.post("/youtube-frame/{match_key}")
def extract_frame_for_match(
    match_key: str,
    time_sec: float = Query(0.0, ge=0.0),
    db: Session = Depends(get_db),
):
    require_write_access("Video frame extraction")
    match = db.get(models.Match, match_key)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    youtube_video = (
        db.query(models.MatchVideo)
        .filter(models.MatchVideo.match_key == match_key, models.MatchVideo.video_type == "youtube")
        .order_by(models.MatchVideo.id.asc())
        .first()
    )
    if youtube_video is None or not youtube_video.url:
        raise HTTPException(status_code=404, detail="No YouTube video found for this match")

    try:
        frame_path = extract_youtube_frame(match_key, youtube_video.url, time_sec)
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=502,
            detail=sanitize_external_error(exc, default="Unable to extract frame from YouTube video."),
        ) from exc

    return {
        "ok": True,
        "match_key": match_key,
        "video_url": youtube_video.url,
        "time_sec": time_sec,
        "frame_url": media_url_for_path(frame_path),
    }


@router.post("/youtube-video/{match_key}")
def download_video_for_match(
    match_key: str,
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    require_write_access("Video download")
    match = db.get(models.Match, match_key)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    youtube_video = (
        db.query(models.MatchVideo)
        .filter(models.MatchVideo.match_key == match_key, models.MatchVideo.video_type == "youtube")
        .order_by(models.MatchVideo.id.asc())
        .first()
    )
    if youtube_video is None or not youtube_video.url:
        raise HTTPException(status_code=404, detail="No YouTube video found for this match")

    try:
        local_video_path = download_youtube_video(match_key, youtube_video.url, force=refresh)
        metadata = probe_video_metadata(local_video_path)
    except VideoExtractionError as exc:
        raise HTTPException(
            status_code=502,
            detail=sanitize_external_error(exc, default="Unable to download YouTube video."),
        ) from exc

    return {
        "ok": True,
        "match_key": match_key,
        "video_url": youtube_video.url,
        "local_video_url": media_url_for_path(local_video_path),
        "video_metadata": metadata,
    }


@router.post("/youtube-backfill/{event_key}")
def backfill_event_youtube_links(
    event_key: str,
    only_missing: bool = Query(True, description="Only search matches missing YouTube links."),
    max_matches: int = Query(200, ge=1, le=1000),
    min_score: int = Query(6, ge=0, le=50),
    max_results_per_query: int = Query(5, ge=1, le=20),
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    require_write_access("YouTube backfill")
    logger.info(
        "videos.youtube_backfill.start event=%s dry_run=%s only_missing=%s max_matches=%s",
        event_key,
        dry_run,
        only_missing,
        max_matches,
    )
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")

    matches = (
        db.query(models.Match)
        .filter(models.Match.event_key == event_key)
        .order_by(models.Match.comp_level.asc(), models.Match.set_number.asc(), models.Match.match_number.asc())
        .all()
    )
    if not matches:
        return {
            "ok": True,
            "event_key": event_key,
            "total_matches": 0,
            "processed": 0,
            "added": 0,
            "skipped_existing": 0,
            "skipped_low_score": 0,
            "errors": [],
            "results": [],
        }

    season, event_code = _event_season_and_code(event_key)
    event_name = event.name or event_key

    match_keys = [m.match_key for m in matches]
    all_youtube_videos = (
        db.query(models.MatchVideo)
        .filter(models.MatchVideo.match_key.in_(match_keys), models.MatchVideo.video_type == "youtube")
        .all()
    )
    existing_youtube_by_match: dict[str, models.MatchVideo | None] = {}
    for mv in sorted(all_youtube_videos, key=lambda v: int(v.id)):
        if mv.match_key not in existing_youtube_by_match:
            existing_youtube_by_match[mv.match_key] = mv
    existing_video_keys: set[tuple[str, str]] = {(mv.match_key, str(mv.video_key)) for mv in all_youtube_videos}

    processed = 0
    added = 0
    skipped_existing = 0
    skipped_low_score = 0
    errors: list[dict] = []
    results: list[dict] = []

    for match in matches:
        if processed >= max_matches:
            break
        existing_youtube = existing_youtube_by_match.get(match.match_key)
        if only_missing and existing_youtube is not None and existing_youtube.url:
            skipped_existing += 1
            continue

        processed += 1
        try:
            candidate = _search_youtube_for_match(
                season=season,
                event_code=event_code,
                event_name=event_name,
                event_key=event_key,
                match=match,
                max_results_per_query=max_results_per_query,
            )
        except Exception as exc:
            errors.append(
                {
                    "match_key": match.match_key,
                    "detail": sanitize_external_error(exc, default="YouTube search failed."),
                }
            )
            continue

        if candidate is None:
            results.append({"match_key": match.match_key, "status": "not_found"})
            continue
        if int(candidate.get("score") or 0) < min_score:
            skipped_low_score += 1
            results.append(
                {
                    "match_key": match.match_key,
                    "status": "low_score",
                    "score": candidate.get("score"),
                    "title": candidate.get("title"),
                    "url": candidate.get("url"),
                }
            )
            continue

        video_id = str(candidate["video_id"])
        already_present = (match.match_key, video_id) in existing_video_keys
        if not dry_run and not already_present:
            db.add(
                models.MatchVideo(
                    match_key=match.match_key,
                    video_type="youtube",
                    video_key=video_id,
                    url=str(candidate["url"]),
                )
            )
            added += 1

        results.append(
            {
                "match_key": match.match_key,
                "status": "added" if not already_present else "already_present",
                "score": candidate.get("score"),
                "title": candidate.get("title"),
                "url": candidate.get("url"),
            }
        )

    if not dry_run:
        db.commit()

    return {
        "ok": True,
        "event_key": event_key,
        "dry_run": dry_run,
        "total_matches": len(matches),
        "processed": processed,
        "added": added,
        "skipped_existing": skipped_existing,
        "skipped_low_score": skipped_low_score,
        "errors": errors,
        "results": results,
    }


@router.post("/manual-youtube-template/{event_key}")
def create_manual_youtube_template(
    event_key: str,
    overwrite: bool = Query(False),
    db: Session = Depends(get_db),
):
    require_write_access("Manual YouTube template generation")
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")

    missing_matches = (
        db.query(models.Match.match_key)
        .outerjoin(
            models.MatchVideo,
            (models.MatchVideo.match_key == models.Match.match_key)
            & (models.MatchVideo.video_type == "youtube"),
        )
        .filter(models.Match.event_key == event_key, models.MatchVideo.id.is_(None))
        .order_by(models.Match.comp_level.asc(), models.Match.set_number.asc(), models.Match.match_number.asc())
        .all()
    )
    match_keys = [row[0] for row in missing_matches]

    MANUAL_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = _default_manual_csv_path(event_key)
    if csv_path.exists() and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"Template already exists at {csv_path}. Use overwrite=true to regenerate.",
        )

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["match_key", "youtube_url"])
        writer.writeheader()
        for match_key in match_keys:
            writer.writerow({"match_key": match_key, "youtube_url": ""})

    return {
        "ok": True,
        "event_key": event_key,
        "path": str(csv_path),
        "rows": len(match_keys),
    }


@router.post("/manual-youtube-import/{event_key}")
def import_manual_youtube_links(
    event_key: str,
    csv_path: str | None = Query(None, description="Absolute or backend-relative CSV path."),
    db: Session = Depends(get_db),
):
    require_write_access("Manual YouTube import")
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")

    if csv_path:
        path = Path(csv_path)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
    else:
        path = _default_manual_csv_path(event_key)

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"CSV file not found: {path}")

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    added = 0
    skipped_blank = 0
    skipped_duplicate = 0
    invalid = []
    for idx, row in enumerate(rows, start=2):
        match_key = str(row.get("match_key") or "").strip()
        url = str(row.get("youtube_url") or row.get("url") or "").strip()
        if not match_key or not url:
            skipped_blank += 1
            continue
        match = db.get(models.Match, match_key)
        if match is None or match.event_key != event_key:
            invalid.append({"line": idx, "match_key": match_key, "reason": "unknown_match_for_event"})
            continue

        video_id = _extract_youtube_id(url)
        if not video_id:
            invalid.append({"line": idx, "match_key": match_key, "reason": "invalid_youtube_url"})
            continue

        existing = (
            db.query(models.MatchVideo)
            .filter(
                models.MatchVideo.match_key == match_key,
                models.MatchVideo.video_type == "youtube",
                models.MatchVideo.video_key == video_id,
            )
            .first()
        )
        if existing is not None:
            skipped_duplicate += 1
            continue

        db.add(
            models.MatchVideo(
                match_key=match_key,
                video_type="youtube",
                video_key=video_id,
                url=_youtube_url_from_id(video_id),
            )
        )
        added += 1

    db.commit()

    return {
        "ok": True,
        "event_key": event_key,
        "path": str(path),
        "rows": len(rows),
        "added": added,
        "skipped_blank": skipped_blank,
        "skipped_duplicate": skipped_duplicate,
        "invalid": invalid,
    }
