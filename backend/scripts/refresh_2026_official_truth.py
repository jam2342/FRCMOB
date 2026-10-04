"""Refresh 2026 official findings and ratings after an ingest semantics change.

Dry run by default. Example, from backend/:
  python -m scripts.refresh_2026_official_truth --event 2026cur
  python -m scripts.refresh_2026_official_truth --event 2026cur --apply

Each event commits separately. Rerunning the command is safe: the ingest
replaces only its own official-truth runs and then recomputes event ratings.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import models
from app.db.session import SessionLocal
from app.services.events.ingest import (
    extract_2026_auto_fuel_coprs,
    upsert_score_breakdown_truth,
)
from app.services.ratings.model import recompute_event_ratings
from app.tba.client import TBAClient


def eligible_match_payloads(
    matches: object,
    *,
    stored_match_keys: set[str],
    auto_fuel_coprs: dict[str, float],
) -> tuple[list[dict[str, Any]], set[str]]:
    eligible: list[dict[str, Any]] = []
    missing_teams: set[str] = set()
    for match in matches if isinstance(matches, list) else []:
        if not isinstance(match, dict) or match.get("key") not in stored_match_keys:
            continue
        score_breakdown = match.get("score_breakdown")
        alliances = match.get("alliances")
        if not isinstance(score_breakdown, dict) or not isinstance(alliances, dict):
            continue
        if not any(isinstance(score_breakdown.get(color), dict) for color in ("red", "blue")):
            continue
        eligible.append(match)
        for color in ("red", "blue"):
            if not isinstance(score_breakdown.get(color), dict):
                continue
            roster = alliances.get(color) if isinstance(alliances.get(color), dict) else {}
            team_keys = [key for key in roster.get("team_keys") or [] if isinstance(key, str)]
            for team_key in team_keys:
                if team_key not in auto_fuel_coprs:
                    missing_teams.add(team_key)
            hub = score_breakdown[color].get("hubScore")
            auto_points = hub.get("autoPoints") if isinstance(hub, dict) else None
            if isinstance(auto_points, (int, float)) and auto_points > 0 and not any(
                auto_fuel_coprs.get(key, 0.0) > 0.0 for key in team_keys
            ):
                missing_teams.update(team_keys)
    return eligible, missing_teams


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--event", action="append", help="Event key; repeat for several events")
    source.add_argument("--year", type=int, help="Refresh event keys stored for this year")
    parser.add_argument("--limit", type=int, help="Maximum events to inspect (default: 1 for --year, all listed --event keys)")
    parser.add_argument("--apply", action="store_true", help="Write findings and recompute ratings")
    args = parser.parse_args()
    limit = args.limit if args.limit is not None else (len(args.event) if args.event else 1)
    if not 1 <= limit <= 400:
        parser.error("--limit must be between 1 and 400")
    if args.year is not None and args.year != 2026:
        parser.error("this repair supports 2026 only")

    with SessionLocal() as db:
        if args.event:
            event_keys = [str(key).strip().lower() for key in args.event]
            years = dict(db.query(models.Event.event_key, models.Event.year).filter(models.Event.event_key.in_(event_keys)))
            if any(years.get(key) != 2026 for key in event_keys):
                parser.error("every --event must be a stored 2026 event")
        else:
            event_keys = [key for key, in db.query(models.Event.event_key).filter(models.Event.year == 2026).order_by(models.Event.event_key).limit(limit)]
        event_keys = list(dict.fromkeys(event_keys))[:limit]

    tba = TBAClient()
    failed = 0
    refreshed = 0
    for event_key in event_keys:
        try:
            with SessionLocal() as db:
                stored_keys = {
                    key for key, in db.query(models.Match.match_key).filter(models.Match.event_key == event_key)
                }
            if not stored_keys:
                print(json.dumps({
                    "event": event_key, "status": "skipped",
                    "eligible_matches": 0,
                    "missing_auto_copr_teams": [],
                    "missing_existing_truth_matches": [],
                }), flush=True)
                continue
            # External requests finish before the database transaction starts.
            matches = tba.event_matches(event_key)
            auto_fuel_coprs = extract_2026_auto_fuel_coprs(tba.event_coprs(event_key))
            with SessionLocal() as db:
                eligible, missing = eligible_match_payloads(
                    matches,
                    stored_match_keys=stored_keys,
                    auto_fuel_coprs=auto_fuel_coprs,
                )
                old_truth_match_keys = {
                    key for key, in (
                        db.query(models.AnalysisRun.match_key)
                        .join(models.Match, models.Match.match_key == models.AnalysisRun.match_key)
                        .filter(
                            models.Match.event_key == event_key,
                            models.AnalysisRun.run_kind == "official_truth",
                            models.AnalysisRun.version.in_(("tba_score_breakdown_v1", "tba_score_breakdown_v2")),
                        )
                    )
                }
                missing_old_matches = old_truth_match_keys - {match["key"] for match in eligible}
                # The ingest leaves auto unknown for an alliance with incomplete
                # COPRs. It can still repair cycle units and other findings.
                if not eligible or missing_old_matches:
                    print(json.dumps({
                        "event": event_key, "status": "skipped",
                        "eligible_matches": len(eligible),
                        "missing_auto_copr_teams": sorted(missing),
                        "missing_existing_truth_matches": sorted(missing_old_matches),
                    }), flush=True)
                    failed += 1 if missing_old_matches else 0
                    continue
                if not args.apply:
                    print(json.dumps({
                        "event": event_key, "status": "ready",
                        "eligible_matches": len(eligible),
                        "missing_auto_copr_teams": sorted(missing),
                    }), flush=True)
                    continue

                truth = upsert_score_breakdown_truth(
                    db,
                    event_key=event_key,
                    season_year=2026,
                    matches=eligible,
                    auto_fuel_copr_by_team=auto_fuel_coprs,
                )
                db.commit()
                ratings = recompute_event_ratings(db, event_key)
                if not ratings.get("ok"):
                    raise RuntimeError("ratings recompute returned ok=false")
                refreshed += 1
                print(json.dumps({
                    "event": event_key, "status": "refreshed",
                    "eligible_matches": len(eligible),
                    "findings": truth["inserted_findings"],
                    "ratings": ratings.get("count"),
                    "missing_auto_copr_teams": sorted(missing),
                }), flush=True)
        except Exception as exc:
            failed += 1
            print(json.dumps({"event": event_key, "status": "failed", "error_type": type(exc).__name__}), flush=True)

    print(json.dumps({"status": "complete", "mode": "apply" if args.apply else "dry_run", "inspected": len(event_keys), "refreshed": refreshed, "failed": failed}), flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
