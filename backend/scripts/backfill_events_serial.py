"""Bounded serial ingest in a separate process, never through the API pool.

Run with the deployment environment: PYTHONPATH=. python scripts/backfill_events_serial.py
--event 2026arc --state /persistent/backfill.json. Default is ONE event. Supply
--year and --max-events explicitly for a larger run. No ratings/training jobs
are triggered. A match count alone is never treated as proof of completion.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import signal


def missing_records(matches: list[dict], stored_matches: set[str],
                    stored_teams: set[tuple], stored_videos: set[tuple]) -> dict[str, int]:
    missing_matches = missing_teams = missing_videos = 0
    for match in matches:
        key = match["key"]
        missing_matches += key not in stored_matches
        for alliance, roster in match.get("alliances", {}).items():
            for team in roster.get("team_keys", []):
                missing_teams += (key, team, alliance) not in stored_teams
        for video in match.get("videos", []):
            if video.get("type") and video.get("key"):
                missing_videos += (key, video["type"], video["key"]) not in stored_videos
    return {"matches": missing_matches, "team_links": missing_teams, "videos": missing_videos}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--event", action="append")
    source.add_argument("--year", type=int)
    parser.add_argument("--max-events", type=int, default=1)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.max_events <= 400:
        parser.error("max-events must be between 1 and 400")
    from app.db.session import SessionLocal
    from app.db import models
    from app.tba.client import TBAClient
    from app.services.events.ingest import ingest_event

    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock = args.state.with_suffix(".lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("A backfill already holds this state lock")
    stopped = False

    def stop_after_event(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop_after_event)
    signal.signal(signal.SIGINT, stop_after_event)
    tba = TBAClient()
    events = args.event or [event["key"] for event in tba.events(args.year)]
    state = json.loads(args.state.read_text()) if args.state.exists() else {"events": {}}
    completed = errors = 0
    for event_key in events:
        if stopped or completed >= args.max_events or errors >= 3:
            break
        try:
            # Fetch externally before opening a DB transaction.
            matches = tba.event_matches(event_key)
            with SessionLocal() as db:
                def check():
                    return missing_records(matches,
                        {row[0] for row in db.query(models.Match.match_key).filter_by(event_key=event_key)},
                        set(db.query(models.MatchTeam.match_key, models.MatchTeam.team_key, models.MatchTeam.alliance).filter_by(event_key=event_key)),
                        set(db.query(models.MatchVideo.match_key, models.MatchVideo.video_type, models.MatchVideo.video_key)
                            .join(models.Match, models.Match.match_key == models.MatchVideo.match_key)
                            .filter(models.Match.event_key == event_key)))
                before = check()
                db.rollback()
                if not any(before.values()):
                    status = "no_upstream_matches" if not matches else "inventory_complete"
                    after = before
                else:
                    print(json.dumps({"event": event_key, "status": "ingesting", "missing": before}), flush=True)
                    ingest_event(event_key, tba, db, run_post_compute=False)
                    db.commit()
                    after = check()
                    completed += 1
                    status = "inventory_complete" if not any(after.values()) else "incomplete"
                    if status == "incomplete":
                        errors += 1
                state["events"][event_key] = {"status": status, "upstream_matches": len(matches), "missing": after}
        except Exception as exc:
            errors += 1
            state["events"][event_key] = {"status": "failed", "error_type": type(exc).__name__}
        temporary = args.state.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2))
        temporary.replace(args.state)
        print(json.dumps({"event": event_key, **state["events"][event_key]}), flush=True)
    print(json.dumps({"status": "stopped" if stopped else "batch_finished", "ingested": completed, "errors": errors}), flush=True)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
