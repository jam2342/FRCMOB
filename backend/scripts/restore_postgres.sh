#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   DATABASE_URL=postgresql://... ./backend/scripts/restore_postgres.sh <backup_file> [target_database_url]
#
# WARNING:
#   This script uses --clean and --if-exists and will overwrite existing objects.

if ! command -v pg_restore >/dev/null 2>&1; then
  echo "pg_restore is required but not installed." >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <backup_file> [target_database_url]" >&2
  exit 1
fi

backup_file="$1"
if [[ ! -f "${backup_file}" ]]; then
  echo "Backup file not found: ${backup_file}" >&2
  exit 1
fi

target_database_url="${2:-${DATABASE_URL:-}}"
if [[ -z "${target_database_url}" ]]; then
  echo "Target database URL is required (arg 2 or DATABASE_URL)." >&2
  exit 1
fi

echo "Restoring PostgreSQL backup: ${backup_file}"
pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --dbname="${target_database_url}" \
  "${backup_file}"

echo "Restore complete."
