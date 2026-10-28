"""Validation and cross-worker admission limits for anonymous push subscribers."""

import base64
import binascii
import logging
import re
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.cache import get_cache

logger = logging.getLogger(__name__)
_B64URL = re.compile(r"[A-Za-z0-9_-]+={0,2}")
_ADMISSION_LOCK = 0x50555348
_CONSUME_BUDGET = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], 7200) end
return count
"""


def valid_subscription_keys(keys: dict) -> bool:
    """Accept only bounded base64url Web Push keys and an actual P-256 point."""
    decoded = {}
    for name, length in (("p256dh", 65), ("auth", 16)):
        value = keys.get(name)
        if not isinstance(value, str) or len(value) > 4 * ((length + 2) // 3):
            return False
        if not _B64URL.fullmatch(value):
            return False
        try:
            raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            return False
        if len(raw) != length or base64.urlsafe_b64encode(raw).decode().rstrip("=") != value.rstrip("="):
            return False
        decoded[name] = raw
    if decoded["p256dh"][0] != 4:
        return False
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded["p256dh"])
    except ValueError:
        return False
    return True


def lock_subscription_admission(db: Session) -> None:
    # Serialize count + insert and same-endpoint upserts across all API workers.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADMISSION_LOCK})


def purge_expired_subscriptions(db: Session, *, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        days=max(1, int(settings.push_subscription_retention_days))
    )
    result = db.execute(delete(models.PushSubscription).where(models.PushSubscription.updated_at < cutoff))
    return int(result.rowcount or 0)


# Registration is anonymous, so the site-wide caps above are all that bound it, and
# one script can spend them. Workspace members (signup is challenged and budgeted)
# get their own hourly budget per workspace and room past the anonymous total, so a
# flood of anonymous subscribers can't stop a team turning on its alerts.
WORKSPACE_REGISTRATIONS_PER_HOUR = 60


def subscription_ceiling() -> int:
    return 2 * max(0, int(settings.push_subscriptions_max_total))


def admit_new_subscription(db: Session, *, workspace_id: int | None = None) -> None:
    # Caller holds the transaction lock until its new subscription is committed.
    total = db.scalar(select(func.count()).select_from(models.PushSubscription)) or 0
    cap = subscription_ceiling() if workspace_id is not None else max(0, int(settings.push_subscriptions_max_total))
    if total >= cap:
        raise HTTPException(status_code=429, detail="Notification subscriptions are at capacity. Try again later.")
    now = datetime.now(timezone.utc)
    hour = int(now.timestamp()) // 3600
    if workspace_id is not None:
        limit, budget_key = WORKSPACE_REGISTRATIONS_PER_HOUR, f"push:registrations:w{int(workspace_id)}:{hour}"
    else:
        limit, budget_key = max(0, int(settings.push_subscriptions_max_new_per_hour)), f"push:registrations:{hour}"
    try:
        client = get_cache().redis_client
        if client is None:
            raise RuntimeError("Push registration budget unavailable")
        count = int(client.eval(_CONSUME_BUDGET, 1, budget_key))
    except Exception:
        if settings.is_production_like:
            logger.warning("push registration budget unavailable", exc_info=True)
            raise HTTPException(status_code=503, detail="Notifications are temporarily unavailable. Try again later.") from None
        # Local development may run without Redis. Production always uses the
        # durable budget, which survives unsubscribe/re-register churn.
        count = 1 + (db.scalar(select(func.count()).select_from(models.PushSubscription).where(
            models.PushSubscription.created_at >= now.replace(minute=0, second=0, microsecond=0),
        )) or 0)
    if count > limit:
        raise HTTPException(status_code=429, detail="Too many new notification subscriptions. Try again later.")
