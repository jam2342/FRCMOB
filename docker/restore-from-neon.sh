#!/usr/bin/env bash
# Rebuild the database from the off-site copy in Neon (docs/DISASTER_RECOVERY.md).
# backup_database.sh refreshes that copy every hour.
#
#   sudo bash docker/restore-from-neon.sh             # a new VM: fill scouting-postgres's
#                                                     # empty database from Neon
#   sudo bash docker/restore-from-neon.sh --rehearse  # into a scratch database
#                                                     # (frcmob_dr_rehearsal); production untouched
#
# Credentials go to the Postgres client through a private env file, never on a command
# line where `ps` would show them.
set -euo pipefail

ROOT="${FRCMOB_ROOT:-/home/ubuntu/frcmob}"
WORK="${FRCMOB_RESTORE_DIR:-/home/ubuntu/backups}"
CONTAINER="${FRCMOB_POSTGRES_CONTAINER:-scouting-postgres}"
mode="${1:-}"

env_value() {
  grep -E "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"
}

umask 077
mkdir -p "$WORK"
db_user=$(env_value LOCAL_POSTGRES_USER); db_user=${db_user:-frcmob}
prod_db=$(env_value LOCAL_POSTGRES_DB); prod_db=${prod_db:-frcmob}
if [ "$mode" = "--rehearse" ]; then
  target_db=frcmob_dr_rehearsal
  docker exec "$CONTAINER" psql -U "$db_user" -d postgres -qc "DROP DATABASE IF EXISTS $target_db" -c "CREATE DATABASE $target_db"
else
  target_db="$prod_db"
  tables=$(docker exec "$CONTAINER" psql -U "$db_user" -d "$target_db" -tAc "select count(*) from information_schema.tables where table_schema = 'public'")
  if [ "${tables:-0}" -gt 0 ]; then
    echo "refusing: $target_db already has $tables tables. This is for an empty database on a new VM; use --rehearse to test." >&2
    exit 1
  fi
fi

neon=$(env_value NEON_BACKUP_URL | sed -e 's#postgresql+psycopg://#postgresql://#' -e 's#-pooler\.#.#')
[ -n "$neon" ] || { echo "NEON_BACKUP_URL is not set in $ROOT/.env" >&2; exit 1; }
pg_env_file=$(mktemp "$WORK/.neon-env.XXXXXXXX")
dump="$WORK/neon_restore_$(date -u +%Y%m%dT%H%MZ).dump"
trap 'rm -f "$pg_env_file" "$dump"' EXIT HUP INT TERM
printf '%s' "$neon" | python3 -c '
import sys
from urllib.parse import parse_qs, unquote, urlsplit

url = urlsplit(sys.stdin.read())
if url.scheme not in {"postgres", "postgresql"} or not url.hostname or not url.username or not url.path.strip("/"):
    raise SystemExit("invalid Neon connection URL")
query = parse_qs(url.query)
fields = {
    "PGHOST": url.hostname,
    "PGPORT": str(url.port or 5432),
    "PGUSER": unquote(url.username),
    "PGPASSWORD": unquote(url.password or ""),
    "PGDATABASE": unquote(url.path.lstrip("/")),
    "PGSSLMODE": query.get("sslmode", ["require"])[-1],
}
for key, value in fields.items():
    if "\n" in value or "\r" in value:
        raise SystemExit("invalid Neon connection field")
    print(f"{key}={value}")
' > "$pg_env_file"

started=$(date +%s)
echo "== dumping the Neon copy"
docker run --rm --env-file "$pg_env_file" postgres:17 pg_dump --format=custom --no-owner --no-privileges > "$dump"
echo "   $(du -h "$dump" | cut -f1) in $(( $(date +%s) - started ))s"
echo "== restoring into $target_db"
docker exec -i "$CONTAINER" pg_restore --no-owner --no-privileges -U "$db_user" -d "$target_db" < "$dump"
echo "== done in $(( $(date +%s) - started ))s"
docker exec "$CONTAINER" psql -U "$db_user" -d "$target_db" -tAc \
  "select 'alembic ' || version_num from alembic_version union all
   select 'events ' || count(*) from events union all
   select 'matches ' || count(*) from matches union all
   select 'team_workspaces ' || count(*) from team_workspaces"
