#!/usr/bin/env python3
# Seed TeamMatchThroughput rows from TBA score-breakdown findings.
#
# The shadow-ML trainer labels team_strength / match_outcome from
# TeamMatchThroughput.active_bps, which normally only gets written by the
# video-analysis job (analyze_match). On a deploy with no analyzed videos —
# e.g. a fresh local stack ingested purely from TBA — the trainer has
# findings but no labels and every snapshot row is skipped_missing_target.
#
# This derives a proxy label from the official score breakdown that
# upsert_score_breakdown_truth already wrote into each finding:
#   fuel_scoring_rate is (scoring_proxy / teleop_sec) * 60, so
#   active_bps = fuel_scoring_rate / 60  (balls per second).
# Findings with no teleop scoring get active_bps = 0.0 — a team that never
# scored is a real low-strength label, not missing data.
#
# Idempotent: skips findings that already have a throughput row, so it never
# overwrites real video-derived metrics. Safe to re-run after new ingests.
from __future__ import annotations

# ruff: noqa: E402

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import models
from app.db.session import SessionLocal
from app.services.events.ingest import TBA_SCOREBREAKDOWN_SOURCE

PROXY_SOURCE = "tba_score_breakdown_proxy"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed throughput labels from TBA score-breakdown findings."
    )
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="Restrict to event keys starting with this year (default: all).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without committing.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = (
            db.query(models.TeamMatchFinding)
            .outerjoin(
                models.TeamMatchThroughput,
                models.TeamMatchThroughput.finding_id == models.TeamMatchFinding.id,
            )
            .filter(
                models.TeamMatchFinding.source == TBA_SCOREBREAKDOWN_SOURCE,
                models.TeamMatchThroughput.finding_id.is_(None),
            )
        )
        if args.season:
            query = query.filter(
                models.TeamMatchFinding.event_key.like(f"{args.season}%")
            )

        findings = query.all()
        written = 0
        for finding in findings:
            rate = finding.fuel_scoring_rate
            active_bps = max(0.0, float(rate) / 60.0) if rate is not None else 0.0
            if not args.dry_run:
                db.add(
                    models.TeamMatchThroughput(
                        finding_id=finding.id,
                        analysis_run_id=finding.analysis_run_id,
                        match_key=finding.match_key,
                        event_key=finding.event_key,
                        team_key=finding.team_key,
                        balls_shot_total=None,
                        shooting_time_total_seconds=None,
                        bps_total=active_bps,
                        balls_shot_active=None,
                        shooting_time_active_seconds=None,
                        active_bps=active_bps,
                        metric_coverage={
                            "coverage_score": 1.0,
                            "missing_reasons": [],
                            "has_scoring_events": active_bps > 0.0,
                            "proxy": PROXY_SOURCE,
                        },
                        source=PROXY_SOURCE,
                    )
                )
            written += 1

        if not args.dry_run:
            db.commit()
        print(
            f"{'would write' if args.dry_run else 'wrote'} {written} throughput rows "
            f"from {len(findings)} unlabeled TBA findings"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
