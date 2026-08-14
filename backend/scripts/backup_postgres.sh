#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   DATABASE_URL=postgresql://... ./backend/scripts/backup_postgres.sh [output_file]
# Default output:
#   ./backups/frc_YYYYmmdd_HHMMSS.dump

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "pg_dump is required but not installed." >&2
  exit 1
fi

DATABASE_URL_VALUE="${DATABASE_URL:-}"
if [[ -z "${DATABASE_URL_VALUE}" ]]; then
  echo "DATABASE_URL is required." >&2
  exit 1
fi

timestamp="$(date +%Y%m%d_%H%M%S)"
default_output="backups/frc_${timestamp}.dump"
output_file="${1:-${default_output}}"

mkdir -p "$(dirname "${output_file}")"

echo "Creating PostgreSQL backup: ${output_file}"
pg_dump \
  --format=custom \
  --no-owner \
  --no-privileges \
  --file "${output_file}" \
  "${DATABASE_URL_VALUE}"

echo "Backup complete: ${output_file}"
