#!/usr/bin/env python3
# Verify that active auto_scout_field:* models can actually produce ML evidence in a
# real draft — i.e. the wiring (registry lookup -> runtime -> inference -> predictor)
# is intact end to end. This is wiring verification, NOT accuracy: the local DB labels
# are TBA score-breakdown proxies.
#
# Exits nonzero if active auto_scout_field models exist but no round-2 field produced
# an ml_model:* prediction — that means the ML path is broken and we silently fell
# back to deterministic everywhere.
#
#   PYTHONPATH=. python scripts/verify_auto_scout_ml_path.py
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
from app.services.auto_scout.predictors.round2_ml import _ML_FIELD_THRESHOLDS
from app.services.auto_scout.scouting import generate_auto_scout_draft
from app.services.ml.shadow import AUTO_SCOUT_FIELD_MODEL_PREFIX


def _active_auto_scout_models(db) -> list[str]:
    rows = (
        db.query(models.MLModelRegistry.model_key)
        .filter(
            models.MLModelRegistry.model_key.like(f"{AUTO_SCOUT_FIELD_MODEL_PREFIX}:%"),
            models.MLModelRegistry.is_active.is_(True),
        )
        .distinct()
        .all()
    )
    return sorted({str(key) for (key,) in rows})


def _pick_analyzed_team(db) -> tuple[str, str, str] | None:
    # A completed run that has at least one finding and one resolved team track.
    row = (
        db.query(
            models.TeamMatchFinding.event_key,
            models.TeamMatchFinding.match_key,
            models.TeamMatchFinding.team_key,
        )
        .join(models.AnalysisRun, models.AnalysisRun.id == models.TeamMatchFinding.analysis_run_id)
        .join(
            models.RobotTrack,
            (models.RobotTrack.analysis_run_id == models.TeamMatchFinding.analysis_run_id)
            & (models.RobotTrack.team_key == models.TeamMatchFinding.team_key),
        )
        .filter(models.AnalysisRun.status == "completed")
        .order_by(models.AnalysisRun.created_at.desc(), models.AnalysisRun.id.desc())
        .first()
    )
    if row is None:
        return None
    event_key, match_key, team_key = row
    return str(event_key), str(match_key), str(team_key)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the auto-scout ML prediction path.")
    parser.add_argument("--event", dest="event_key", default=None)
    parser.add_argument("--match", dest="match_key", default=None)
    parser.add_argument("--team", dest="team_key", default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        active_models = _active_auto_scout_models(db)
        print(f"Active auto_scout_field models: {active_models or 'none'}")

        if args.match_key and args.team_key and args.event_key:
            target = (args.event_key, args.match_key, args.team_key)
        else:
            target = _pick_analyzed_team(db)
        if target is None:
            print("No completed analysis with tracks+findings found — cannot verify.")
            # Nothing to run against; not a failure of the ML path itself.
            return 0
        event_key, match_key, team_key = target
        print(f"Target: event={event_key} match={match_key} team={team_key}")

        row, _ = generate_auto_scout_draft(
            db,
            event_key=event_key,
            match_key=match_key,
            team_key=team_key,
            force_regenerate=True,
        )
    finally:
        db.close()

    form_patch = (row.draft_payload or {}).get("form_patch", {})
    confidence = row.field_confidence or {}
    provenance = row.field_provenance or {}
    evidence_refs = row.field_evidence_refs or {}

    ml_fields_used: list[str] = []
    print(f"\nDraft status: {row.status}")
    print("Round-2 fields:")
    for field_name in _ML_FIELD_THRESHOLDS:
        refs = evidence_refs.get(field_name) or []
        ml_ref = next(
            (
                str(ref.get("ref_id"))
                for ref in refs
                if isinstance(ref, dict) and str(ref.get("ref_id", "")).startswith("ml_model:")
            ),
            None,
        )
        if ml_ref is not None:
            ml_fields_used.append(field_name)
        source = "ML" if ml_ref else "deterministic"
        print(
            f"  {field_name}: value={form_patch.get(field_name)} "
            f"conf={confidence.get(field_name)} provenance={provenance.get(field_name)} "
            f"source={source} ref={ml_ref or (refs[0].get('ref_id') if refs else None)}"
        )

    print(f"\nML-backed fields: {ml_fields_used or 'none'}")

    if active_models and not ml_fields_used:
        print(
            "\nFAIL: active auto_scout_field models exist but no round-2 field used ML. "
            "The ML path is broken — drafts silently fell back to deterministic.",
            file=sys.stderr,
        )
        return 1
    print("\nOK: ML path verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
