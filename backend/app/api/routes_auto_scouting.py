from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import require_write_access
from app.db import models
from app.db.session import get_db
from app.services.auto_scout.scouting import (
    _draft_support_payload,
    _serialize_draft,
    approve_auto_scout_draft,
    generate_auto_scout_draft,
    get_auto_scout_approval_telemetry,
    get_auto_scout_draft,
    list_event_auto_scout_drafts,
    reject_auto_scout_draft,
)


router = APIRouter(prefix="/scouting/auto-drafts", tags=["auto-scouting"])


class AutoScoutDraftGenerateRequest(BaseModel):
    event_key: str
    match_key: str
    team_key: str
    force_regenerate: bool = False


class AutoScoutDraftApproveRequest(BaseModel):
    approved_by: str
    edited_payload: dict[str, Any] = Field(default_factory=dict)
    draft_version: int = Field(ge=1)


class AutoScoutDraftRejectRequest(BaseModel):
    reason: str
    draft_version: int = Field(ge=1)


def _season_year_for_event(db: Session, event_key: str) -> int | None:
    event = db.get(models.Event, str(event_key or "").strip().lower())
    return int(event.year) if event is not None else None


@router.post("/generate")
def generate_draft(
    request: AutoScoutDraftGenerateRequest,
    db: Session = Depends(get_db),
):
    require_write_access("Auto-scout draft generation")
    row, created = generate_auto_scout_draft(
        db,
        event_key=request.event_key,
        match_key=request.match_key,
        team_key=request.team_key,
        force_regenerate=bool(request.force_regenerate),
    )
    season_year = _season_year_for_event(db, request.event_key)
    return {
        "ok": True,
        "created": created,
        "draft": _serialize_draft(row, season_year=season_year),
        "season_support": _draft_support_payload(season_year or 0) if season_year is not None else None,
    }


@router.get("/{event_key}/{match_key}/{team_key}")
def get_draft(
    event_key: str,
    match_key: str,
    team_key: str,
    db: Session = Depends(get_db),
):
    row = get_auto_scout_draft(
        db,
        event_key=event_key,
        match_key=match_key,
        team_key=team_key,
    )
    season_year = _season_year_for_event(db, event_key)
    return {
        "ok": True,
        "draft": _serialize_draft(row, season_year=season_year) if row is not None else None,
        "season_support": _draft_support_payload(season_year or 0) if season_year is not None else None,
    }


@router.get("/telemetry")
def get_approval_telemetry(
    event_key: str | None = None,
    season_year: int | None = Query(default=None, ge=2020, le=2100),
    mapper_version: str | None = None,
    max_rows: int = Query(default=2500, ge=100, le=10000),
    db: Session = Depends(get_db),
):
    return {
        "ok": True,
        "telemetry": get_auto_scout_approval_telemetry(
            db,
            event_key=event_key,
            season_year=season_year,
            mapper_version=mapper_version,
            max_rows=max_rows,
        ),
    }


@router.get("/event/{event_key}")
def get_event_draft_readiness(
    event_key: str,
    db: Session = Depends(get_db),
):
    rows = list_event_auto_scout_drafts(db, event_key=event_key)
    season_year = _season_year_for_event(db, event_key)
    return {
        "ok": True,
        "event_key": str(event_key or "").strip().lower(),
        "count": len(rows),
        "drafts": [_serialize_draft(row, season_year=season_year) for row in rows],
        "season_support": _draft_support_payload(season_year or 0) if season_year is not None else None,
    }


@router.post("/{draft_id}/approve")
def approve_draft(
    draft_id: int,
    request: AutoScoutDraftApproveRequest,
    db: Session = Depends(get_db),
):
    require_write_access("Auto-scout draft approval")
    row, overrides = approve_auto_scout_draft(
        db,
        draft_id=draft_id,
        draft_version=request.draft_version,
        approved_by=request.approved_by,
        edited_payload=request.edited_payload,
    )
    season_year = _season_year_for_event(db, row.event_key)
    return {
        "ok": True,
        "draft": _serialize_draft(row, season_year=season_year),
        "field_overrides": overrides,
        "approved_payload": row.approved_payload or {},
    }


@router.post("/{draft_id}/reject")
def reject_draft(
    draft_id: int,
    request: AutoScoutDraftRejectRequest,
    db: Session = Depends(get_db),
):
    require_write_access("Auto-scout draft rejection")
    row = reject_auto_scout_draft(
        db,
        draft_id=draft_id,
        draft_version=request.draft_version,
        reason=request.reason,
    )
    season_year = _season_year_for_event(db, row.event_key)
    return {
        "ok": True,
        "draft": _serialize_draft(row, season_year=season_year),
    }
