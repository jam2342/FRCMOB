from pathlib import Path
import logging
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.api.routes_analysis import router as analysis_router
from app.api.routes_auto_scouting import router as auto_scouting_router
from app.api.routes_calibrations import router as calibrations_router
from app.api.routes_events import router as events_router
from app.api.routes_game_config import router as game_config_router
from app.api.routes_maintenance import router as maintenance_router
from app.api.routes_matches import router as matches_router
from app.api.routes_picklists import router as picklists_router
from app.api.routes_pit_scouting import router as pit_scouting_router
from app.api.routes_push import router as push_router
from app.api.routes_scouting import router as scouting_router
from app.api.routes_scouting_insights import router as scouting_insights_router
from app.api.routes_scouting_rooms import router as scouting_rooms_router
from app.api.routes_storage import router as storage_router
from app.api.routes_synergy import router as synergy_router
from app.api.routes_statbotics import router as statbotics_router
from app.api.routes_teams import router as teams_router
from app.api.routes_tracks import router as tracks_router
from app.api.routes_videos import router as videos_router
from app.api.tba import router as tba_router
from app.core.config import (
    LOCAL_DATABASE_URL_FALLBACK,
    assert_no_legacy_texas_settings,
    settings,
)
from app.core.security import ADMIN_SESSION_HEADER
from app.core.security import enforce_write_request_access
from app.core.security import request_has_admin_access
from app.db.session import SessionLocal
from app.services.utils import pg_sqlstate_code as _pg_sqlstate
from app.services.scouting_rooms.bus import scouting_room_bus
from app.services.analysis.live_monitor import stop_all_live_monitors
from app.services.scouting_rooms.realtime import scouting_room_hub
from app.services.scheduler import start_scheduler, stop_scheduler

import json as _json

class _JSONLogFormatter(logging.Formatter):
    # Structured JSON log formatter for production observability.

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "request_id"):
            entry["request_id"] = record.request_id
        return _json.dumps(entry, default=str)

def _configure_logging() -> None:
    level_name = str(settings.log_level or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    log_format = str(getattr(settings, "log_format", "text") or "text").strip().lower()
    root_logger = logging.getLogger()

    if not root_logger.handlers:
        handler = logging.StreamHandler()
        if log_format == "json":
            handler.setFormatter(_JSONLogFormatter())
        else:
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            )
        root_logger.addHandler(handler)
        root_logger.setLevel(level)
    else:
        root_logger.setLevel(level)

_configure_logging()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Manage startup/shutdown lifecycle for background services.
    # ── startup ──────────────────────────────────────────────────────────
    # Unconditional guard: legacy *_TEXAS_* keys are silently ignored and
    # disable the analysis/ML pipeline. Fail before anything else starts.
    assert_no_legacy_texas_settings()

    # Best-effort: ensure the FRC detector model is present (download if a URL
    # is configured). Never blocks startup; the pipeline flags degraded runs.
    model_status: dict[str, object] = {
        "present": False,
        "downloaded": False,
        "source": None,
        "error": "model_provisioning_not_run",
        "checksum_verified": False,
    }
    try:
        from app.services.vision.model_provisioning import ensure_primary_model_available

        model_status = ensure_primary_model_available()
        logger.info("startup.model_provisioning %s", model_status)
    except Exception:
        logger.exception("startup.model_provisioning unexpected failure (continuing)")
        model_status["error"] = "model_provisioning_unexpected_failure"
    app.state.primary_model_status = model_status

    env_report = _startup_env_validation_report(model_status=model_status)
    for warning in env_report.get("warnings", []):
        logger.warning("startup.env_validation.warning %s", warning)
    if env_report.get("errors"):
        logger.error("startup.env_validation.errors %s", env_report.get("errors"))
        if bool(env_report.get("fail_startup")) or bool(settings.strict_startup_env_validation):
            raise RuntimeError(
                f"Startup env validation failed: {', '.join(env_report.get('errors') or [])}"
            )
    production_like = bool(getattr(settings, "is_production_like", False))

    try:
        start_scheduler()
        logger.info("Background scheduler initialized on startup")
    except Exception:
        logger.exception("Failed to start scheduler on startup")
        if production_like:
            raise

    try:
        await scouting_room_bus.start(on_message=_handle_scouting_room_bus_message)
        logger.info("Scouting room redis bus initialized")
    except Exception:
        logger.exception("Failed to initialize scouting room redis bus")
        if production_like:
            raise

    # Statbotics is an optional external dep with its own retry/circuit breaker;
    # pre-warming the connection pool is best-effort even in production.
    try:
        from app.services.clients import statbotics as statbotics_client
        await statbotics_client._get_client()
        logger.info("Statbotics HTTP client initialized with connection pooling")
    except Exception:
        logger.warning("Statbotics client warmup failed; continuing.", exc_info=True)

    yield

    # ── shutdown ─────────────────────────────────────────────────────────
    try:
        from app.services.clients import statbotics as statbotics_client
        await statbotics_client.close_client()
        logger.info("Statbotics HTTP client closed")
    except Exception as e:
        logger.error("Error closing Statbotics client: %s", e)

    try:
        monitor_stop = stop_all_live_monitors()
        logger.info(
            "Live analysis monitors stopped count=%s",
            monitor_stop.get("stopped_count") if isinstance(monitor_stop, dict) else None,
        )
    except Exception as e:
        logger.error("Error stopping live analysis monitors: %s", e)

    try:
        stop_scheduler()
        logger.info("Background scheduler stopped on shutdown")
    except Exception as e:
        logger.error("Error during scheduler shutdown: %s", e)

    try:
        await scouting_room_bus.stop()
        logger.info("Scouting room redis bus stopped")
    except Exception as e:
        logger.error("Error stopping scouting room redis bus: %s", e)

    try:
        await scouting_room_hub.shutdown()
        logger.info("Scouting room realtime hub stopped")
    except Exception as e:
        logger.error("Error stopping scouting room realtime hub: %s", e)

app = FastAPI(lifespan=lifespan)
MEDIA_DIR = Path(__file__).resolve().parents[1] / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
request_logger = logging.getLogger("app.request")

def _database_url_targets_localhost(database_url: str) -> bool:
    # True if the DB URL host is loopback. In a container that means "this
    # container", which is virtually never a valid production database target.
    raw = str(database_url or "").strip()
    if not raw:
        return False
    try:
        # urlsplit needs a scheme without '+driver' to parse the host cleanly.
        scheme, _, rest = raw.partition("://")
        host = urlsplit(f"x://{rest}").hostname if rest else None
    except (ValueError, TypeError):
        return False
    return (host or "").lower() in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _startup_env_validation_report(*, model_status: dict[str, object] | None = None) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    strict_mode = bool(settings.strict_startup_env_validation)
    production_like = bool(getattr(settings, "is_production_like", False))

    if not str(settings.database_url or "").strip():
        errors.append("DATABASE_URL is empty.")
    elif production_like and bool(getattr(settings, "database_url_is_local_fallback", False)):
        errors.append(
            "DATABASE_URL is using the local development fallback "
            f"({LOCAL_DATABASE_URL_FALLBACK})."
        )
    elif production_like and _database_url_targets_localhost(str(settings.database_url)):
        errors.append(
            "DATABASE_URL points at localhost/127.0.0.1 while running in "
            "production. Inside a container 'localhost' is the container "
            "itself — set DATABASE_URL to the real database host (override it "
            "in the deploy environment, not just .env)."
        )
    if not str(settings.redis_url or "").strip():
        errors.append("REDIS_URL is empty.")

    if not str(settings.admin_session_token_secret or "").strip():
        message = (
            "ADMIN_SESSION_TOKEN_SECRET is empty; room/admin tokens cannot be trusted without a configured signing secret."
        )
        if production_like or strict_mode:
            errors.append(message)
        else:
            warnings.append(
                f"{message} Dev/testing will use a process-local ephemeral secret."
            )

    if bool(settings.enforce_admin_auth_for_writes) and not str(settings.admin_api_key or "").strip():
        message = (
            "ENFORCE_ADMIN_AUTH_FOR_WRITES=true but ADMIN_API_KEY is empty; "
            "admin-gated write operations cannot be authenticated."
        )
        if production_like or strict_mode:
            errors.append(message)
        else:
            warnings.append(message)

    cors_raw = str(settings.cors_allow_origins or "").strip()
    if strict_mode and (not cors_raw or cors_raw == "*"):
        errors.append(
            "CORS_ALLOW_ORIGINS must be set to explicit domain(s) when STRICT_STARTUP_ENV_VALIDATION=true."
        )

    first_user = bool(str(settings.first_frc_api_username or "").strip())
    first_key = bool(str(settings.first_frc_api_auth_key or "").strip())
    if first_user ^ first_key:
        warnings.append(
            "FIRST API credentials are partially configured. Set both FIRST_FRC_API_USERNAME and FIRST_FRC_API_AUTH_KEY."
        )

    if not str(settings.tba_auth_key or "").strip():
        warnings.append("TBA_AUTH_KEY is not configured; TBA-backed intel may be sparse.")
    if not str(settings.statbotics_base_url or "").strip():
        errors.append("STATBOTICS_BASE_URL is empty.")

    require_primary_model = bool(
        getattr(settings, "video_tracking_require_primary_model_in_production", True)
    )
    require_model_checksum = bool(
        getattr(settings, "video_tracking_require_primary_model_sha256_in_production", True)
    )
    if production_like and require_primary_model:
        model_status = model_status or {}
        if not bool(model_status.get("present")):
            errors.append(
                "Primary FRC detector is unavailable. Mount it or set VIDEO_TRACKING_YOLO_MODEL_URL."
            )
        elif require_model_checksum and not bool(model_status.get("checksum_verified")):
            errors.append(
                "Primary FRC detector checksum is not verified; set VIDEO_TRACKING_YOLO_MODEL_SHA256."
            )

    if production_like and bool(settings.ml_shadow_auto_train_activate):
        errors.append(
            "ML_SHADOW_AUTO_TRAIN_ACTIVATE must be false in production; promote reviewed candidates explicitly."
        )

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "strict": strict_mode,
        "production_like": production_like,
        "fail_startup": bool(production_like and len(errors) > 0),
    }

def _is_database_transfer_quota_error(exc: Exception) -> bool:
    # Neon surfaces the quota breach as a connection-time message, not a SQLSTATE,
    # so a substring match against exc.args is the only reliable signal.
    return "data transfer quota" in str(exc).lower()

def _database_unavailable_detail(exc: Exception) -> str:
    if _is_database_transfer_quota_error(exc):
        return (
            "Database is temporarily unavailable: Neon data transfer quota exceeded. "
            "Upgrade/reset Neon quota, then retry."
        )
    sqlstate = _pg_sqlstate(exc)
    if sqlstate == "53300":  # too_many_connections
        return "Database is temporarily over connection limit. Retry shortly."
    if sqlstate and sqlstate.startswith("08"):  # connection_exception class
        return "Database connection is temporarily unavailable. Retry shortly."
    return "Database is temporarily unavailable. Retry shortly."

_ALLOWED_BUS_MESSAGE_TYPES = frozenset(
    {
        "room_update",
        "presence",
        "entry_saved",
        "entry_deleted",
        "assignment_updated",
        "assignment_replaced",
        "leader_added",
        "leader_removed",
        "member_kicked",
        "room_control_disconnect_profile",
        "ping",
        "pong",
    }
)

def _enforce_media_access(request: Request) -> None:
    # Block unauthenticated access to /media/* when admin auth is configured.
    request_path = str(getattr(request.url, "path", "") or "").strip()
    if not request_path.startswith("/media"):
        return
    if request_path.startswith("/media/pit_photos/"):
        return
    if not bool(settings.enforce_admin_auth_for_writes):
        return
    if request_has_admin_access(request):
        return
    raise HTTPException(
        status_code=403,
        detail="Media access requires admin authorization.",
    )

def _cors_origins() -> list[str]:
    raw = str(settings.cors_allow_origins or "").strip()
    if not raw:
        # Explicit empty value = refuse all cross-origin. Do NOT silently open to *.
        return []
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if "*" in parts:
        if bool(getattr(settings, "is_production_like", False)):
            logging.getLogger(__name__).warning(
                "CORS_ALLOW_ORIGINS=* in a production-like environment; restrict to explicit domains."
            )
        return ["*"]
    return parts

# Evaluated at import time so the middleware sees the value from settings at startup.
cors_origins = _cors_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=bool(cors_origins) and "*" not in cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
if bool(getattr(settings, "response_compression_enabled", True)):
    app.add_middleware(
        GZipMiddleware,
        minimum_size=max(256, int(getattr(settings, "response_compression_min_size_bytes", 1200) or 1200)),
        compresslevel=max(1, min(9, int(getattr(settings, "response_compression_level", 5) or 5))),
    )

@app.middleware("http")
async def request_access_and_logging_middleware(request: Request, call_next):
    request_id = str(request.headers.get("X-Request-ID") or "").strip() or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        enforce_write_request_access(request)
        _enforce_media_access(request)
    except HTTPException as exc:
        response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        response.headers["X-Request-ID"] = request_id
        request_logger.warning(
            "request.denied request_id=%s method=%s path=%s status=%s detail=%s",
            request_id,
            request.method,
            request.url.path,
            exc.status_code,
            exc.detail,
        )
        return response

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        request_logger.exception(
            "request.error request_id=%s method=%s path=%s duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            duration_ms,
        )
        raise

    duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
    response.headers["X-Request-ID"] = request_id
    if bool(settings.request_timing_header_enabled):
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
    slow_threshold_ms = max(
        1.0,
        float(getattr(settings, "request_slow_log_threshold_ms", 900.0) or 900.0),
    )
    if duration_ms >= slow_threshold_ms:
        request_logger.warning(
            "request.slow request_id=%s method=%s path=%s status=%s duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
    else:
        request_logger.info(
            "request.complete request_id=%s method=%s path=%s status=%s duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
    return response

@app.exception_handler(DBAPIError)
async def dbapi_error_handler(request: Request, exc: DBAPIError):
    request_id = str(getattr(request.state, "request_id", "") or uuid.uuid4().hex[:12])
    request_logger.exception(
        "request.db_error request_id=%s method=%s path=%s",
        request_id,
        request.method,
        request.url.path,
        exc_info=exc,
    )
    response = JSONResponse(
        status_code=503,
        content={"detail": _database_unavailable_detail(exc)},
    )
    response.headers["X-Request-ID"] = request_id
    return response

app.include_router(events_router)
app.include_router(matches_router)
app.include_router(analysis_router)
app.include_router(auto_scouting_router)
app.include_router(calibrations_router)
app.include_router(game_config_router)
app.include_router(maintenance_router)
app.include_router(picklists_router)
app.include_router(pit_scouting_router)
app.include_router(push_router)
app.include_router(scouting_router)
app.include_router(scouting_insights_router)
app.include_router(scouting_rooms_router)
app.include_router(storage_router)
app.include_router(synergy_router)
app.include_router(statbotics_router)
app.include_router(teams_router)
app.include_router(tracks_router)
app.include_router(videos_router)
app.include_router(tba_router)
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

async def _handle_scouting_room_bus_message(room_key: str, payload: dict) -> None:
    message_type = str((payload or {}).get("type") or "").strip().lower()

    if message_type not in _ALLOWED_BUS_MESSAGE_TYPES:
        logging.getLogger(__name__).warning(
            "bus.unknown_type room_key=%s type=%r — dropping", room_key, message_type
        )
        return

    if message_type == "room_control_disconnect_profile":
        target_profile = str((payload or {}).get("scout_profile") or "").strip()
        if not target_profile:
            return
        close_code_raw = payload.get("close_code")
        try:
            close_code = int(close_code_raw)
        except Exception:
            close_code = 4403
        reason = str((payload or {}).get("reason") or "").strip() or "Removed from room by room leader."
        removed_connections, presence_after = await scouting_room_hub.disconnect_scout_profile(
            room_key,
            target_profile,
            close_code=close_code,
            reason=reason,
        )
        if removed_connections > 0:
            await scouting_room_bus.publish(
                room_key,
                {
                    "type": "presence",
                    "room_key": room_key,
                    "members": presence_after,
                },
            )
        return
    await scouting_room_hub.broadcast(room_key, payload)

@app.get("/")
def root():
    return {"status": "scouting backend running"}

@app.get("/health")
def health():
    model_status = getattr(app.state, "primary_model_status", None)
    env_report = _startup_env_validation_report(model_status=model_status)
    return {
        "ok": bool(env_report.get("ok")),
        "app_env": str(getattr(settings, "app_env", "development")),
        "public_readonly_mode": bool(settings.public_readonly_mode),
        "write_auth": {
            "enforced": bool(settings.enforce_admin_auth_for_writes),
            "admin_key_configured": bool(str(settings.admin_api_key or "").strip()),
            "session_token_secret_configured": bool(str(settings.admin_session_token_secret or "").strip()),
            "header": str(settings.admin_api_header or "X-Admin-Key"),
            "session_header": ADMIN_SESSION_HEADER,
        },
        "integrations": {
            "tba_configured": bool(settings.tba_auth_key),
            "first_frc_api_configured": bool(
                settings.first_frc_api_username and settings.first_frc_api_auth_key
            ),
        },
        "ml": {
            "primary_detector": model_status,
            "shadow_enabled": bool(settings.ml_shadow_enabled),
            "auto_training_on_event_breakdown": bool(settings.ml_shadow_auto_train_on_event_breakdown),
        },
        "env_validation": env_report,
    }

@app.get("/health/deep")
def deep_health():
    database_ok = False
    redis_ok = False

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        database_ok = True
    except SQLAlchemyError as exc:
        logger.warning("Deep health database check failed: %s", exc)

    redis_client = None
    try:
        redis_client = redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        redis_ok = bool(redis_client.ping())
    except (redis.RedisError, ValueError, TypeError) as exc:
        logger.warning("Deep health redis check failed: %s", exc)
    finally:
        if redis_client is not None:
            redis_client.close()

    model_status = getattr(app.state, "primary_model_status", None)
    env_report = _startup_env_validation_report(model_status=model_status)
    return {
        "ok": bool(database_ok and redis_ok and env_report.get("ok")),
        "api": True,
        "database": bool(database_ok),
        "redis": bool(redis_ok),
        "ml_primary_detector": model_status,
        "env_validation": env_report,
    }
