#!/usr/bin/env python3
# Save one past season's official events from TBA as raw JSON, for the rating research
# harness (scripts/evaluate_rating_research.py). One file per event: its metadata, every
# match with its score breakdown, and the team list (rookie years). Public data, but kept
# outside every repo by default. Resumable: events already on disk are skipped.
#
#   PYTHONPATH=. python scripts/fetch_tba_season.py --season 2025
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

from app.tba.client import TBAClient, TBAClientError

# Regional, district, district championship (+ divisions), Championship divisions and finals.
OFFICIAL_EVENT_TYPES = {0, 1, 2, 3, 4, 5}
DEFAULT_DIR = BACKEND_ROOT.parents[1] / "research-data" / "tba"
EVENT_FIELDS = ("key", "name", "event_type", "week", "start_date", "end_date", "year", "district")


def _call(fn, *args):
    # TBAClient already retries; a 404 is a real answer (an event with no data yet).
    try:
        return fn(*args)
    except TBAClientError as error:
        if error.status_code == 404:
            return None
        raise


def fetch_season(season: int, out_dir: Path, event_types: set[int] = OFFICIAL_EVENT_TYPES) -> dict:
    tba = TBAClient()
    target = out_dir / str(season)
    target.mkdir(parents=True, exist_ok=True)
    events = [e for e in (_call(tba.events, season) or []) if isinstance(e, dict) and e.get("event_type") in event_types]
    done = skipped = empty = 0
    for event in sorted(events, key=lambda e: (e.get("start_date") or "", e["key"])):
        path = target / f"{event['key']}.json"
        if path.exists():
            skipped += 1
            continue
        matches = _call(tba.event_matches, event["key"]) or []
        if not matches:
            empty += 1
            continue
        teams = _call(tba.event_teams, event["key"]) or []
        payload = {
            "event": {k: (event.get("district") or {}).get("abbreviation") if k == "district" else event.get(k) for k in EVENT_FIELDS},
            "teams": [{"key": t.get("key"), "rookie_year": t.get("rookie_year")} for t in teams if isinstance(t, dict)],
            "matches": matches,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")))
        tmp.replace(path)
        done += 1
    return {"season": season, "events_listed": len(events), "saved": done, "already_there": skipped, "no_matches": empty, "dir": str(target)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, action="append", required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--event-types", default=",".join(str(t) for t in sorted(OFFICIAL_EVENT_TYPES)), help="TBA event types; add 99,100 for offseason, which production also stores (use another --out so the official cache stays clean)")
    args = parser.parse_args()
    for season in args.season:
        started = time.perf_counter()
        report = fetch_season(season, args.out, {int(t) for t in args.event_types.split(",")})
        report["seconds"] = round(time.perf_counter() - started, 1)
        print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
