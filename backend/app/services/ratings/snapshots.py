# Rating time-series helpers: record snapshots on recompute and compute the
# trend/momentum data the live UI renders (sparkline points, delta, direction).
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db import models

# How many recent snapshots the sparkline draws, and what counts as a
# meaningful move (smaller deltas render flat to avoid jitter from noise).
TREND_SPARKLINE_POINTS = 12
TREND_FLAT_EPSILON = 0.25


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
    captured_at = datetime.now(timezone.utc)
    written = 0
    for row in rating_rows:
        db.add(
            models.RatingSnapshot(
                event_key=event_key,
                team_key=row.team_key,
                rating_0_100=float(row.rating_0_100),
                confidence_0_1=float(row.confidence_0_1 or 0.0),
                findings_count=int(findings_by_team.get(row.team_key, 0) or 0),
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
    rows = (
        db.query(
            models.RatingSnapshot.team_key,
            models.RatingSnapshot.rating_0_100,
            models.RatingSnapshot.captured_at,
        )
        .filter(models.RatingSnapshot.event_key == event_key)
        .order_by(
            models.RatingSnapshot.team_key.asc(),
            models.RatingSnapshot.captured_at.asc(),
        )
        .all()
    )

    series: dict[str, list[float]] = defaultdict(list)
    for team_key, rating, _captured_at in rows:
        series[team_key].append(float(rating))

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
            "snapshot_count": len(values),
            "sparkline": [round(v, 2) for v in spark],
            "min_0_100": round(min(spark), 2),
            "max_0_100": round(max(spark), 2),
        }
    return trends


def prune_rating_snapshots(
    db: Session,
    *,
    older_than_days: int = 30,
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
