#!/usr/bin/env python3
# Pre-flight survey for shadow ML training: how many training rows would each model
# get from this database right now?
#
# Runs the real snapshot builders inside a transaction that is always rolled back,
# so the counts are exactly what the trainer would see and nothing is written.
# Run this before backfill_shadow_models.py.
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import models
from app.db.session import SessionLocal
from app.services.ml import shadow
from app.services.ml.role_ml import rebuild_role_signal_feature_snapshots
from app.services.ml.synergy_ml import rebuild_synergy_pair_feature_snapshots

# The trainer refuses to fit a model on fewer rows than this (see shadow.py).
TRAINER_MIN_ROWS = 20


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--limit-events", type=int, default=250)
    args = parser.parse_args()

    db = SessionLocal()
    db.autoflush = False
    try:
        event_keys = set(
            shadow._event_keys_for_snapshot_rebuild(db, None, args.limit_events, season_year=args.season)
        )
        year_map = shadow._event_year_map(db, event_keys)
        builders = {
            "team_strength": shadow._rebuild_team_strength_feature_snapshots,
            "match_outcome": shadow._rebuild_match_outcome_feature_snapshots,
            "synergy_pair": rebuild_synergy_pair_feature_snapshots,
            "role_signal": rebuild_role_signal_feature_snapshots,
        }
        results = {}
        for name, build in builders.items():
            results[name] = build(db, event_keys=event_keys, source_version="survey_only", year_map=year_map)
        known_teams = {key for (key,) in db.query(models.Team.team_key)}
        unknown_team_rows = sum(
            1
            for obj in db.new
            if isinstance(obj, models.MLFeatureSnapshot) and obj.team_key and obj.team_key not in known_teams
        )
    finally:
        db.rollback()
        db.close()

    rows = {name: int((result or {}).get("rows_written") or 0) for name, result in results.items()}
    verdict = {f"{name}_clears_threshold": count >= TRAINER_MIN_ROWS for name, count in rows.items()}
    print(json.dumps(
        {
            "ok": all(verdict.values()) and unknown_team_rows == 0,
            "season": args.season,
            "events": len(event_keys),
            "min_rows_threshold": TRAINER_MIN_ROWS,
            "rows": rows,
            "skipped": results,
            # Rows whose team is missing from `teams` would fail the foreign key and
            # roll back the whole training run.
            "unknown_team_rows": unknown_team_rows,
            "verdict": verdict,
        },
        indent=2,
        default=str,
    ))
    return 0 if all(verdict.values()) and unknown_team_rows == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
