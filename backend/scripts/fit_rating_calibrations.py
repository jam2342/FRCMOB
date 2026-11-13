#!/usr/bin/env python3
# Run outside the API worker: Monday 09:00 UTC, with a database-writing job role.
from __future__ import annotations

import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.ratings.calibrations import weekly_job  # noqa: E402
from app.services.ratings.locks import rating_lock  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", required=True, type=int)
    args = parser.parse_args()
    if not settings.rating_autotune or not settings.rating_prediction_log_enabled:
        print(json.dumps({"status": "disabled"}))
        return
    os.nice(10)
    if hasattr(os, "sched_getaffinity"):
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    code_sha = subprocess.check_output(["git", "-C", str(Path(__file__).resolve().parents[2]), "rev-parse", "HEAD"], text=True).strip() if (Path(__file__).resolve().parents[2] / ".git").exists() else os.environ.get("APP_COMMIT_SHA", "")
    build_sha = Path(__file__).resolve().parents[1] / "BUILD_SHA"
    if not code_sha and build_sha.exists():
        code_sha = build_sha.read_text().strip()
    if not code_sha:
        raise SystemExit("BUILD_SHA or APP_COMMIT_SHA is required in the job container")
    with rating_lock(f"fit:{args.season}", seconds=3600) as acquired:
        if not acquired:
            print(json.dumps({"status": "locked"}))
            return
        with SessionLocal() as db:
            result = weekly_job(db, args.season, now=datetime.now(timezone.utc), code_sha=code_sha)
            print(json.dumps(result))


if __name__ == "__main__":
    main()
