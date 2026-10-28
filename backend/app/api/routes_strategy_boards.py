# A match's plan drawn on the field (Strategy Briefing → Plan on the field), private to the
# team workspace that drew it. Same concurrency model as picklists: each write echoes the
# `version` it loaded, and a stale one gets the current board back instead of overwriting it.

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.db.session import get_db
from app.services.workspaces import require_workspace_actor, require_workspace_writer

router = APIRouter(prefix="/strategy-boards", tags=["strategy-boards"])

_MATCH_KEY_RE = re.compile(r"^(?P<event>\d{4}[a-z0-9]{1,32})_(?:qm\d{1,4}|(?:ef|qf|sf|f)\d{1,2}m\d{1,2})$")
TOOLS = {"pen", "arrow", "zone"}
COLORS = {"ours", "theirs", "note"}
MAX_STROKES = 150
MAX_POINTS = 400


def _match_key(raw: str) -> tuple[str, str]:
    key = str(raw or "").strip().lower()
    match = _MATCH_KEY_RE.match(key)
    if match is None:
        raise HTTPException(status_code=400, detail="Invalid match key")
    return key, match.group("event")


def _normalize_strokes(raw: Any) -> list[dict[str, Any]]:
    # Field fractions, three decimals (a few centimetres on a field): plenty for a plan, and a
    # board stays a few kilobytes. Anything unrecognised is dropped, not stored.
    if not isinstance(raw, list):
        return []
    strokes: list[dict[str, Any]] = []
    for item in raw[:MAX_STROKES]:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "")
        color = str(item.get("color") or "")
        points_raw = item.get("points")
        if tool not in TOOLS or color not in COLORS or not isinstance(points_raw, list):
            continue
        points: list[list[float]] = []
        for point in points_raw[:MAX_POINTS]:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            try:
                x, y = float(point[0]), float(point[1])
            except (TypeError, ValueError):
                continue
            if x != x or y != y:  # NaN
                continue
            points.append([round(min(1.0, max(0.0, x)), 3), round(min(1.0, max(0.0, y)), 3)])
        if len(points) < (1 if tool == "pen" else 2):
            continue
        if tool != "pen":
            points = [points[0], points[-1]]
        strokes.append({"tool": tool, "color": color, "points": points})
    return strokes


def _serialize(row: models.StrategyBoard | None, match_key: str) -> dict[str, Any]:
    if row is None:
        return {"match_key": match_key, "strokes": [], "version": 0, "updated_by": None, "updated_at": None}
    return {
        "match_key": row.match_key,
        "strokes": row.strokes if isinstance(row.strokes, list) else [],
        "version": int(row.version or 1),
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class StrategyBoardUpdate(BaseModel):
    # The version this edit was made on; 0 for a board nobody has drawn on yet.
    version: int = Field(ge=0)
    strokes: list[Any] = Field(default_factory=list, max_length=MAX_STROKES * 2)


@router.get("/{match_key}")
def get_strategy_board(match_key: str, request: Request, db: Session = Depends(get_db)):
    actor = require_workspace_actor(request, db)
    key, _event = _match_key(match_key)
    row = db.execute(
        select(models.StrategyBoard).where(
            models.StrategyBoard.workspace_id == actor.workspace_id, models.StrategyBoard.match_key == key
        )
    ).scalar_one_or_none()
    return {"ok": True, "board": _serialize(row, key)}


@router.put("/{match_key}")
def put_strategy_board(match_key: str, payload: StrategyBoardUpdate, request: Request, db: Session = Depends(get_db)):
    actor = require_workspace_writer(request, db)
    key, event_key = _match_key(match_key)
    row = db.execute(
        select(models.StrategyBoard)
        .where(models.StrategyBoard.workspace_id == actor.workspace_id, models.StrategyBoard.match_key == key)
        .with_for_update()
    ).scalar_one_or_none()
    current = int(row.version or 1) if row is not None else 0
    if payload.version != current:
        return {"ok": False, "conflict": True, "detail": "Someone else changed this plan. Here is theirs.", "board": _serialize(row, key)}
    strokes = _normalize_strokes(payload.strokes)
    if row is None:
        row = models.StrategyBoard(workspace_id=actor.workspace_id, event_key=event_key, match_key=key, version=1)
        db.add(row)
    else:
        row.version = current + 1
    row.strokes = strokes
    row.updated_by = actor.member.display_name
    db.commit()
    db.refresh(row)
    return {"ok": True, "board": _serialize(row, key)}
