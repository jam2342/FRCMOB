#!/usr/bin/env python3
# Fill match_alliance_results for a season's stored events from TBA, without a full
# re-ingest: one match-list request per event, rows rewritten in place (safe to rerun).
# New results arrive through ingest_event afterwards.
#
#   PYTHONPATH=. python scripts/backfill_alliance_results.py --season 2026 [--events 2026arc,2026cur]
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func

from app.db import models
from app.db.session import SessionLocal
from app.services.scoring.alliance_results import upsert_alliance_results
from app.tba.client import TBAClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--events", default="", help="Comma list; default every stored event of the season with matches.")
    args = parser.parse_args()

    tba = TBAClient()
    db = SessionLocal()
    if args.events:
        events = [key.strip().lower() for key in args.events.split(",") if key.strip()]
    else:
        events = [
            key
            for (key,) in db.query(models.Match.event_key)
            .join(models.Event, models.Event.event_key == models.Match.event_key)
            .filter(models.Event.year == args.season)
            .group_by(models.Match.event_key)
            .having(func.count(models.Match.match_key) > 0)
            .order_by(models.Match.event_key)
        ]

    started = time.perf_counter()
    written = 0
    failed: list[tuple[str, str]] = []
    for event_key in events:
        try:
            matches = tba.event_matches(event_key)  # outside any transaction: TBA can be slow
            count = upsert_alliance_results(db, event_key=event_key, season_year=args.season, matches=list(matches or []))
            db.commit()
            written += count
        except Exception as exc:
            db.rollback()
            failed.append((event_key, repr(exc)[:200]))
    print(json.dumps({
        "season": args.season,
        "events": len(events),
        "rows_written": written,
        "failed": failed,
        "seconds": round(time.perf_counter() - started, 1),
    }, indent=2))
    db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
