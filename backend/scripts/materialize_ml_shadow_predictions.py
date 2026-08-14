#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal
from app.services.ml.shadow import materialize_shadow_predictions_for_event


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize shadow model predictions for one event.")
    parser.add_argument("--event-key", required=True)
    parser.add_argument("--model-key", choices=["all", "team_strength", "match_outcome"], default="all")
    parser.add_argument("--team-strength-model-version", default=None)
    parser.add_argument("--match-outcome-model-version", default=None)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--keep-existing", dest="replace_existing", action="store_false")
    parser.set_defaults(replace_existing=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = materialize_shadow_predictions_for_event(
            db,
            event_key=args.event_key,
            model_key=args.model_key,
            team_strength_model_version=args.team_strength_model_version,
            match_outcome_model_version=args.match_outcome_model_version,
            replace_existing=bool(args.replace_existing),
        )
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
