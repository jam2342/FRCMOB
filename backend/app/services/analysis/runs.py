from __future__ import annotations

from typing import Literal

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session

from app.db import models

# "video" rows are history from the retired broadcast pipeline; nothing writes them now.
RunKind = Literal["video", "official_truth", "on_device"]

RUN_KIND_VIDEO: RunKind = "video"
RUN_KIND_OFFICIAL_TRUTH: RunKind = "official_truth"
RUN_KIND_ON_DEVICE: RunKind = "on_device"


def visible_sessions(statuses: tuple[str, ...] = ("accepted",), workspace_id: int | None = None):
    # Reviewed recordings are public. A team also sees its own recordings before
    # anyone reviews them; they stay out of every other visitor's view.
    clause = models.OnDeviceSession.status.in_(statuses)
    if workspace_id is None:
        return clause
    return or_(
        clause,
        and_(
            models.OnDeviceSession.workspace_id == workspace_id,
            models.OnDeviceSession.status == "provisional",
        ),
    )


def best_on_device_run(
    db: Session,
    *,
    match_key: str,
    team_key: str | None = None,
    statuses: tuple[str, ...] = ("accepted",),
    workspace_id: int | None = None,
) -> tuple[models.AnalysisRun, models.OnDeviceSession] | None:
    # The highest-quality phone recording of a match that an operator has reviewed
    # (or, with workspace_id, that this team recorded). With team_key, only
    # recordings that actually tracked that robot count.
    query = (
        db.query(models.AnalysisRun, models.OnDeviceSession)
        .join(models.OnDeviceSession, models.OnDeviceSession.analysis_run_id == models.AnalysisRun.id)
        .filter(
            models.OnDeviceSession.match_key == match_key,
            visible_sessions(statuses, workspace_id),
            models.AnalysisRun.run_kind == RUN_KIND_ON_DEVICE,
            models.AnalysisRun.status == "completed",
        )
    )
    if team_key:
        query = query.filter(
            exists().where(
                models.RobotTrack.analysis_run_id == models.AnalysisRun.id,
                models.RobotTrack.team_key == team_key,
            )
        )
    return query.order_by(
        models.OnDeviceSession.quality_score.desc(), models.AnalysisRun.id.desc()
    ).first()
