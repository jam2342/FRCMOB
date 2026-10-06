# Event refresh: re-ingest one event's official data, then rebuild what depends on it.

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import sanitize_external_error
from app.services.ml.shadow import auto_train_shadow_models_for_event_breakdown
from app.services.ml.synergy import QUALITY_THRESHOLD_DEFAULT, SYNERGY_MODEL_VERSION, precompute_event_synergy
from app.services.ratings.model import recompute_event_ratings

logger = logging.getLogger(__name__)


def train_shadow_models_after_refresh(db: Session, *, event_key: str) -> dict[str, Any]:
    if not bool(settings.ml_shadow_auto_train_on_event_breakdown):
        return {"triggered": False, "ok": False, "detail": "disabled"}
    try:
        result = auto_train_shadow_models_for_event_breakdown(
            db,
            event_key=event_key,
            limit_events=int(settings.ml_shadow_auto_train_limit_events or 40),
            source_version=None,
            activate=bool(settings.ml_shadow_auto_train_activate),
            replace_predictions=True,
        )
    except Exception as exc:
        db.rollback()
        return {
            "triggered": True,
            "ok": False,
            "detail": sanitize_external_error(exc, default="ML shadow auto-train failed."),
        }
    # A newly trained model feeds the ratings blend, so rebuild this event's ratings on it.
    if bool(result.get("ok")) and bool(settings.ml_shadow_auto_train_recompute_ratings):
        try:
            result["ratings_result"] = recompute_event_ratings(db, event_key)
            result["ratings_recomputed_after_train"] = True
        except Exception as exc:
            db.rollback()
            result["ratings_recomputed_after_train"] = False
            result["ratings_recompute_error"] = sanitize_external_error(
                exc, default="Ratings recompute after ML auto-train failed."
            )
    return result


def post_compute_event(
    db: Session,
    *,
    event_key: str,
    train_ml: bool,
    synergy_model_version: str = SYNERGY_MODEL_VERSION,
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    season_ready: bool = False,
) -> dict[str, Any]:
    try:
        synergy_result = precompute_event_synergy(
            db,
            event_key,
            model_version=synergy_model_version,
            quality_threshold=quality_threshold,
            season_ready=season_ready,
        )
    except Exception as exc:
        db.rollback()
        synergy_result = {"ok": False, "detail": sanitize_external_error(exc, default="Synergy precompute failed.")}

    try:
        ratings_result = recompute_event_ratings(db, event_key)
    except Exception as exc:
        db.rollback()
        ratings_result = {"ok": False, "detail": sanitize_external_error(exc, default="Ratings recompute failed.")}

    ml_shadow_result: dict[str, Any] = {"triggered": False, "ok": False, "detail": "skipped"}
    if train_ml:
        ml_shadow_result = train_shadow_models_after_refresh(db, event_key=event_key)
        if ml_shadow_result.get("ratings_recomputed_after_train"):
            ratings_result = ml_shadow_result.pop("ratings_result", ratings_result)
    ml_shadow_result.pop("ratings_result", None)

    return {"synergy": synergy_result, "ratings": ratings_result, "ml_shadow": ml_shadow_result}


def refresh_event(
    db: Session,
    *,
    event_key: str,
    run_post_compute: bool,
    synergy_model_version: str,
    quality_threshold: float,
    train_ml: bool = True,
) -> dict[str, Any]:
    from app.services.events.ingest import ingest_event as ingest_event_data
    from app.tba.client import TBAClient

    started = time.perf_counter()
    try:
        # Ingest skips its own rebuild: it would run synergy and ratings, then the
        # block below would run them again.
        ingest_result = ingest_event_data(event_key=event_key, tba=TBAClient(), db=db, run_post_compute=False)
    except Exception as exc:
        db.rollback()
        logger.error("event_refresh.ingest_failed event=%s error=%s", event_key, str(exc)[:200])
        return {
            "event_key": event_key,
            "status": "ingest_failed",
            "detail": sanitize_external_error(exc, default="Event ingest failed."),
        }

    post_compute: dict[str, Any] = {"ran": False}
    if run_post_compute:
        post_compute = {"ran": True, **post_compute_event(
            db,
            event_key=event_key,
            train_ml=train_ml,
            synergy_model_version=synergy_model_version,
            quality_threshold=quality_threshold,
        )}

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "event_refresh.completed event=%s elapsed_ms=%.1f ratings_ok=%s synergy_ok=%s ml_shadow_ok=%s",
        event_key,
        elapsed_ms,
        bool((post_compute.get("ratings") or {}).get("ok")),
        bool((post_compute.get("synergy") or {}).get("ok")),
        bool((post_compute.get("ml_shadow") or {}).get("ok")),
    )
    return {
        "event_key": event_key,
        "status": "processed",
        "ingest": ingest_result,
        "post_compute": post_compute,
        "elapsed_ms": elapsed_ms,
    }
