# Rating time-series helpers: record snapshots on recompute and compute the
# trend/momentum data the live UI renders (sparkline points, delta, direction).
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.db import models

# How many recent snapshots the sparkline draws, and what counts as a
# meaningful move (smaller deltas render flat to avoid jitter from noise).
TREND_SPARKLINE_POINTS = 12
TREND_FLAT_EPSILON = 0.25
# Snapshots older than this are pruned once a day.
SNAPSHOT_RETENTION_DAYS = 30


def record_event_rating_snapshots(
    db: Session,
    event_key: str,
    *,
    findings_count_by_team: dict[str, int] | None = None,
) -> int:
    # Append one snapshot row per team from the just-committed EventTeamRating
    # rows. Returns the number of snapshot rows written. Best-effort: callers
    # should not let a snapshot failure abort the recompute, so this commits
    # independently and the caller wraps it in try/except.
    rating_rows = (
        db.query(models.EventTeamRating)
        .filter(models.EventTeamRating.event_key == event_key)
        .all()
    )
    if not rating_rows:
        return 0

    findings_by_team = findings_count_by_team or {}
    # Only a change is a new point: recomputes of a finished event used to append
    # identical rows for every team (2026cmptx reached ~112k rows).
    latest_at = (
        db.query(models.RatingSnapshot.team_key, func.max(models.RatingSnapshot.captured_at).label("at"))
        .filter(models.RatingSnapshot.event_key == event_key)
        .group_by(models.RatingSnapshot.team_key)
        .subquery()
    )
    previous = {
        team_key: (float(rating), int(findings or 0))
        for team_key, rating, findings in db.query(
            models.RatingSnapshot.team_key, models.RatingSnapshot.rating_0_100, models.RatingSnapshot.findings_count
        )
        .join(latest_at, and_(models.RatingSnapshot.team_key == latest_at.c.team_key, models.RatingSnapshot.captured_at == latest_at.c.at))
        .filter(models.RatingSnapshot.event_key == event_key)
    }
    captured_at = datetime.now(timezone.utc)
    written = 0
    for row in rating_rows:
        findings = int(findings_by_team.get(row.team_key, 0) or 0)
        last = previous.get(row.team_key)
        if last is not None and abs(last[0] - float(row.rating_0_100)) < 1e-6 and last[1] == findings:
            continue
        db.add(
            models.RatingSnapshot(
                event_key=event_key,
                team_key=row.team_key,
                rating_0_100=float(row.rating_0_100),
                confidence_0_1=float(row.confidence_0_1 or 0.0),
                findings_count=findings,
                model_version=str(row.model_version or "rating_v1"),
                captured_at=captured_at,
            )
        )
        written += 1
    db.commit()
    return written


def _direction(delta: float) -> str:
    if delta > TREND_FLAT_EPSILON:
        return "up"
    if delta < -TREND_FLAT_EPSILON:
        return "down"
    return "flat"


def compute_event_rating_trends(
    db: Session,
    event_key: str,
    *,
    points: int = TREND_SPARKLINE_POINTS,
) -> dict[str, dict[str, Any]]:
    # Build per-team trend payloads for every team in the event in a single
    # query. Each payload: latest value, delta vs the previous snapshot,
    # direction, total snapshot count, and up to ``points`` sparkline values
    # (oldest-first). Teams with a single snapshot get a flat, zero-delta trend.
    # Only the last `points` per team are read; the total count comes from the window.
    ranked = (
        db.query(
            models.RatingSnapshot.team_key.label("team_key"),
            models.RatingSnapshot.rating_0_100.label("rating"),
            models.RatingSnapshot.captured_at.label("captured_at"),
            func.row_number()
            .over(partition_by=models.RatingSnapshot.team_key, order_by=models.RatingSnapshot.captured_at.desc())
            .label("rn"),
            func.count().over(partition_by=models.RatingSnapshot.team_key).label("total"),
        )
        .filter(models.RatingSnapshot.event_key == event_key)
        .subquery()
    )
    rows = (
        db.query(ranked.c.team_key, ranked.c.rating, ranked.c.total)
        .filter(ranked.c.rn <= max(2, int(points)))
        .order_by(ranked.c.team_key.asc(), ranked.c.captured_at.asc())
        .all()
    )

    series: dict[str, list[float]] = defaultdict(list)
    totals: dict[str, int] = {}
    for team_key, rating, total in rows:
        series[team_key].append(float(rating))
        totals[team_key] = int(total)

    trends: dict[str, dict[str, Any]] = {}
    for team_key, values in series.items():
        if not values:
            continue
        latest = values[-1]
        previous = values[-2] if len(values) >= 2 else latest
        delta = round(latest - previous, 2)
        spark = values[-points:]
        trends[team_key] = {
            "latest_0_100": round(latest, 2),
            "previous_0_100": round(previous, 2),
            "delta": delta,
            "direction": _direction(delta),
            "snapshot_count": totals.get(team_key, len(values)),
            "sparkline": [round(v, 2) for v in spark],
            "min_0_100": round(min(spark), 2),
            "max_0_100": round(max(spark), 2),
        }
    return trends


def prune_rating_snapshots(
    db: Session,
    *,
    older_than_days: int = SNAPSHOT_RETENTION_DAYS,
) -> int:
    # Drop snapshots older than the retention window so the table stays small.
    # Returns rows deleted. Intended for a periodic maintenance tick, not the
    # hot recompute path.
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(older_than_days)))
    deleted = (
        db.query(models.RatingSnapshot)
        .filter(models.RatingSnapshot.captured_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted or 0)
