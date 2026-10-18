# Web Push delivery via pywebpush (VAPID).
#
# pywebpush is an optional dependency: when it is missing or VAPID keys are
# unconfigured, push features degrade to no-ops with a logged warning instead
# of breaking the API.

import json
import logging
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.push.subscriptions import valid_subscription_keys

logger = logging.getLogger(__name__)

try:
    from pywebpush import webpush, WebPushException

    _PYWEBPUSH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the dep
    webpush = None
    WebPushException = Exception
    _PYWEBPUSH_AVAILABLE = False

_MISSING_DEP_WARNED = False
# Browsers hand out endpoints on their vendor's push service only. Anything else
# would make the server POST wherever a caller points it (and wait on it).
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
)
PUSH_SERVICE_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".push.services.mozilla.com")
PUSH_SEND_TIMEOUT_SEC = 10


def push_endpoint_allowed(endpoint: str) -> bool:
    try:
        parts = urlsplit(str(endpoint or "").strip())
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or port not in (None, 443) or parts.username or parts.password:
        return False
    return host in PUSH_SERVICE_HOSTS or host.endswith(PUSH_SERVICE_HOST_SUFFIXES)

def push_configured() -> bool:
    return bool(
        _PYWEBPUSH_AVAILABLE
        and str(settings.vapid_public_key or "").strip()
        and str(settings.vapid_private_key or "").strip()
    )

def push_unavailable_reason() -> str | None:
    if not _PYWEBPUSH_AVAILABLE:
        return "pywebpush is not installed on the backend."
    if not str(settings.vapid_public_key or "").strip() or not str(settings.vapid_private_key or "").strip():
        return "VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY are not configured."
    return None

def send_web_push(
    db: Session,
    subscription: models.PushSubscription,
    payload: dict[str, Any],
) -> bool:
    # Send one notification. Returns True on success. Disables the
    # subscription after repeated permanent failures (endpoint gone).
    global _MISSING_DEP_WARNED
    if not push_configured():
        if not _MISSING_DEP_WARNED:
            logger.warning("Web push skipped: %s", push_unavailable_reason())
            _MISSING_DEP_WARNED = True
        return False

    if not push_endpoint_allowed(subscription.endpoint):
        # Stored before the endpoint check existed; never send there.
        logger.warning("Push endpoint not on a known push service; deleting subscription %s", subscription.id)
        db.delete(subscription)
        return False

    keys = subscription.keys if isinstance(subscription.keys, dict) else {}
    if not valid_subscription_keys(keys):
        logger.warning("Invalid push encryption keys; deleting subscription %s", subscription.id)
        db.delete(subscription)
        return False
    subscription_info = {
        "endpoint": subscription.endpoint,
        "keys": {
            "p256dh": str(keys.get("p256dh") or ""),
            "auth": str(keys.get("auth") or ""),
        },
    }
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload, default=str),
            vapid_private_key=str(settings.vapid_private_key).strip(),
            vapid_claims={"sub": str(settings.vapid_subject or "https://scouting-app-iryg.vercel.app").strip()},
            ttl=int(settings.push_ttl_sec or 1800),
            timeout=PUSH_SEND_TIMEOUT_SEC,
        )
        if subscription.failure_count:
            subscription.failure_count = 0
        return True
    except WebPushException as exc:  # type: ignore[misc]
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in (404, 410):
            # Endpoint permanently gone — drop the subscription.
            logger.info("Push endpoint gone (%s); deleting subscription %s", status_code, subscription.id)
            db.delete(subscription)
            return False
        subscription.failure_count = int(subscription.failure_count or 0) + 1
        max_failures = max(1, int(settings.push_subscription_max_failures or 8))
        if subscription.failure_count >= max_failures:
            subscription.enabled = False
            logger.warning(
                "Push subscription %s disabled after %s failures",
                subscription.id,
                subscription.failure_count,
            )
        else:
            logger.warning("Push send failed for subscription %s: %s", subscription.id, exc)
        return False
    except Exception:
        logger.exception("Unexpected push send failure for subscription %s", subscription.id)
        return False
