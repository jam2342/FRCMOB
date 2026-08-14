# Team stats endpoints (competitions, capability, event status, awards).
#
# Split from routes_teams.py — no business logic was changed.
# Endpoint handler functions only; all helpers remain in routes_teams.py.

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.season_config import CURRENT_SEASON_YEAR, PREVIOUS_SEASON_YEAR
from app.core.security import require_write_access
from app.db import models
from app.db.session import get_db
from app.services.intel.helpers import _normalize_team_key
from app.tba.client import TBAClient

import app.api.routes_teams as _rt

router = APIRouter(tags=["teams"])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class TeamCapabilityUpsertRequest(BaseModel):
    ball_capacity: int | None = Field(default=None, ge=0, le=30)
    notes: str | None = None

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{team_key}/competitions")
def get_team_competitions(
    team_key: str,
    event_key: str | None = None,
    preferred_year: int = CURRENT_SEASON_YEAR,
    fallback_year: int = PREVIOUS_SEASON_YEAR,
    db: Session = Depends(get_db),
):
    normalized_team_key = _normalize_team_key(team_key)
    context_year = _rt._event_year_from_key(event_key)
    tba = TBAClient() if settings.tba_auth_key.strip() else None

    selected_year, registered_events, source = _rt._team_registered_events(
        db=db,
        team_key=normalized_team_key,
        preferred_year=preferred_year,
        fallback_year=fallback_year,
        tba=tba,
        primary_year=context_year,
    )

    return {
        "ok": True,
        "team_key": normalized_team_key,
        "event_key": event_key,
        "registration_year": selected_year,
        "registered_events_count": len(registered_events),
        "registered_events_source": source,
        "registered_events": registered_events,
    }

@router.get("/{team_key}/capability")
def get_team_capability(team_key: str, db: Session = Depends(get_db)):
    normalized_team_key = _normalize_team_key(team_key)
    capability = db.get(models.TeamStaticCapability, normalized_team_key)
    return {
        "ok": True,
        "team_key": normalized_team_key,
        "capability": _rt._serialize_team_capability(capability),
    }

@router.get("/{team_key}/event/{event_key}/status")
def get_team_event_status(team_key: str, event_key: str):
    normalized_team_key = _normalize_team_key(team_key)
    tba = _rt._get_tba_client_or_503()
    try:
        payload = tba.team_event_status(normalized_team_key, event_key)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to fetch TBA team event status for {normalized_team_key} at {event_key}",
        ) from exc
    return {
        "ok": True,
        "team_key": normalized_team_key,
        "event_key": event_key,
        "source": "tba",
        "status": payload if isinstance(payload, dict) else None,
    }

@router.get("/{team_key}/events/{year}/statuses")
def get_team_event_statuses_for_year(team_key: str, year: int):
    normalized_team_key = _normalize_team_key(team_key)
    tba = _rt._get_tba_client_or_503()
    try:
        payload = tba.team_events_statuses(normalized_team_key, year)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to fetch TBA event statuses for {normalized_team_key} in {year}",
        ) from exc
    statuses = payload if isinstance(payload, dict) else {}
    return {
        "ok": True,
        "team_key": normalized_team_key,
        "year": year,
        "source": "tba",
        "count": len(statuses),
        "statuses": statuses,
    }

@router.get("/{team_key}/awards/{year}")
def get_team_awards_for_year(team_key: str, year: int):
    normalized_team_key = _normalize_team_key(team_key)
    tba = _rt._get_tba_client_or_503()
    try:
        payload = tba.team_awards_year(normalized_team_key, year)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to fetch TBA awards for {normalized_team_key} in {year}",
        ) from exc
    awards = payload if isinstance(payload, list) else []
    return {
        "ok": True,
        "team_key": normalized_team_key,
        "year": year,
        "source": "tba",
        "count": len(awards),
        "awards": awards,
    }

@router.put("/{team_key}/capability")
def upsert_team_capability(
    team_key: str,
    payload: TeamCapabilityUpsertRequest,
    db: Session = Depends(get_db),
):
    require_write_access("Team capability update")
    normalized_team_key = _normalize_team_key(team_key)
    team = db.get(models.Team, normalized_team_key)
    if team is None:
        team_number = 0
        if normalized_team_key.startswith("frc"):
            suffix = normalized_team_key[3:]
            if suffix.isdigit():
                team_number = int(suffix)
        db.merge(
            models.Team(
                team_key=normalized_team_key,
                team_number=team_number,
                nickname=None,
            )
        )

    capability = db.get(models.TeamStaticCapability, normalized_team_key)
    if capability is None:
        capability = models.TeamStaticCapability(team_key=normalized_team_key)

    if "ball_capacity" in payload.model_fields_set:
        capability.ball_capacity = (
            int(payload.ball_capacity)
            if isinstance(payload.ball_capacity, int) and payload.ball_capacity > 0
            else None
        )
    if "notes" in payload.model_fields_set:
        notes = (payload.notes or "").strip()
        capability.notes = notes if notes else None

    db.merge(capability)
    db.commit()
    refreshed = db.get(models.TeamStaticCapability, normalized_team_key)
    return {
        "ok": True,
        "team_key": normalized_team_key,
        "capability": _rt._serialize_team_capability(refreshed),
    }
