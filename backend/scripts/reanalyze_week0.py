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


def _analysis_version() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"video_v3_tracks_week0_reanalysis_{stamp}"


def main() -> int:
    version = _analysis_version()
    ingest_ok = 0
    ingest_failed = 0
    cloned_calibrations = 0
    clone_skipped_no_template = 0
    processed = 0
    skipped = 0
    failed = 0
    failures: list[tuple[str, str]] = []

    with SessionLocal() as db:
        match_rows = (
            db.query(models.Match.match_key, models.Match.event_key)
            .filter(models.Match.event_key.ilike("%week0%"))
            .order_by(models.Match.event_key.asc(), models.Match.comp_level.asc(), models.Match.match_number.asc())
            .all()
        )

    if not match_rows:
        print("week0_reanalysis_no_matches_found")
        return 1

    event_keys = sorted({event_key for _, event_key in match_rows if event_key})

    with SessionLocal() as db:
        for event_key in event_keys:
            try:
                ingest_event(event_key=event_key, run_post_compute=False, db=db)
                ingest_ok += 1
                print("week0_event_ingest_ok", {"event_key": event_key})
            except Exception as exc:
                ingest_failed += 1
                print("week0_event_ingest_failed", {"event_key": event_key, "error": str(exc)})

        for event_key in event_keys:
            template = _latest_event_calibration_template(db, event_key)
            match_keys = [
                match_key
                for match_key, key in match_rows
                if key == event_key
            ]
            if template is None:
                clone_skipped_no_template += len(match_keys)
                print("week0_calibration_clone_skipped", {"event_key": event_key, "reason": "no_template", "matches": len(match_keys)})
                continue
            for match_key in match_keys:
                before = (
                    db.query(models.FieldCalibration)
                    .filter(models.FieldCalibration.match_key == match_key)
                    .count()
                )
                clone = _clone_template_calibration_to_match(
                    db,
                    template=template,
                    event_key=event_key,
                    match_key=match_key,
                )
                if clone is not None and before == 0:
                    cloned_calibrations += 1
            print("week0_calibration_clone_ok", {"event_key": event_key, "template_match_key": template.match_key})
    print("week0_reanalysis_start", {"analysis_version": version, "matches": len(match_rows), "events": len(event_keys)})

    for index, (match_key, event_key) in enumerate(match_rows, start=1):
        try:
            result = analyze_match(match_key=match_key, analysis_version=version)
            status = str(result.get("status") or "")
            if status == "skipped":
                skipped += 1
            else:
                processed += 1
            print(
                "week0_match",
                {
                    "index": index,
                    "total": len(match_rows),
                    "match_key": match_key,
                    "event_key": event_key,
                    "status": status or "processed",
                },
            )
        except Exception as exc:
            failed += 1
            failures.append((match_key, str(exc)))
            print(
                "week0_match_failed",
                {
                    "index": index,
                    "total": len(match_rows),
                    "match_key": match_key,
                    "event_key": event_key,
                    "error": str(exc),
                },
            )

    ratings_ok = 0
    ratings_failed = 0
    synergy_ok = 0
    synergy_failed = 0

    with SessionLocal() as db:
        for event_key in event_keys:
            try:
                recompute_event_ratings(db, event_key)
                ratings_ok += 1
                print("week0_event_ratings_ok", {"event_key": event_key})
            except Exception as exc:
                ratings_failed += 1
                print("week0_event_ratings_failed", {"event_key": event_key, "error": str(exc)})

            try:
                precompute_event_synergy(db, event_key)
                synergy_ok += 1
                print("week0_event_synergy_ok", {"event_key": event_key})
            except Exception as exc:
                synergy_failed += 1
                print("week0_event_synergy_failed", {"event_key": event_key, "error": str(exc)})

    print(
        "week0_reanalysis_done",
        {
            "analysis_version": version,
            "event_ingest_ok": ingest_ok,
            "event_ingest_failed": ingest_failed,
            "calibration_cloned": cloned_calibrations,
            "calibration_clone_skipped_no_template": clone_skipped_no_template,
            "matches_total": len(match_rows),
            "matches_processed": processed,
            "matches_skipped": skipped,
            "matches_failed": failed,
            "ratings_ok": ratings_ok,
            "ratings_failed": ratings_failed,
            "synergy_ok": synergy_ok,
            "synergy_failed": synergy_failed,
            "sample_failures": failures[:10],
        },
    )

    if failed > 0 or ratings_failed > 0 or synergy_failed > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
