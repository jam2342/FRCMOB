from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from app.core.config import settings
from app.services.utils import BACKEND_ROOT

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_SEC = 120
_MIN_PLAUSIBLE_MODEL_BYTES = 100_000  # a real .pt is MBs; guards against HTML error bodies
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")


def _resolve_under_backend(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (BACKEND_ROOT / path)


def primary_model_path() -> Path:
    # Absolute path to the configured primary detector model.
    return _resolve_under_backend(str(settings.video_tracking_yolo_model or "").strip())


def generic_model_names() -> set[str]:
    raw = str(settings.video_tracking_generic_model_names or "")
    return {tok.strip().lower() for tok in raw.split(",") if tok.strip()}


def is_generic_fallback_model(model_source: str) -> bool:
    # True if *model_source* is a known generic (non-FRC) model. Used to flag a
    # run as degraded when the FRC detector was unavailable.
    if not model_source:
        return False
    return Path(str(model_source)).name.strip().lower() in generic_model_names()


def _configured_model_sha256() -> str | None:
    raw = str(getattr(settings, "video_tracking_yolo_model_sha256", "") or "").strip().lower()
    if not raw:
        return None
    if not _SHA256_RE.fullmatch(raw):
        raise ValueError("VIDEO_TRACKING_YOLO_MODEL_SHA256 must be a 64-character hexadecimal SHA-256.")
    return raw


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_model_status(target: Path, expected_sha256: str | None) -> dict:
    if not target.is_file():
        return {"present": False, "bytes": 0, "sha256": None, "checksum_verified": False, "error": None}

    size = int(target.stat().st_size)
    if size < _MIN_PLAUSIBLE_MODEL_BYTES:
        return {
            "present": False,
            "bytes": size,
            "sha256": None,
            "checksum_verified": False,
            "error": f"existing_model_implausibly_small:{size}",
        }

    if expected_sha256 is None:
        return {"present": True, "bytes": size, "sha256": None, "checksum_verified": False, "error": None}

    digest = _sha256_file(target)
    if digest != expected_sha256:
        return {
            "present": False,
            "bytes": size,
            "sha256": digest,
            "checksum_verified": False,
            "error": "existing_model_checksum_mismatch",
        }
    return {"present": True, "bytes": size, "sha256": digest, "checksum_verified": True, "error": None}


def ensure_primary_model_available() -> dict:
    # Best-effort: make the primary FRC detector present on disk.
    #
    # Returns a status dict (never raises) so startup can log loudly but still
    # boot — the pipeline itself enforces the degraded-flag policy per run.
    target = primary_model_path()
    status: dict = {
        "model": str(target),
        "present": target.is_file(),
        "downloaded": False,
        "source": None,
        "error": None,
        "bytes": 0,
        "sha256": None,
        "checksum_verified": False,
    }
    try:
        expected_sha256 = _configured_model_sha256()
    except ValueError as exc:
        status.update(present=False, error=str(exc))
        logger.error("Invalid primary detector configuration: %s", exc)
        return status

    status.update(_existing_model_status(target, expected_sha256))
    if status["present"]:
        status["source"] = "local"
        return status

    url = str(settings.video_tracking_yolo_model_url or "").strip()
    if not url:
        status["error"] = str(status.get("error") or "missing_and_no_download_url")
        logger.error(
            "FRC detector model missing at %s and VIDEO_TRACKING_YOLO_MODEL_URL "
            "is not set. Tracking will fall back to the generic model and every "
            "analysis run will be flagged DEGRADED (low/empty findings).",
            target,
        )
        return status

    parsed_url = urlparse(url)
    if bool(getattr(settings, "is_production_like", False)) and parsed_url.scheme.lower() != "https":
        status["error"] = "production_model_url_must_use_https"
        logger.error("Refusing non-HTTPS detector URL in production: %s", parsed_url.scheme or "missing scheme")
        return status

    tmp_name: str | None = None
    fd = -1
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading FRC detector model -> %s", target)
        max_bytes = max(
            _MIN_PLAUSIBLE_MODEL_BYTES,
            int(getattr(settings, "video_tracking_yolo_model_max_bytes", 1_073_741_824) or 1_073_741_824),
        )
        digest = hashlib.sha256()
        downloaded_bytes = 0
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
        with urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SEC) as resp:  # noqa: S310 - operator-configured URL
            with os.fdopen(fd, "wb") as fh:
                fd = -1
                while chunk := resp.read(_DOWNLOAD_CHUNK_BYTES):
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_bytes:
                        raise ValueError(f"downloaded model exceeds configured limit ({max_bytes} bytes)")
                    digest.update(chunk)
                    fh.write(chunk)
        if downloaded_bytes < _MIN_PLAUSIBLE_MODEL_BYTES:
            raise ValueError(
                f"downloaded model is implausibly small ({downloaded_bytes} bytes); "
                "refusing to install (likely an error page, not weights)"
            )
        actual_sha256 = digest.hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise ValueError("downloaded_model_checksum_mismatch")
        os.replace(tmp_name, target)
        tmp_name = None
        status.update(
            present=True,
            downloaded=True,
            source="download",
            bytes=downloaded_bytes,
            sha256=actual_sha256 if expected_sha256 is not None else None,
            checksum_verified=expected_sha256 is not None,
            error=None,
        )
        logger.info("FRC detector model downloaded (%s bytes) -> %s", downloaded_bytes, target)
    except Exception as exc:  # noqa: BLE001 - never block startup on this
        # Model URLs are commonly pre-signed. Do not reflect a transport
        # exception into logs or the health endpoint because it may include the
        # signed URL and its credentials.
        status["error"] = "download_failed"
        logger.error(
            "Failed to download configured FRC detector model for %s (%s). Tracking will "
            "fall back to the generic model and runs will be flagged DEGRADED.",
            target,
            type(exc).__name__,
        )
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp_name and os.path.exists(tmp_name):
            with __import__("contextlib").suppress(OSError):
                os.remove(tmp_name)
    return status
