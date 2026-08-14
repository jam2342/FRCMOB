#!/usr/bin/env python3
# Read-only calibration inspector for auto-scout confidence.
#
# Before touching any confidence formula (e.g. offense_level's 0.46 + 0.36*coverage)
# we want evidence, not vibes. This walks approved drafts and reports, per field,
# how often humans override what we labelled high-confidence and how often they
# accept what we labelled low-confidence — i.e. where our confidence is lying.
#
# Local DB caveat: labels here are TBA score-breakdown proxies, so treat output as
# wiring/shape verification. Real calibration evidence comes from prod approvals.
#
#   PYTHONPATH=. python scripts/inspect_auto_scout_calibration.py --event 2026txhou --max-rows 500
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal
from app.services.auto_scout.scouting import summarize_auto_scout_confidence_calibration


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect auto-scout confidence calibration.")
    parser.add_argument("--event", dest="event_key", default=None, help="Restrict to one event_key.")
    parser.add_argument("--season", dest="season_year", type=int, default=None, help="Restrict to a season year.")
    parser.add_argument("--mapper-version", dest="mapper_version", default=None)
    parser.add_argument("--max-rows", dest="max_rows", type=int, default=2500)
    parser.add_argument("--min-samples", dest="min_samples", type=int, default=8)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        report = summarize_auto_scout_confidence_calibration(
            db,
            event_key=args.event_key,
            season_year=args.season_year,
            mapper_version=args.mapper_version,
            max_rows=args.max_rows,
            min_samples=args.min_samples,
        )
    finally:
        db.close()

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
