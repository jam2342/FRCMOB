import json
import logging
import re
import shutil
import subprocess  # nosec B404
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from app.core.config import settings
from app.services.media.retention import run_media_retention
from app.services.utils import (
    ANALYSIS_FRAMES_ROOT,
    FRAMES_ROOT,
    MEDIA_ROOT,
    VIDEOS_ROOT,
)

logger = logging.getLogger(__name__)

# Timeout constants
YOUTUBE_DOWNLOAD_TIMEOUT_SEC = max(10, int(getattr(settings, "video_extraction_youtube_timeout_sec", 60)))
FFMPEG_TIMEOUT_SEC = max(30, int(getattr(settings, "video_extraction_ffmpeg_timeout_sec", 180)))
LIVE_CLIP_TIMEOUT_BUFFER_SEC = max(
    10,
    int(getattr(settings, "video_extraction_live_clip_timeout_buffer_sec", 45)),
)

class VideoExtractionError(RuntimeError):
    pass

@dataclass(slots=True)
class PreparedYoutubeSource:
    video_source: Path | str
    video_metadata: dict
    source_mode: str
    stream_url: str | None = None
    local_video_path: Path | None = None
    stream_error: str | None = None

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}

def sanitize_token(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_") or "match"

def media_url_for_path(path: Path) -> str:
    relative = path.relative_to(MEDIA_ROOT)
    return f"/media/{relative.as_posix()}"

def _ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise VideoExtractionError("ffmpeg is not installed in backend runtime.")

def _ensure_ffprobe() -> None:
    if shutil.which("ffprobe") is None:
        raise VideoExtractionError("ffprobe is not installed in backend runtime.")

def _pick_downloaded_video(match_key: str) -> Path | None:
    safe_match = sanitize_token(match_key)
    candidates = [
        path
        for path in sorted(VIDEOS_ROOT.glob(f"{safe_match}.*"))
        if path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]

def resolve_stream_url(youtube_url: str) -> str:
    max_attempts = max(1, int(getattr(settings, "video_extraction_stream_resolve_max_attempts", 3)))
    backoff_sec = max(0.0, float(getattr(settings, "video_extraction_stream_resolve_backoff_sec", 1.0)))
    formats = [
        "best[ext=mp4]/best",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
    ]
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        for format_selector in formats:
            ydl_options = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "format": format_selector,
                "socket_timeout": YOUTUBE_DOWNLOAD_TIMEOUT_SEC,
            }
            try:
                logger.debug(
                    "Resolving YouTube stream (attempt=%s/%s, format=%s) for %s",
                    attempt,
                    max_attempts,
                    format_selector,
                    youtube_url,
                )
                with yt_dlp.YoutubeDL(ydl_options) as ydl:
                    info = ydl.extract_info(youtube_url, download=False)
            except Exception as exc:
                last_error = str(exc) or exc.__class__.__name__
                continue

            if isinstance(info, dict):
                stream_url = info.get("url")
                if stream_url:
                    return str(stream_url)
            last_error = "No playable stream URL found in yt-dlp metadata."

        if attempt < max_attempts and backoff_sec > 0.0:
            time.sleep(backoff_sec * float(attempt))

    detail = f": {last_error}" if last_error else "."
    raise VideoExtractionError(f"Failed to resolve YouTube stream URL after {max_attempts} attempts{detail}")

def prepare_youtube_video_source(
    *,
    match_key: str,
    youtube_url: str,
    force_download_refresh: bool = False,
    prefer_streaming: bool | None = None,
    allow_download_fallback: bool | None = None,
) -> PreparedYoutubeSource:
    stream_first = (
        bool(getattr(settings, "video_extraction_prefer_streaming", True))
        if prefer_streaming is None
        else bool(prefer_streaming)
    )
    can_fallback_to_download = (
        bool(getattr(settings, "video_extraction_allow_download_fallback", True))
        if allow_download_fallback is None
        else bool(allow_download_fallback)
    )
    stream_error: str | None = None

    if stream_first and not force_download_refresh:
        try:
            stream_url = resolve_stream_url(youtube_url)
            metadata = probe_video_metadata(stream_url)
            return PreparedYoutubeSource(
                video_source=stream_url,
                video_metadata=metadata,
                source_mode="youtube_stream",
                stream_url=stream_url,
            )
        except VideoExtractionError as exc:
            stream_error = str(exc) or exc.__class__.__name__
            if not can_fallback_to_download:
                raise

    local_video_path = download_youtube_video(match_key, youtube_url, force=force_download_refresh)
    metadata = probe_video_metadata(local_video_path)
    return PreparedYoutubeSource(
        video_source=local_video_path,
        video_metadata=metadata,
        source_mode="downloaded_video",
        local_video_path=local_video_path,
        stream_error=stream_error,
    )

def cleanup_prepared_youtube_source(source: PreparedYoutubeSource | None) -> None:
    if source is None:
        return
    if not bool(getattr(settings, "video_extraction_cleanup_fallback_download", True)):
        return
    local_video_path = source.local_video_path
    if not isinstance(local_video_path, Path):
        return
    try:
        local_video_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Unable to remove fallback downloaded video %s: %s", local_video_path, exc)

def extract_youtube_frame(match_key: str, youtube_url: str, time_sec: float) -> Path:
    if time_sec < 0:
        raise VideoExtractionError("time_sec must be >= 0")
    _ensure_ffmpeg()

    FRAMES_ROOT.mkdir(parents=True, exist_ok=True)
    safe_match = sanitize_token(match_key)
    time_ms = int(round(time_sec * 1000))
    frame_path = FRAMES_ROOT / f"{safe_match}_{time_ms}.jpg"

    if frame_path.exists():
        return frame_path

    stream_url = resolve_stream_url(youtube_url)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{time_sec:.3f}",
        "-i",
        stream_url,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(frame_path),
    ]
    try:
        logger.debug("Extracting frame at %ss from %s", time_sec, match_key)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SEC, check=False)  # nosec B603
    except subprocess.TimeoutExpired as exc:
        logger.error("FFmpeg timeout extracting frame from %s", match_key)
        raise VideoExtractionError(f"ffmpeg timeout extracting frame (>{FFMPEG_TIMEOUT_SEC}s)") from exc
    if result.returncode != 0:
        details = (result.stderr or "").strip()
        if len(details) > 500:
            details = details[-500:]
        raise VideoExtractionError(f"ffmpeg failed to extract frame: {details}")

    if not frame_path.exists():
        raise VideoExtractionError("Frame extraction completed but output file was not created.")

    return frame_path

def download_youtube_video(match_key: str, youtube_url: str, force: bool = False) -> Path:
    VIDEOS_ROOT.mkdir(parents=True, exist_ok=True)
    with suppress(Exception):
        run_media_retention(force=False, reason="download_youtube_video")

    if not force:
        cached = _pick_downloaded_video(match_key)
        if cached is not None:
            return cached

    safe_match = sanitize_token(match_key)
    template = VIDEOS_ROOT / f"{safe_match}.%(ext)s"
    ydl_options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(template),
        "restrictfilenames": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_options) as ydl:
            ydl.download([youtube_url])
    except Exception as exc:
        raise VideoExtractionError(f"Failed to download YouTube video: {exc}") from exc

    downloaded = _pick_downloaded_video(match_key)
    if downloaded is None:
        raise VideoExtractionError("YouTube download completed but no video file was found.")
    return downloaded

def capture_live_stream_clip(
    match_key: str,
    stream_url: str,
    *,
    clip_duration_sec: float = 26.0,
    output_tag: str = "live",
) -> Path:
    if clip_duration_sec <= 1.0:
        raise VideoExtractionError("clip_duration_sec must be > 1 second")
    if clip_duration_sec > 180.0:
        raise VideoExtractionError("clip_duration_sec must be <= 180 seconds")
    _ensure_ffmpeg()
    VIDEOS_ROOT.mkdir(parents=True, exist_ok=True)

    safe_match = sanitize_token(match_key)
    safe_tag = sanitize_token(output_tag)
    output_path = VIDEOS_ROOT / f"{safe_match}.{safe_tag}.mp4"
    temp_path = VIDEOS_ROOT / f"{safe_match}.{safe_tag}.tmp.mp4"
    if temp_path.exists():
        with suppress(Exception):
            temp_path.unlink()

    input_url = str(stream_url or "").strip()
    if not input_url:
        raise VideoExtractionError("stream_url is required for live clip capture.")
    lowered = input_url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        input_url = resolve_stream_url(input_url)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_url,
        "-t",
        f"{float(clip_duration_sec):.3f}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(temp_path),
    ]
    timeout_sec = int(max(FFMPEG_TIMEOUT_SEC, float(clip_duration_sec) + LIVE_CLIP_TIMEOUT_BUFFER_SEC))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)  # nosec B603
    except subprocess.TimeoutExpired as exc:
        temp_path.unlink(missing_ok=True)
        raise VideoExtractionError(f"ffmpeg timeout capturing live clip (>{timeout_sec}s)") from exc
    if result.returncode != 0:
        details = (result.stderr or "").strip()
        if len(details) > 500:
            details = details[-500:]
        raise VideoExtractionError(f"ffmpeg failed to capture live clip: {details}")

    if not temp_path.exists():
        raise VideoExtractionError("Live clip capture completed but output file was not created.")

    try:
        temp_path.replace(output_path)
    except Exception as exc:
        raise VideoExtractionError(f"Unable to finalize live clip output: {exc}") from exc
    return output_path

def probe_video_metadata(video_source: Path | str) -> dict:
    # Probe video metadata.  *video_source* can be a local Path **or** a
    # remote URL (e.g. a resolved YouTube stream URL).
    _ensure_ffprobe()

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate:format=duration",
        "-of",
        "json",
        str(video_source),
    ]
    try:
        logger.debug("Probing video metadata for %s", video_source)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SEC, check=False)  # nosec B603
    except subprocess.TimeoutExpired as exc:
        logger.error("FFmpeg timeout probing video %s", video_source)
        raise VideoExtractionError(f"ffprobe timeout (>{FFMPEG_TIMEOUT_SEC}s)") from exc
    if result.returncode != 0:
        details = (result.stderr or "").strip()
        raise VideoExtractionError(f"ffprobe failed to read video metadata: {details}")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise VideoExtractionError("ffprobe returned invalid JSON metadata.") from exc

    streams = payload.get("streams") or []
    if not streams:
        raise VideoExtractionError("Video stream metadata is missing.")
    stream = streams[0]

    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise VideoExtractionError("Invalid video dimensions.")

    duration_raw = str(((payload.get("format") or {}).get("duration") or "0")).strip()
    try:
        duration_sec = float(duration_raw)
    except ValueError as exc:
        raise VideoExtractionError("Invalid video duration metadata.") from exc
    if duration_sec <= 0:
        raise VideoExtractionError("Video duration must be positive.")

    fps_raw = str(stream.get("r_frame_rate") or "").strip()
    fps: float | None = None
    if fps_raw and fps_raw != "0/0":
        if "/" in fps_raw:
            numer, denom = fps_raw.split("/", 1)
            try:
                denom_value = float(denom)
                if denom_value != 0:
                    fps = float(numer) / denom_value
            except ValueError:
                fps = None
        else:
            try:
                fps = float(fps_raw)
            except ValueError:
                fps = None

    return {
        "duration_sec": round(duration_sec, 3),
        "width": width,
        "height": height,
        "fps": round(fps, 3) if isinstance(fps, float) else None,
    }

def extract_sample_frames(
    match_key: str,
    video_source: Path | str,
    run_id: int,
    sample_every_sec: float = 2.0,
    max_frames: int = 120,
    start_sec: float | None = None,
    duration_sec: float | None = None,
) -> list[Path]:
    # Extract sample frames.  *video_source* can be a local Path **or** a
    # remote URL (e.g. a resolved YouTube stream URL).
    if sample_every_sec <= 0:
        raise VideoExtractionError("sample_every_sec must be > 0")
    if max_frames <= 0:
        raise VideoExtractionError("max_frames must be > 0")
    if start_sec is not None and float(start_sec) < 0.0:
        raise VideoExtractionError("start_sec must be >= 0 when provided")
    if duration_sec is not None and float(duration_sec) <= 0.0:
        raise VideoExtractionError("duration_sec must be > 0 when provided")
    _ensure_ffmpeg()

    safe_match = sanitize_token(match_key)
    run_dir = ANALYSIS_FRAMES_ROOT / safe_match / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for stale in run_dir.glob("frame_*.jpg"):
        with suppress(Exception):
            stale.unlink()
    output_pattern = run_dir / "frame_%05d.jpg"
    cmd = [
        "ffmpeg",
        "-y",
    ]
    if start_sec is not None and float(start_sec) > 0.0:
        cmd.extend(["-ss", f"{float(start_sec):.3f}"])
    if duration_sec is not None and float(duration_sec) > 0.0:
        cmd.extend(["-t", f"{float(duration_sec):.3f}"])
    cmd.extend(
        [
            "-i",
            str(video_source),
            "-vf",
            f"fps=1/{sample_every_sec:.3f}",
            "-frames:v",
            str(max_frames),
            "-q:v",
            "3",
            str(output_pattern),
        ]
    )
    try:
        logger.debug(
            "Sampling frames from %s every %ss (start=%s, duration=%s)",
            video_source,
            sample_every_sec,
            None if start_sec is None else round(float(start_sec), 3),
            None if duration_sec is None else round(float(duration_sec), 3),
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SEC, check=False)  # nosec B603
    except subprocess.TimeoutExpired as exc:
        logger.error("FFmpeg timeout sampling frames from %s", video_source)
        raise VideoExtractionError(f"ffmpeg frame sampling timeout (>{FFMPEG_TIMEOUT_SEC}s)") from exc
    if result.returncode != 0:
        details = (result.stderr or "").strip()
        if len(details) > 500:
            details = details[-500:]
        raise VideoExtractionError(f"ffmpeg failed to extract sample frames: {details}")

    frames = sorted(run_dir.glob("frame_*.jpg"))
    if not frames:
        raise VideoExtractionError("No sample frames were extracted from the video.")
    return frames

def probe_image_dimensions(image_path: Path) -> tuple[int, int]:
    _ensure_ffprobe()

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=s=x:p=0",
        str(image_path),
    ]
    try:
        logger.debug("Probing image dimensions for %s", image_path)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SEC, check=False)  # nosec B603
    except subprocess.TimeoutExpired as exc:
        logger.error("FFmpeg timeout probing image %s", image_path)
        raise VideoExtractionError(f"ffprobe timeout (>{FFMPEG_TIMEOUT_SEC}s)") from exc
    if result.returncode != 0:
        details = (result.stderr or "").strip()
        raise VideoExtractionError(f"ffprobe failed to read dimensions: {details}")

    raw = (result.stdout or "").strip()
    if "x" not in raw:
        raise VideoExtractionError("ffprobe output did not include dimensions.")

    width_str, height_str = raw.split("x", 1)
    try:
        width = int(width_str)
        height = int(height_str)
    except ValueError as exc:
        raise VideoExtractionError("Invalid ffprobe dimensions output.") from exc

    if width <= 0 or height <= 0:
        raise VideoExtractionError("Invalid image dimensions.")
    return width, height
