from __future__ import annotations

from typing import Literal

from sqlalchemy import exists
from sqlalchemy.orm import Session

from app.db import models

# "video" rows are history from the retired broadcast pipeline; nothing writes them now.
RunKind = Literal["video", "official_truth", "on_device"]

RUN_KIND_VIDEO: RunKind = "video"
RUN_KIND_OFFICIAL_TRUTH: RunKind = "official_truth"
RUN_KIND_ON_DEVICE: RunKind = "on_device"


def latest_completed_run(
    db: Session,
    *,
    match_key: str,
    run_kind: RunKind,
    analysis_version: str | None = None,
) -> tuple[models.AnalysisRun, models.AnalysisRunContext] | None:
    query = (
        db.query(models.AnalysisRun, models.AnalysisRunContext)
        .join(models.AnalysisRunContext, models.AnalysisRunContext.run_id == models.AnalysisRun.id)
        .filter(
            models.AnalysisRun.match_key == match_key,
            models.AnalysisRun.run_kind == run_kind,
            models.AnalysisRun.status == "completed",
        )
    )
    if analysis_version is not None:
        query = query.filter(models.AnalysisRunContext.analysis_version == analysis_version)
    return query.order_by(
        models.AnalysisRun.created_at.desc(), models.AnalysisRun.id.desc()
    ).first()


def best_on_device_run(
    db: Session,
    *,
    match_key: str,
    team_key: str | None = None,
    statuses: tuple[str, ...] = ("accepted",),
) -> tuple[models.AnalysisRun, models.OnDeviceSession] | None:
    # The highest-quality phone recording of a match that an operator has reviewed.
    # With team_key, only recordings that actually tracked that robot count.
    query = (
        db.query(models.AnalysisRun, models.OnDeviceSession)
        .join(models.OnDeviceSession, models.OnDeviceSession.analysis_run_id == models.AnalysisRun.id)
        .filter(
            models.OnDeviceSession.match_key == match_key,
            models.OnDeviceSession.status.in_(statuses),
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
