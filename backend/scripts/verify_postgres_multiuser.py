#!/usr/bin/env python3
"""Run beta concurrency checks on a new, Unix-socket-only PostgreSQL cluster.

No existing database, dotenv file, Docker service, or production credential is used.
Requires PostgreSQL server binaries and the backend test dependencies locally.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TESTS = [
    "tests/test_workspace_postgres_e2e.py",
    "tests/test_on_device_sync_postgres_e2e.py",
    "tests/test_team_workspaces.py",
    "tests/test_routes_tracks_on_device_sync.py",
    "tests/test_ml_model_selection.py",
    "tests/test_ml_shadow_rollout.py",
    "tests/test_routes_events_schedule_ml.py",
]


def worker(database_url: str, tests: list[str]) -> int:
    sys.path.insert(0, str(BACKEND_ROOT))
    from tests.disposable_config import configure_disposable_database

    configure_disposable_database(database_url)
    from app.db import models  # noqa: F401
    from app.db.base import Base
    from app.db.session import engine
    import pytest

    Base.metadata.create_all(engine)
    os.environ["DATABASE_URL"] = database_url
    os.environ["RUN_POSTGRES_INTEGRATION_E2E"] = "1"
    try:
        return pytest.main(["-q", *tests])
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", type=Path)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args()
    tests = args.tests or DEFAULT_TESTS
    if args.worker:
        return worker(args.worker, tests)

    candidates = [
        args.postgres_bin,
        Path(shutil.which("postgres")).parent if shutil.which("postgres") else None,
        Path("/opt/homebrew/opt/postgresql@17/bin"),
        Path("/usr/lib/postgresql/17/bin"),
    ]
    pg_bin = next((path for path in candidates if path and (path / "postgres").is_file()), None)
    if pg_bin is None:
        parser.error("PostgreSQL server binaries missing; supply --postgres-bin.")
    clean_env = {"PATH": f"{Path(sys.executable).parent}:{pg_bin}:/usr/bin:/bin", "LC_ALL": "C"}
    with tempfile.TemporaryDirectory(prefix="frcmob-pg-", dir="/tmp") as scratch:
        root = Path(scratch).resolve()
        data = root / "data"
        socket_dir = root / "socket"
        socket_dir.mkdir(mode=0o700)
        log = root / "postgres.log"
        subprocess.run(
            [str(pg_bin / "initdb"), "-D", str(data), "-U", "frcmob_test", "--no-locale",
             "--encoding=UTF8", "--auth-local=trust", "--auth-host=reject"],
            env=clean_env, check=True, stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [str(pg_bin / "pg_ctl"), "-D", str(data), "-l", str(log), "-w", "start", "-o",
             f"-k {socket_dir} -c listen_addresses='' -c max_connections=40"],
            env=clean_env, check=True, stdout=subprocess.DEVNULL,
        )
        try:
            import psycopg

            with psycopg.connect(host=str(socket_dir), user="frcmob_test", dbname="postgres", autocommit=True) as conn:
                conn.execute("CREATE DATABASE frcmob_beta_test")
            database_url = f"postgresql+psycopg://frcmob_test@/frcmob_beta_test?host={socket_dir}"
            print("Running real PostgreSQL checks on an isolated local Unix socket.", flush=True)
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--worker", database_url, *tests],
                cwd=BACKEND_ROOT, env=clean_env,
            )
            return result.returncode
        finally:
            subprocess.run(
                [str(pg_bin / "pg_ctl"), "-D", str(data), "-w", "stop", "-m", "fast"],
                env=clean_env, check=True, stdout=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    raise SystemExit(main())
