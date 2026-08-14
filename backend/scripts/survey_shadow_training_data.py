#!/usr/bin/env python3
# Pre-flight survey for shadow ML training: how much usable data is in this DB?
#
# The shadow trainer needs:
#   - TeamMatchFinding rows (from completed analyze_match)
#   - TeamMatchThroughput rows (sibling, written alongside)
#   - EventTeamRating rows (from recompute_event_ratings)
# All for the same event, current season, with enough volume to clear the
# 20-row-per-model training threshold.
#
# Run this BEFORE backfill_shadow_models.py to know what you're working with.
# Especially useful after a deploy when you suspect the prior season's
# pipeline was degraded and didn't write findings reliably.
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func

from app.db import models
from app.db.session import SessionLocal


def _season_year_from_event_key(event_key: str) -> int | None:
    raw = (event_key or "")[:4]
    try:
        return int(raw)
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Survey the DB for shadow-ML-trainable data, by event."
    )
    parser.add_argument(
        "--season",
        type=int,
        default=datetime.now(timezone.utc).year,
        help="Season year to inspect (default: current year).",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=20,
        help="Threshold the shadow trainer uses per model (default: 20).",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Per-event tallies of the three things the trainer needs.
        rows = (
            db.query(
                models.TeamMatchFinding.event_key,
                func.count(models.TeamMatchFinding.id),
            )
            .group_by(models.TeamMatchFinding.event_key)
            .all()
        )
        per_event: dict[str, dict[str, int]] = {}
        for event_key, finding_count in rows:
            if _season_year_from_event_key(event_key) != args.season:
                continue
            per_event[event_key] = {"findings": int(finding_count or 0)}

        if not per_event:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "season": args.season,
                        "events_with_findings": 0,
                        "message": (
                            "No events have any TeamMatchFinding rows for this "
                            "season. Either the pipeline never ran (check the "
                            "YouTube/worker chain), or the season key prefix "
                            "differs from the year. Run analyze_match on at "
                            "least a few matches before backfilling."
                        ),
                    },
                    indent=2,
                )
            )
            return 1

        # Augment with throughput + ratings counts (the other requirements).
        throughput_rows = (
            db.query(
                models.TeamMatchThroughput.event_key,
                func.count(models.TeamMatchThroughput.finding_id),
            )
            .filter(models.TeamMatchThroughput.event_key.in_(per_event.keys()))
            .group_by(models.TeamMatchThroughput.event_key)
            .all()
        )
        for event_key, count in throughput_rows:
            per_event[event_key]["throughput"] = int(count or 0)

        # Pre-existing ratings (informational).
        rating_rows = (
            db.query(
                models.EventTeamRating.event_key,
                func.count(models.EventTeamRating.team_key),
            )
            .filter(models.EventTeamRating.event_key.in_(per_event.keys()))
            .group_by(models.EventTeamRating.event_key)
            .all()
        )
        for event_key, count in rating_rows:
            per_event[event_key]["ratings_existing"] = int(count or 0)

        # team_strength snapshots are produced AT BACKFILL TIME by
        # recompute_event_ratings, one per (event, distinct team) — so the
        # potential pool size = distinct teams per event from MatchTeam. This
        # matters because the survey runs BEFORE ratings are recomputed.
        team_rows = (
            db.query(
                models.MatchTeam.event_key,
                func.count(func.distinct(models.MatchTeam.team_key)),
            )
            .filter(models.MatchTeam.event_key.in_(per_event.keys()))
            .group_by(models.MatchTeam.event_key)
            .all()
        )
        for event_key, count in team_rows:
            per_event[event_key]["team_strength_rows_after_backfill"] = int(count or 0)

        # Match-outcome rows per event ≈ count of distinct (match_key, alliance)
        # pairs with throughput data; this is what the trainer feeds the
        # match-outcome head.
        mo_rows = (
            db.query(
                models.TeamMatchThroughput.event_key,
                func.count(
                    func.distinct(
                        func.concat(
                            models.TeamMatchThroughput.match_key,
                            ":",
                            models.TeamMatchFinding.alliance,
                        )
                    )
                ),
            )
            .join(
                models.TeamMatchFinding,
                models.TeamMatchFinding.id == models.TeamMatchThroughput.finding_id,
            )
            .filter(models.TeamMatchThroughput.event_key.in_(per_event.keys()))
            .group_by(models.TeamMatchThroughput.event_key)
            .all()
        )
        for event_key, count in mo_rows:
            per_event[event_key]["match_outcome_rows"] = int(count or 0)

        # Aggregate totals — the cumulative pool the trainer would see after
        # backfill_shadow_models.py runs (which recomputes ratings first).
        total_team_strength = sum(
            d.get("team_strength_rows_after_backfill", 0)
            for d in per_event.values()
        )
        total_match_outcome = sum(
            d.get("match_outcome_rows", 0) for d in per_event.values()
        )
        events_sorted = sorted(
            per_event.items(),
            key=lambda kv: kv[1].get("findings", 0),
            reverse=True,
        )

        verdict = {
            "team_strength_pool_clears_threshold": total_team_strength >= args.min_rows,
            "match_outcome_pool_clears_threshold": total_match_outcome
            >= args.min_rows,
        }
        print(
            json.dumps(
                {
                    "ok": all(verdict.values()),
                    "season": args.season,
                    "min_rows_threshold": args.min_rows,
                    "events_with_findings": len(per_event),
                    "cumulative_pool": {
                        "team_strength_rows_available": total_team_strength,
                        "match_outcome_rows_available": total_match_outcome,
                    },
                    "verdict": verdict,
                    "next_step": (
                        "Run backfill_shadow_models.py — pool is large enough "
                        "to train immediately."
                        if all(verdict.values())
                        else (
                            "Pool too small to train. Either run analyze_match "
                            "on more events first, or wait for more matches "
                            "to be ingested."
                        )
                    ),
                    "top_events": [
                        {"event_key": ek, **counts}
                        for ek, counts in events_sorted[:10]
                    ],
                },
                indent=2,
            )
        )
        return 0 if all(verdict.values()) else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
