# Clear all old analysis data for 2026week0 and reanalyze from scratch.
#
# Usage:
# cd backend
# PYTHONPATH=. python scripts/clear_and_reanalyze_week0.py
from __future__ import annotations

from datetime import datetime, timezone

from app.db import models
from app.db.session import SessionLocal
from app.services.events.pipeline import (
    _clone_template_calibration_to_match,
    _latest_event_calibration_template,
)
from app.api.routes_events import ingest_event
from app.services.jobs import analyze_match
from app.services.ratings.model import recompute_event_ratings
from app.services.ml.synergy import precompute_event_synergy

EVENT_KEY = "2026week0"

def _analysis_version() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"video_v3_tracks_week0_clean_{stamp}"

def clear_event_analysis(db, event_key: str) -> dict[str, int]:
    # Delete all analysis-generated data for the given event.
    counts: dict[str, int] = {}

    # Order matters: children before parents (FK constraints)

    # 1. Artifacts (via analysis_run_id)
    run_ids = [
        r[0] for r in
        db.query(models.AnalysisRun.id)
        .filter(models.AnalysisRun.match_key.ilike(f"{event_key}%"))
        .all()
    ]
    if run_ids:
        counts["artifacts"] = (
            db.query(models.Artifact)
            .filter(models.Artifact.analysis_run_id.in_(run_ids))
            .delete(synchronize_session=False)
        )
        # 2. Analysis qualities
        counts["analysis_qualities"] = (
            db.query(models.AnalysisQuality)
            .filter(models.AnalysisQuality.run_id.in_(run_ids))
            .delete(synchronize_session=False)
        )
    else:
        counts["artifacts"] = 0
        counts["analysis_qualities"] = 0

    # 3. Team match throughputs (FK to findings)
    counts["throughputs"] = (
        db.query(models.TeamMatchThroughput)
        .filter(models.TeamMatchThroughput.event_key == event_key)
        .delete(synchronize_session=False)
    )

    # 4. Team match findings
    counts["findings"] = (
        db.query(models.TeamMatchFinding)
        .filter(models.TeamMatchFinding.event_key == event_key)
        .delete(synchronize_session=False)
    )

    # 5. Robot tracks
    counts["robot_tracks"] = (
        db.query(models.RobotTrack)
        .filter(models.RobotTrack.event_key == event_key)
        .delete(synchronize_session=False)
    )

    # 6. Match events
    counts["match_events"] = (
        db.query(models.MatchEvent)
        .filter(models.MatchEvent.event_key == event_key)
        .delete(synchronize_session=False)
    )

    # 7. Match phase windows
    counts["phase_windows"] = (
        db.query(models.MatchPhaseWindow)
        .filter(models.MatchPhaseWindow.event_key == event_key)
        .delete(synchronize_session=False)
    )

    # 8. Analysis run contexts (FK to analysis_runs)
    if run_ids:
        counts["run_contexts"] = (
            db.query(models.AnalysisRunContext)
            .filter(models.AnalysisRunContext.run_id.in_(run_ids))
            .delete(synchronize_session=False)
        )
    else:
        counts["run_contexts"] = 0

    # 9. Analysis runs
    counts["analysis_runs"] = (
        db.query(models.AnalysisRun)
        .filter(models.AnalysisRun.match_key.ilike(f"{event_key}%"))
        .delete(synchronize_session=False)
    )

    # 10. Event team ratings
    counts["ratings"] = (
        db.query(models.EventTeamRating)
        .filter(models.EventTeamRating.event_key == event_key)
        .delete(synchronize_session=False)
    )

    # 11. Event team stats
    counts["stats"] = (
        db.query(models.EventTeamStat)
        .filter(models.EventTeamStat.event_key == event_key)
        .delete(synchronize_session=False)
    )

    # 12. Synergy data
    counts["synergy_events"] = (
        db.query(models.TeamPairSynergyEvent)
        .filter(models.TeamPairSynergyEvent.event_key == event_key)
        .delete(synchronize_session=False)
    )
    counts["synergy_projections"] = (
        db.query(models.MatchSynergyProjection)
        .filter(models.MatchSynergyProjection.event_key == event_key)
        .delete(synchronize_session=False)
    )

    # 13. Intel snapshots
    counts["intel_snapshots"] = (
        db.query(models.IntelSnapshot)
        .filter(models.IntelSnapshot.event_key == event_key)
        .delete(synchronize_session=False)
    )

    # 14. Throughput strengths
    counts["throughput_strengths"] = (
        db.query(models.TeamEventThroughputStrength)
        .filter(models.TeamEventThroughputStrength.event_key == event_key)
        .delete(synchronize_session=False)
    )

    db.flush()
    return counts

def main() -> int:
    version = _analysis_version()

    # ── Step 1: Clear old data ──
    print(f"\n{'='*60}")
    print(f"STEP 1: Clear all analysis data for {EVENT_KEY}")
    print(f"{'='*60}")
    with SessionLocal() as db:
        counts = clear_event_analysis(db, EVENT_KEY)
        db.commit()
    for table, count in counts.items():
        print(f"  Deleted {count:>6} rows from {table}")
    total_deleted = sum(counts.values())
    print(f"  Total deleted: {total_deleted}")

    # ── Step 2: Re-ingest event from TBA ──
    print(f"\n{'='*60}")
    print(f"STEP 2: Re-ingest {EVENT_KEY} from TBA")
    print(f"{'='*60}")
    with SessionLocal() as db:
        try:
            ingest_event(event_key=EVENT_KEY, run_post_compute=False, db=db)
            print("  Event ingest OK")
        except Exception as exc:
            print(f"  Event ingest FAILED: {exc}")
            return 1

    # ── Step 3: Ensure calibration ──
    print(f"\n{'='*60}")
    print("STEP 3: Clone calibration template")
    print(f"{'='*60}")
    with SessionLocal() as db:
        template = _latest_event_calibration_template(db, EVENT_KEY)
        if template is None:
            print("  WARNING: No calibration template found.")
            print("  Matches will use TBA score breakdown fallback only.")
        else:
            print(f"  Template: {template.match_key}")
            match_rows = (
                db.query(models.Match.match_key)
                .filter(models.Match.event_key == EVENT_KEY)
                .all()
            )
            cloned = 0
            for (match_key,) in match_rows:
                result = _clone_template_calibration_to_match(
                    db, template=template, event_key=EVENT_KEY, match_key=match_key
                )
                if result is not None:
                    cloned += 1
            print(f"  Cloned calibration to {cloned} matches")

    # ── Step 4: Load match list ──
    with SessionLocal() as db:
        match_rows = (
            db.query(models.Match.match_key)
            .filter(models.Match.event_key == EVENT_KEY)
            .order_by(models.Match.comp_level.asc(), models.Match.match_number.asc())
            .all()
        )
    match_keys = [mk for (mk,) in match_rows]
    print(f"\n{'='*60}")
    print(f"STEP 4: Analyze {len(match_keys)} matches")
    print(f"{'='*60}")

    processed = 0
    skipped = 0
    failed = 0
    failures: list[tuple[str, str]] = []

    for index, match_key in enumerate(match_keys, start=1):
        try:
            result = analyze_match(match_key=match_key, analysis_version=version)
            status = str(result.get("status") or "")
            if status == "skipped":
                skipped += 1
                tag = "SKIP"
            else:
                processed += 1
                tag = "OK"
            print(f"  [{index:>3}/{len(match_keys)}] {match_key} → {tag}")
        except Exception as exc:
            failed += 1
            failures.append((match_key, str(exc)))
            print(f"  [{index:>3}/{len(match_keys)}] {match_key} → FAIL: {exc}")

    # ── Step 5: Recompute ratings ──
    print(f"\n{'='*60}")
    print("STEP 5: Recompute ratings & synergy")
    print(f"{'='*60}")
    with SessionLocal() as db:
        try:
            result = recompute_event_ratings(db, EVENT_KEY)
            rating_count = result.get("count", 0)
            print(f"  Ratings: {rating_count} teams computed")
        except Exception as exc:
            print(f"  Ratings FAILED: {exc}")

        try:
            precompute_event_synergy(db, EVENT_KEY)
            print("  Synergy: OK")
        except Exception as exc:
            print(f"  Synergy FAILED: {exc}")

    # ── Summary ──
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  Event:     {EVENT_KEY}")
    print(f"  Version:   {version}")
    print(f"  Cleared:   {total_deleted} rows")
    print(f"  Processed: {processed}")
    print(f"  Skipped:   {skipped}")
    print(f"  Failed:    {failed}")
    if failures:
        print("  Failures:")
        for mk, err in failures[:10]:
            print(f"    {mk}: {err}")

    # ── Step 6: Show new ratings ──
    print(f"\n{'='*60}")
    print("NEW RATINGS")
    print(f"{'='*60}")
    with SessionLocal() as db:
        ratings = (
            db.query(models.EventTeamRating)
            .filter(models.EventTeamRating.event_key == EVENT_KEY)
            .order_by(models.EventTeamRating.rating_0_100.desc())
            .all()
        )
        for r in ratings[:20]:
            print(
                f"  {r.team_key:<10} "
                f"rating={r.rating_0_100:>6.1f}  "
                f"conf={r.confidence_0_1:.3f}  "
                f"robot={r.robot_level_0_100:>5.1f}  "
                f"driver={r.driver_skill_0_100:>5.1f}  "
                f"anchor={r.results_anchor:>5.1f}  "
                f"endgame={r.endgame:>5.1f}"
            )

    return 2 if failed > 0 else 0

if __name__ == "__main__":
    raise SystemExit(main())
