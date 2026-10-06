# Team media endpoints (robot image, team logo).
#
# Split from routes_teams.py — no business logic was changed.
# Media helper functions + endpoint handlers.

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.season_config import CURRENT_SEASON_YEAR, PREVIOUS_SEASON_YEAR
from app.db import models
from app.db.session import get_db
from app.services.intel.helpers import _normalize_team_key
from app.tba.client import TBAClient

import app.api.routes_teams as _rt

router = APIRouter(tags=["teams"])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Media helper functions (only used by media endpoints)
# ---------------------------------------------------------------------------

def _media_image_url(media: dict[str, Any]) -> str | None:
    direct_url = media.get("direct_url")
    if isinstance(direct_url, str) and direct_url.strip():
        lowered = direct_url.lower()
        if not lowered.endswith(".mp4"):
            return direct_url

    details = media.get("details")
    media_type = media.get("type")
    foreign_key = media.get("foreign_key")

    if media_type == "imgur":
        if isinstance(details, dict):
            image_partial = details.get("image_partial")
            if isinstance(image_partial, str) and image_partial.strip():
                return f"https://i.imgur.com/{image_partial}"
        if isinstance(foreign_key, str) and foreign_key.strip():
            return f"https://i.imgur.com/{foreign_key}.jpg"

    if media_type == "instagram-image" and isinstance(details, dict):
        image_url = details.get("image_url")
        if isinstance(image_url, str) and image_url.strip():
            return image_url

    if media_type == "cdphotothread" and isinstance(details, dict):
        image_url = details.get("image_url")
        if isinstance(image_url, str) and image_url.strip():
            return image_url

    if media_type == "youtube" and isinstance(foreign_key, str) and foreign_key.strip():
        return f"https://img.youtube.com/vi/{foreign_key}/hqdefault.jpg"

    view_url = media.get("view_url")
    if isinstance(view_url, str) and view_url.strip():
        lowered = view_url.lower()
        if lowered.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return view_url

    return None

def _pick_media(
    media_rows: list[dict[str, Any]],
    url_for: Callable[[dict[str, Any]], str | None],
    default_type: str | None = None,
) -> tuple[str, str | None, str | None] | None:
    # TBA's "preferred" media first, then anything usable.
    for preferred_only in (True, False):
        for media in media_rows:
            if not isinstance(media, dict) or (preferred_only and not bool(media.get("preferred"))):
                continue
            url = url_for(media)
            if url:
                media_type = media.get("type")
                view_url = media.get("view_url")
                return (
                    url,
                    media_type if isinstance(media_type, str) else default_type,
                    view_url if isinstance(view_url, str) else None,
                )
    return None


def _select_robot_image(media_rows: list[dict[str, Any]]) -> tuple[str, str | None, str | None] | None:
    return _pick_media(media_rows, _media_image_url)


def _avatar_logo_url(media: dict[str, Any]) -> str | None:
    if media.get("type") != "avatar":
        return None

    direct_url = media.get("direct_url")
    if isinstance(direct_url, str) and direct_url.strip():
        return direct_url

    details = media.get("details")
    if isinstance(details, dict):
        base64_image = details.get("base64Image")
        if isinstance(base64_image, str) and base64_image.strip():
            encoded = base64_image.strip()
            if encoded.startswith("data:image"):
                return encoded
            return f"data:image/png;base64,{encoded}"
    return None

def _select_team_logo(media_rows: list[dict[str, Any]]) -> tuple[str, str | None, str | None] | None:
    # The team avatar, else any static image.
    return _pick_media(media_rows, _avatar_logo_url, "avatar") or _pick_media(media_rows, _media_image_url)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _team_media_payload(
    db: Session,
    *,
    team_key: str,
    event_key: str | None,
    preferred_year: int,
    fallback_year: int,
    select: Callable[[list[dict[str, Any]]], tuple[str, str | None, str | None] | None],
    kind: str,
    missing_reason: str,
) -> dict[str, Any]:
    normalized_team_key = _normalize_team_key(team_key)
    latest_local_year = db.query(func.max(models.Event.year)).scalar()
    years = _rt._media_candidate_years(
        _rt._candidate_years(
            event_key=event_key,
            preferred_year=preferred_year,
            fallback_year=fallback_year,
            latest_local_year=int(latest_local_year) if latest_local_year is not None else None,
        )
    )
    payload: dict[str, Any] = {
        "ok": True,
        "team_key": normalized_team_key,
        "event_key": event_key,
        "available": False,
        "year": years[0] if years else None,
        "image_url": None,
        "media_type": None,
        "view_url": None,
        "source": "none",
        "reason": missing_reason,
    }
    if not settings.tba_auth_key.strip():
        return payload

    tba = TBAClient()
    for year in years:
        try:
            team_media = tba.team_media(normalized_team_key, year)
        except Exception as exc:
            logger.warning("Failed to fetch TBA %s media for %s in %s: %s", kind, normalized_team_key, year, exc)
            continue
        selected = select(team_media) if isinstance(team_media, list) else None
        if selected is None:
            continue
        image_url, media_type, view_url = selected
        return {
            **payload,
            "available": True,
            "year": year,
            "image_url": image_url,
            "media_type": media_type,
            "view_url": view_url,
            "source": "tba",
            "reason": None,
        }
    return payload


@router.get("/{team_key}/robot-image")
def get_team_robot_image(
    team_key: str,
    event_key: str | None = None,
    preferred_year: int = CURRENT_SEASON_YEAR,
    fallback_year: int = PREVIOUS_SEASON_YEAR,
    db: Session = Depends(get_db),
):
    return _team_media_payload(
        db,
        team_key=team_key,
        event_key=event_key,
        preferred_year=preferred_year,
        fallback_year=fallback_year,
        select=_select_robot_image,
        kind="robot",
        missing_reason="A picture of the robot isn't available.",
    )


@router.get("/{team_key}/logo")
def get_team_logo(
    team_key: str,
    event_key: str | None = None,
    preferred_year: int = CURRENT_SEASON_YEAR,
    fallback_year: int = PREVIOUS_SEASON_YEAR,
    db: Session = Depends(get_db),
):
    return _team_media_payload(
        db,
        team_key=team_key,
        event_key=event_key,
        preferred_year=preferred_year,
        fallback_year=fallback_year,
        select=_select_team_logo,
        kind="logo",
        missing_reason="Team logo is not available.",
    )
