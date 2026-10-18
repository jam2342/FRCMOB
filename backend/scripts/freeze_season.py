#!/usr/bin/env python3
# Freeze a finished season: archive its match predictions and mark it read-only, so its
# ratings are never recomputed and its pages never rebuild the season in memory.
# Run once at the next season's kickoff, after the last refresh of the old season
# (docs/SEASON_TRANSITION.md).
#
#   PYTHONPATH=. python scripts/freeze_season.py --season 2026
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

from app.db.session import SessionLocal
from app.services.seasons import freeze_season


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--allow-current", action="store_true", help="Rehearsals on a scratch database only.")
    parser.add_argument("--note", default=None)
    args = parser.parse_args()
    started = time.perf_counter()
    db = SessionLocal()
    try:
        result = freeze_season(db, args.season, allow_current=args.allow_current, note=args.note)
    finally:
        db.close()
    result["seconds"] = round(time.perf_counter() - started, 1)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
