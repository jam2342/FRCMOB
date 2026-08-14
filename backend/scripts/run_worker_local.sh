#!/usr/bin/env bash
# Run the RQ analysis worker locally on macOS, outside Docker.
#
# Two env vars are required on macOS or the forked RQ work-horse dies with
# SIGABRT ("crashed on child side of fork pre-exec") the moment it opens a
# Postgres connection:
#   OBJC_DISABLE_INITIALIZE_FORK_SAFETY - libpq's Kerberos check touches
#     CFPreferences, and Apple's ObjC runtime forbids class init in forked
#     children. Read by libobjc at process start, so it must be set in the
#     environment, not from Python.
#   PGGSSENCMODE=disable - skips the GSSAPI/Kerberos negotiation entirely
#     (local postgres doesn't use it anyway).
# Neither affects the Linux/Docker worker, which is the production path.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_ROOT="$REPO_ROOT/backend"
PYTHON="$REPO_ROOT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "error: $PYTHON not found - create the venv first" >&2
    exit 1
fi

cd "$BACKEND_ROOT"
exec env \
    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES \
    PGGSSENCMODE=disable \
    PYTHONPATH="$BACKEND_ROOT" \
    REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}" \
    "$PYTHON" "$REPO_ROOT/worker/worker.py"
