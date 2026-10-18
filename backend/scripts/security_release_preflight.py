"""Read-only release checks. Prints setting names/status only, never their values.

Run inside the candidate backend container, where settings are already injected.
This script does not open dotenv files, contact services, migrate, or change files.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
from typing import Mapping

_TRUE = {"1", "true", "on", "yes"}
_HOST = re.compile(r"(?i)^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


def environment_checks(env: Mapping[str, str], frontend_host: str) -> list[dict]:
    checks = []

    def add(name: str, passed: bool, remedy: str):
        checks.append({"check": name, "passed": bool(passed), "remedy": None if passed else remedy})

    add("production_environment", env.get("APP_ENV", "").lower() not in {"", "development", "testing"}, "Set APP_ENV=production.")
    for name in ("STRICT_STARTUP_ENV_VALIDATION", "ENFORCE_ADMIN_AUTH_FOR_WRITES", "ON_DEVICE_SYNC_REQUIRE_SIGNED_TOKEN"):
        add(name, env.get(name, "").lower() in _TRUE, f"Set {name}=true.")
    for name in ("ADMIN_API_KEY", "ADMIN_SESSION_TOKEN_SECRET"):
        add(name, len(env.get(name, "").strip().encode()) >= 32, f"Configure a unique {name} with at least 32 bytes; do not paste it into logs or chat.")
    for name in ("TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY"):
        add(name, bool(env.get(name, "").strip()), f"Configure {name} directly in the backend environment.")
    hosts = {host.strip().lower() for host in env.get("TURNSTILE_ALLOWED_HOSTNAMES", "").split(",") if host.strip()}
    add("TURNSTILE_ALLOWED_HOSTNAMES", bool(hosts) and all(_HOST.fullmatch(host) for host in hosts) and frontend_host.lower() in hosts,
        "Allow the frontend's exact hostname; use no scheme, path, port or wildcard.")
    origins = [origin.strip() for origin in env.get("CORS_ALLOW_ORIGINS", "").split(",") if origin.strip()]
    add("explicit_cors_origins", bool(origins) and all("*" not in origin for origin in origins), "Use explicit CORS origins without wildcards.")
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-host", required=True)
    parser.add_argument("--app-root", type=Path, default=Path.cwd())
    parser.add_argument("--media-root", type=Path, default=Path("/app/media"))
    args = parser.parse_args()
    if not _HOST.fullmatch(args.frontend_host):
        parser.error("--frontend-host must be an exact hostname")
    checks = environment_checks(os.environ, args.frontend_host)
    migration = args.app_root / "alembic/versions/20261017_0027_scout_entry_authors.py"
    checks.append({"check": "candidate_migration_present", "passed": migration.is_file(),
                   "remedy": None if migration.is_file() else "Build the reviewed candidate image containing migration 0027 before release."})
    try:
        reserve = max(1, int(os.environ.get("PIT_PHOTO_MINIMUM_FREE_BYTES", str(512 * 1024 * 1024))))
        disk_ready = shutil.disk_usage(args.media_root).free >= reserve
    except (OSError, ValueError):
        disk_ready = False
    checks.append({"check": "media_disk_reserve", "passed": disk_ready,
                   "remedy": None if disk_ready else "Verify the mounted media volume and its configured free-space reserve."})
    ready = all(check["passed"] for check in checks)
    print(json.dumps({"ready": ready, "read_only": True, "checks": checks}, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
