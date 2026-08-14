from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
from pathlib import Path
import re
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request
import jwt
from jwt import InvalidTokenError

from app.core.config import settings

logger = logging.getLogger(__name__)

_SENSITIVE_KV_RE = re.compile(
    r"(?i)\b(x-tba-auth-key|authorization|api[_ -]?key|token|secret|password)\b\s*[:=]\s*([^\s,;]+)"
)
_SENSITIVE_QS_RE = re.compile(
    r"(?i)([?&](?:x_tba_auth_key|auth|authorization|api_key|apikey|token|secret)=)([^&\s]+)"
)
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_WRITE_PATH_ADMIN_EXEMPT_PREFIXES = (
    "/scouting/rooms",
    "/api/scouting/rooms",
    "/maintenance/admin/session",
    "/api/maintenance/admin/session",
    # Scout-facing collaborative tools: writable without admin keys, same
    # trust model as scouting rooms. Still blocked in public readonly mode.
    "/picklists",
    "/api/picklists",
    "/pit-scouting",
    "/api/pit-scouting",
    "/push",
    "/api/push",
    # Offline PWA sync: scouts in the field flush on-device match breakdowns
    # without admin keys. Scoped to the single sync route, not all of /tracks.
    "/tracks/on-device-session",
    "/api/tracks/on-device-session",
)
ADMIN_SESSION_HEADER = "X-Admin-Session"
ROOM_ACCESS_HEADER = "X-Room-Access-Token"
ROOM_ROLE_OWNER = "owner"
ROOM_ROLE_EDITOR = "editor"
ROOM_ROLE_VIEWER = "viewer"
_ROOM_WRITE_ROLES = {ROOM_ROLE_OWNER, ROOM_ROLE_EDITOR}
_TOKEN_ALGORITHM = "HS256"  # nosec B105
_LEGACY_TOKEN_VERSION = "v1"  # nosec B105

def _build_stable_dev_ephemeral_token_secret() -> bytes:
    # Build a deterministic fallback secret for local/dev environments.
    #
    # This avoids cross-worker token mismatch when explicit token secrets are
    # unset and workers are launched without explicit worker-count env vars.
    deployment_fingerprint = "|".join(
        [
            str(Path(__file__).resolve().parents[2]),
            str(settings.app_env or ""),
            str(settings.database_url or ""),
            str(settings.redis_url or ""),
            str(settings.security.admin_api_header or ""),
        ]
    )
    return hashlib.sha256(deployment_fingerprint.encode("utf-8", errors="ignore")).digest()

_DEV_EPHEMERAL_TOKEN_SECRET = _build_stable_dev_ephemeral_token_secret()

def _safe_compare_secret(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))

def _secret_to_bytes(value: str | None) -> bytes:
    token = str(value or "").strip()
    if not token:
        return b""
    try:
        return token.encode("utf-8")
    except UnicodeEncodeError:
        return token.encode("utf-8", errors="surrogatepass")

def _token_secret_bytes() -> bytes:
    explicit = _secret_to_bytes(settings.security.admin_session_token_secret)
    if explicit:
        return explicit
    if bool(settings.is_production_like):
        return b""
    fallback = _secret_to_bytes(settings.security.admin_api_key)
    if fallback:
        return fallback
    logger.warning(
        "Signing admin/session tokens with a deterministic development fallback secret. "
        "Set ADMIN_SESSION_TOKEN_SECRET to a managed stable value for production deployments."
    )
    return _DEV_EPHEMERAL_TOKEN_SECRET

def _b64url_decode(token: str) -> bytes:
    raw = str(token or "").strip()
    if not raw:
        return b""
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    return base64.urlsafe_b64decode(raw + pad)

def _signed_payload_token(payload: dict[str, Any]) -> str:
    secret_bytes = _token_secret_bytes()
    if not secret_bytes:
        raise RuntimeError(
            "ADMIN_SESSION_TOKEN_SECRET is required for signing access/session tokens in production-like environments."
        )
    return str(jwt.encode(payload, secret_bytes, algorithm=_TOKEN_ALGORITHM))

def _verify_legacy_signed_payload_token(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    version, payload_b64, signature_b64 = parts
    if version != _LEGACY_TOKEN_VERSION or not payload_b64 or not signature_b64:
        return None

    secret_bytes = _token_secret_bytes()
    if not secret_bytes:
        return None

    expected_signature = hmac.new(
        secret_bytes,
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        provided_signature = _b64url_decode(signature_b64)
    except (binascii.Error, ValueError):
        return None
    if not secrets.compare_digest(expected_signature, provided_signature):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    try:
        exp_value = int(exp) if exp is not None else 0
    except (TypeError, ValueError):
        return None
    now_ts = int(time.time())
    if exp_value <= now_ts:
        return None
    return payload

def _verify_signed_payload_token(token: str | None) -> dict[str, Any] | None:
    secret_bytes = _token_secret_bytes()
    if not secret_bytes:
        return None
    raw = str(token or "").strip()
    if not raw:
        return None
    if raw.startswith(f"{_LEGACY_TOKEN_VERSION}."):
        return _verify_legacy_signed_payload_token(raw)
    try:
        payload = jwt.decode(
            raw,
            secret_bytes,
            algorithms=[_TOKEN_ALGORITHM],
            options={"require": ["exp", "iat", "typ"]},
        )
    except InvalidTokenError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload

def _header_or_bearer(headers: dict[str, str] | Any, header_name: str) -> str:
    value = str(headers.get(header_name) or "").strip()
    if value:
        return value
    auth = str(headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return ""

def read_admin_session_token_from_request(request: Request) -> str:
    return _header_or_bearer(request.headers, ADMIN_SESSION_HEADER)

def normalize_room_role(value: str | None) -> str:
    token = str(value or "").strip().lower()
    if token in {ROOM_ROLE_OWNER, ROOM_ROLE_EDITOR, ROOM_ROLE_VIEWER}:
        return token
    # Unknown/malformed role must fail to the least-privileged role, not write access.
    return ROOM_ROLE_VIEWER

def issue_admin_session_token(*, ttl_sec: int | None = None, principal: str = "admin") -> dict[str, Any]:
    now_ts = int(time.time())
    configured_ttl = int(settings.security.admin_session_ttl_sec or 7200)
    resolved_ttl = int(ttl_sec or configured_ttl)
    resolved_ttl = max(60, min(resolved_ttl, 24 * 3600))
    expires_ts = now_ts + resolved_ttl
    payload = {
        "typ": "admin_session",
        "sid": secrets.token_hex(12),
        "principal": str(principal or "admin")[:64],
        "iat": now_ts,
        "exp": expires_ts,
    }
    return {
        "token": _signed_payload_token(payload),
        "expires_at_unix": expires_ts,
        "expires_at": datetime.fromtimestamp(expires_ts, tz=timezone.utc).isoformat(),
        "ttl_sec": resolved_ttl,
    }

def parse_admin_session_token(token: str | None) -> dict[str, Any] | None:
    payload = _verify_signed_payload_token(token)
    if not payload:
        return None
    if str(payload.get("typ") or "") != "admin_session":
        return None
    return payload

def issue_room_access_token(
    *,
    room_key: str,
    scout_profile: str,
    role: str = ROOM_ROLE_EDITOR,
    ttl_sec: int | None = None,
) -> dict[str, Any]:
    now_ts = int(time.time())
    configured_ttl = int(settings.security.scouting_room_access_ttl_sec or 43200)
    resolved_ttl = int(ttl_sec or configured_ttl)
    resolved_ttl = max(300, min(resolved_ttl, 7 * 24 * 3600))
    expires_ts = now_ts + resolved_ttl
    normalized_role = normalize_room_role(role)
    payload = {
        "typ": "room_access",
        "rid": secrets.token_hex(10),
        "room_key": str(room_key or "").strip().lower()[:48],
        "scout_profile": str(scout_profile or "").strip()[:40],
        "role": normalized_role,
        "iat": now_ts,
        "exp": expires_ts,
    }
    return {
        "token": _signed_payload_token(payload),
        "role": normalized_role,
        "expires_at_unix": expires_ts,
        "expires_at": datetime.fromtimestamp(expires_ts, tz=timezone.utc).isoformat(),
        "ttl_sec": resolved_ttl,
    }

def parse_room_access_token(token: str | None) -> dict[str, Any] | None:
    payload = _verify_signed_payload_token(token)
    if not payload:
        return None
    if str(payload.get("typ") or "") != "room_access":
        return None
    return payload

def room_access_allows(
    payload: dict[str, Any] | None,
    *,
    room_key: str,
    scout_profile: str | None = None,
    require_write: bool = False,
) -> bool:
    if not isinstance(payload, dict):
        return False
    token_room_key = str(payload.get("room_key") or "").strip().lower()
    expected_room_key = str(room_key or "").strip().lower()
    if not token_room_key or token_room_key != expected_room_key:
        return False
    token_profile = str(payload.get("scout_profile") or "").strip()
    if scout_profile is not None and token_profile != str(scout_profile or "").strip():
        return False
    role = normalize_room_role(str(payload.get("role") or ROOM_ROLE_EDITOR))
    if require_write and role not in _ROOM_WRITE_ROLES:
        return False
    return True

def request_has_admin_session(request: Request) -> bool:
    token = read_admin_session_token_from_request(request)
    return parse_admin_session_token(token) is not None

def admin_api_key_authorized(candidate_key: str) -> bool:
    if not bool(settings.security.enforce_admin_auth_for_writes):
        return True
    expected = str(settings.security.admin_api_key or "").strip()
    if not expected:
        return not bool(settings.is_production_like)
    provided = str(candidate_key or "").strip()
    if provided and _safe_compare_secret(provided, expected):
        return True
    return False

def request_has_admin_access(request: Request) -> bool:
    if not bool(settings.security.enforce_admin_auth_for_writes):
        return True
    expected = str(settings.security.admin_api_key or "").strip()
    if not expected:
        # Fail-closed outside dev/testing when admin key is not configured.
        return not bool(settings.is_production_like)

    if request_has_admin_session(request):
        return True

    header_name = str(settings.security.admin_api_header or "X-Admin-Key").strip() or "X-Admin-Key"
    provided = str(request.headers.get(header_name) or "").strip()
    if provided and _safe_compare_secret(provided, expected):
        return True

    return False

def on_device_sync_identity(request: Request) -> tuple[bool, str]:
    # Authorisation + a stable namespace prefix for the on-device session sync.
    # Admin access (key/session) authorises under the shared "a" namespace; a valid
    # signed room-access token authorises under a per-scout namespace so one scout
    # cannot overwrite another's synced run by guessing its client session id.
    # Returns (authorized, identity_prefix); "n" means unauthenticated.
    if request_has_admin_access(request):
        return True, "a"
    token = _header_or_bearer(request.headers, ROOM_ACCESS_HEADER)
    payload = parse_room_access_token(token)
    if isinstance(payload, dict):
        ident = str(payload.get("scout_profile") or payload.get("rid") or "").strip()[:48]
        return True, f"r:{ident or 'anon'}"
    return False, "n"

def require_write_access(operation: str) -> None:
    if settings.security.public_readonly_mode:
        raise HTTPException(
            status_code=403,
            detail=f"{operation} is disabled in public mode.",
        )

def require_admin_access(request: Request, operation: str) -> None:
    if request_has_admin_access(request):
        return

    header_name = str(settings.security.admin_api_header or "X-Admin-Key").strip() or "X-Admin-Key"
    # 403 Forbidden — the server understood the request but refuses to authorise it.
    # 401 is for unauthenticated requests and requires a WWW-Authenticate challenge header.
    raise HTTPException(
        status_code=403,
        detail=(
            f"{operation} requires a valid {header_name} header or {ADMIN_SESSION_HEADER} session token."
        ),
    )

def enforce_write_request_access(request: Request) -> None:
    method = str(request.method or "").upper()
    if method not in _WRITE_METHODS:
        return

    request_path = str(getattr(request.url, "path", "") or "").strip()

    if settings.security.public_readonly_mode:
        raise HTTPException(
            status_code=403,
            detail="Write endpoints are disabled in public mode.",
        )

    if any(request_path.startswith(prefix) for prefix in _WRITE_PATH_ADMIN_EXEMPT_PREFIXES):
        return

    if request_has_admin_access(request):
        return

    header_name = str(settings.security.admin_api_header or "X-Admin-Key").strip() or "X-Admin-Key"
    raise HTTPException(
        status_code=403,
        detail=(
            f"Write access requires a valid {header_name} header or {ADMIN_SESSION_HEADER} session token."
        ),
    )

def sanitize_external_error(
    exc: Exception | str,
    *,
    default: str = "Upstream service request failed.",
    max_len: int = 240,
) -> str:
    raw = str(exc or "").strip()
    if not raw:
        return default
    redacted = _SENSITIVE_KV_RE.sub(r"\1=***", raw)
    redacted = _SENSITIVE_QS_RE.sub(r"\1***", redacted)
    if len(redacted) > max_len:
        redacted = f"{redacted[: max_len - 3]}..."
    return redacted
