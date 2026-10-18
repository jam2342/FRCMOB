#!/usr/bin/env python3
# Rebuild every event's ratings for a season without re-ingesting anything from TBA
# (after a rating model change). On the 2026 season (361 events, a full production
# copy) it takes about a minute.
#
#   PYTHONPATH=. python scripts/rebuild_season_ratings.py --season 2026
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import models
from app.db.session import SessionLocal
from app.services.events.pipeline import post_compute_event


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    db = SessionLocal()
    events = [key for (key,) in db.query(models.Event.event_key).filter(models.Event.year == args.season).order_by(models.Event.event_key)]
    failed: list[tuple[str, str]] = []

    started = time.perf_counter()
    for event_key in events:
        # Ratings, then the event's page snapshots (they hold the old numbers).
        result = post_compute_event(db, event_key=event_key)["ratings"]
        if result.get("ok") is False:
            failed.append((event_key, str(result.get("detail"))[:300]))
    ratings_sec = round(time.perf_counter() - started, 1)
    print(f"ratings pass: {len(events)} events, {ratings_sec}s", file=sys.stderr, flush=True)

    versions = Counter(
        version
        for (version,) in db.query(models.EventTeamRating.model_version).filter(models.EventTeamRating.event_key.like(f"{args.season}%"))
    )
    print(json.dumps({
        "season": args.season,
        "events": len(events),
        "ratings_pass_sec": ratings_sec,
        "failed": failed,
        "rating_rows_by_version": dict(versions),
    }, indent=2))
    db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
