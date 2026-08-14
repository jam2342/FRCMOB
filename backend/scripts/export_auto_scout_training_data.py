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
from app.services.auto_scout.training import export_auto_scout_training_snapshots


def _parse_fields(raw_fields: str | None) -> list[str] | None:
    if raw_fields is None:
        return None
    values = [str(token or "").strip().lower() for token in str(raw_fields).split(",")]
    normalized = [value for value in values if value]
    return normalized or None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export approved auto-scout draft labels into MLFeatureSnapshot rows "
            "for auto_scout_field models."
        )
    )
    parser.add_argument("--source-version", default=None)
    parser.add_argument("--season-year", type=int, default=None)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--keep-existing", dest="replace_existing", action="store_false")
    parser.set_defaults(replace_existing=False)
    parser.add_argument("--max-drafts", type=int, default=None)
    parser.add_argument(
        "--fields",
        default=None,
        help="Comma-separated field list (defaults to all Phase-2 round2 fields).",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = export_auto_scout_training_snapshots(
            db,
            source_version=args.source_version,
            season_year=args.season_year,
            replace_existing=bool(args.replace_existing),
            max_drafts=args.max_drafts,
            field_names=_parse_fields(args.fields),
        )
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
