from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db import models
from app.services.auto_scout.scouting import generate_auto_scout_drafts_for_match
from app.services.auto_scout.specs import mapper_version_for_season
from app.services.events.pipeline import ANALYSIS_VERSION

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def backfill_missing_auto_scout_drafts(
    db: Session,
    *,
    season_year: int | None = None,
    event_key: str | None = None,
    max_runs: int = 20,
    max_drafts: int = 120,
    lookback_hours: int | None = 168,
) -> dict[str, Any]:
    # Reliability net for matches analyzed before the post-analysis hook shipped, and
    # for any hook failure. Walks recent completed runs, finds slots with no draft tied
    # to the run, and fills only those. generate_auto_scout_drafts_for_match is idempotent
    # for slots that already have a draft, so re-running is safe.
    normalized_event_key = str(event_key or "").strip().lower() or None

    query = (
        db.query(models.AnalysisRun, models.AnalysisRunContext)
        .join(models.AnalysisRunContext, models.AnalysisRunContext.run_id == models.AnalysisRun.id)
        .filter(models.AnalysisRun.status == "completed")
    )
    if normalized_event_key is not None:
        query = query.filter(models.AnalysisRunContext.event_key == normalized_event_key)
    if isinstance(lookback_hours, int) and lookback_hours > 0:
        cutoff = _utc_now() - timedelta(hours=int(lookback_hours))
        query = query.filter(models.AnalysisRun.created_at >= cutoff)

    rows = (
        query.order_by(models.AnalysisRun.created_at.desc(), models.AnalysisRun.id.desc())
        .limit(max(1, int(max_runs)) * 8)
        .all()
    )

    summary: dict[str, Any] = {
        "runs_considered": 0,
        "runs_processed": 0,
        "drafts_created": 0,
        "matches_with_missing": 0,
        "errors": [],
    }

    seen_matches: set[str] = set()
    event_year_cache: dict[str, int | None] = {}

    for run, context in rows:
        if summary["runs_processed"] >= int(max_runs) or summary["drafts_created"] >= int(max_drafts):
            break
        match_key = str(run.match_key or "").strip().lower()
        ctx_event_key = str(getattr(context, "event_key", "") or "").strip().lower()
        if not match_key or not ctx_event_key or match_key in seen_matches:
            continue
        seen_matches.add(match_key)
        summary["runs_considered"] += 1

        # Skip stale analysis versions — those would trigger a re-queue, not a backfill.
        run_analysis_version = str(getattr(context, "analysis_version", "") or run.version or "")
        if run_analysis_version and run_analysis_version != ANALYSIS_VERSION:
            continue

        if ctx_event_key not in event_year_cache:
            event = db.get(models.Event, ctx_event_key)
            event_year_cache[ctx_event_key] = int(event.year) if event is not None and event.year is not None else None
        year = event_year_cache[ctx_event_key]
        if year is None:
            continue
        if isinstance(season_year, int) and int(year) != int(season_year):
            continue
        mapper_version = mapper_version_for_season(int(year))

        match_teams = (
            db.query(models.MatchTeam.team_key)
            .filter(models.MatchTeam.match_key == match_key)
            .all()
        )
        if not match_teams:
            continue
        team_keys = {str(team_key).strip().lower() for (team_key,) in match_teams if team_key}

        present = (
            db.query(models.AutoScoutDraft.team_key)
            .filter(
                models.AutoScoutDraft.match_key == match_key,
                models.AutoScoutDraft.mapper_version == mapper_version,
                models.AutoScoutDraft.analysis_run_id == run.id,
                models.AutoScoutDraft.superseded_at.is_(None),
            )
            .all()
        )
        present_teams = {str(team_key).strip().lower() for (team_key,) in present if team_key}
        missing = team_keys - present_teams
        if not missing:
            continue

        summary["matches_with_missing"] += 1
        try:
            result = generate_auto_scout_drafts_for_match(
                db,
                event_key=ctx_event_key,
                match_key=match_key,
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-match failures
            db.rollback()
            summary["errors"].append({"match_key": match_key, "error": str(exc)})
            continue

        summary["runs_processed"] += 1
        summary["drafts_created"] += int(result.get("created_count") or 0)

    logger.info(
        "auto_scout.backfill_completed runs_considered=%s runs_processed=%s drafts_created=%s "
        "matches_with_missing=%s errors=%s",
        summary["runs_considered"],
        summary["runs_processed"],
        summary["drafts_created"],
        summary["matches_with_missing"],
        len(summary["errors"]),
    )
    return summary
