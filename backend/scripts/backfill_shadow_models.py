#!/usr/bin/env python3
# One-shot shadow-model backfill across an entire season.
#
# Production normally lets the regional automation scheduler trigger
# auto_train_shadow_models_for_event_breakdown after each event completes,
# which means the first ~2 events of a fresh season ship deterministic-only
# predictions (the trainer needs ≥20 labeled rows per model).
#
# That's fine mid-season. After a deploy with months of accumulated data
# already in the DB, waiting for "the next event" is silly — train once across
# everything and materialize ML predictions for every event immediately.
#
# What this does, for each event in --season that has findings:
#   1. recompute_event_ratings  (required snapshot input)
#   2. precompute_event_synergy (required snapshot input)
#   3. auto_train_shadow_models_for_event_breakdown
#        -> exports snapshots, trains team_strength + match_outcome models on
#           the cumulative pool (capped by --limit-events), activates them,
#           materializes ml_shadow_predictions for this event_key
# Idempotent: re-running overwrites predictions (replace_predictions=True).
#
# Run BEFORE this: survey_shadow_training_data.py (confirms pool is big enough).
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

from app.core.config import settings
from app.db import models
from app.db.session import SessionLocal
from app.services.ml.shadow import auto_train_shadow_models_for_event_breakdown
from app.services.ml.synergy import SYNERGY_MODEL_VERSION, precompute_event_synergy
from app.services.ratings.model import recompute_event_ratings


def _season_year_from_event_key(event_key: str) -> int | None:
    raw = (event_key or "")[:4]
    try:
        return int(raw)
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-shot shadow-model backfill across a whole season."
    )
    parser.add_argument(
        "--season",
        type=int,
        default=datetime.now(timezone.utc).year,
        help="Season year to backfill (default: current year).",
    )
    parser.add_argument(
        "--limit-events",
        type=int,
        default=int(
            getattr(settings, "ml_shadow_auto_train_limit_events", 40) or 40
        ),
        help="Cap on events pooled into the cumulative training set.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be processed without training.",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=0,
        help="Optional cap on events to actually process (0 = all). Useful "
        "for first-time smoke runs.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Distinct events with at least one finding, filtered to the season.
        from sqlalchemy import distinct, func

        event_rows = (
            db.query(
                models.TeamMatchFinding.event_key,
                func.count(distinct(models.TeamMatchFinding.match_key)).label("matches"),
            )
            .group_by(models.TeamMatchFinding.event_key)
            .order_by(func.count(distinct(models.TeamMatchFinding.match_key)).desc())
            .all()
        )
        season_events = [
            (ek, int(m or 0))
            for ek, m in event_rows
            if _season_year_from_event_key(ek) == args.season
        ]
        if not season_events:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "season": args.season,
                        "events_found": 0,
                        "message": (
                            "No events with findings for this season. Run "
                            "survey_shadow_training_data.py to diagnose."
                        ),
                    },
                    indent=2,
                )
            )
            return 1

        if args.max_events and args.max_events > 0:
            season_events = season_events[: args.max_events]

        if args.dry_run:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "season": args.season,
                        "events_would_process": len(season_events),
                        "events": [
                            {"event_key": ek, "matches": m}
                            for ek, m in season_events
                        ],
                    },
                    indent=2,
                )
            )
            return 0

        per_event_results = []
        trained_models_seen: set[tuple[str, str]] = set()
        for event_key, match_count in season_events:
            # Refresh ratings + synergy — required inputs for the snapshot
            # exporter. These are cheap if already current.
            try:
                recompute_event_ratings(db, event_key)
                precompute_event_synergy(
                    db, event_key, model_version=SYNERGY_MODEL_VERSION
                )
            except Exception as exc:  # noqa: BLE001 - per-event resilience
                per_event_results.append(
                    {
                        "event_key": event_key,
                        "ok": False,
                        "stage": "prep",
                        "error": str(exc),
                    }
                )
                continue

            try:
                result = auto_train_shadow_models_for_event_breakdown(
                    db,
                    event_key=event_key,
                    limit_events=int(args.limit_events),
                    activate=True,
                    replace_predictions=True,
                )
            except Exception as exc:  # noqa: BLE001
                per_event_results.append(
                    {
                        "event_key": event_key,
                        "ok": False,
                        "stage": "train",
                        "error": str(exc),
                    }
                )
                continue

            train = result.get("train") or {}
            predict = (result.get("predict") or {}).get("predictions") or {}
            ts = train.get("team_strength") or {}
            mo = train.get("match_outcome") or {}
            if ts.get("ok") and ts.get("model_version"):
                trained_models_seen.add(("team_strength", ts["model_version"]))
            if mo.get("ok") and mo.get("model_version"):
                trained_models_seen.add(("match_outcome", mo["model_version"]))

            per_event_results.append(
                {
                    "event_key": event_key,
                    "matches": match_count,
                    "ok": bool(result.get("ok")),
                    "detail": result.get("detail"),
                    "snapshot_rows": result.get("snapshot_rows_written"),
                    "team_strength_trained": ts.get("ok") or False,
                    "match_outcome_trained": mo.get("ok") or False,
                    "team_strength_predicted": (predict.get("team_strength") or {}).get(
                        "rows", 0
                    ),
                    "match_outcome_predicted": (predict.get("match_outcome") or {}).get(
                        "rows", 0
                    ),
                }
            )

        ok_events = sum(1 for r in per_event_results if r.get("ok"))
        print(
            json.dumps(
                {
                    "ok": ok_events > 0,
                    "season": args.season,
                    "events_processed": len(per_event_results),
                    "events_with_ml_predictions": ok_events,
                    "distinct_models_produced": [
                        {"model_key": mk, "model_version": mv}
                        for mk, mv in sorted(trained_models_seen)
                    ],
                    "per_event": per_event_results,
                },
                indent=2,
            )
        )
        return 0 if ok_events > 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
