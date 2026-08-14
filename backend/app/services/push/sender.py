# Web Push delivery via pywebpush (VAPID).
#
# pywebpush is an optional dependency: when it is missing or VAPID keys are
# unconfigured, push features degrade to no-ops with a logged warning instead
# of breaking the API.

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models

logger = logging.getLogger(__name__)

try:
    from pywebpush import webpush, WebPushException

    _PYWEBPUSH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the dep
    webpush = None
    WebPushException = Exception
    _PYWEBPUSH_AVAILABLE = False

_MISSING_DEP_WARNED = False

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

    keys = subscription.keys if isinstance(subscription.keys, dict) else {}
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
            vapid_claims={"sub": str(settings.vapid_subject or "mailto:admin@frcmob.app").strip()},
            ttl=int(settings.push_ttl_sec or 1800),
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
