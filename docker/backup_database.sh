#!/bin/sh
# Hourly backup of the production database, run by root's cron on the backend VM
# (see docs/BACKEND_HOST.md). The database runs on this VM (scouting-postgres), so
# a dump alone would share its fate; each dump is therefore also restored into
# Neon, the off-site copy DATABASE_URL can be switched back to if the VM is lost.
# Local dumps are only rotated after the new one proves readable.
set -eu

ROOT="${FRCMOB_ROOT:-/home/ubuntu/frcmob}"
DEST="${FRCMOB_BACKUP_DIR:-/home/ubuntu/backups}"
KEEP="${FRCMOB_BACKUP_KEEP:-72}"
CONTAINER="${FRCMOB_POSTGRES_CONTAINER:-scouting-postgres}"

env_value() {
  grep -E "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"
}

umask 077
mkdir -p "$DEST"
db_user=$(env_value LOCAL_POSTGRES_USER); db_user=${db_user:-frcmob}
db_name=$(env_value LOCAL_POSTGRES_DB); db_name=${db_name:-frcmob}

name="frcmob_$(date -u +%Y%m%dT%H%MZ).dump"
docker exec "$CONTAINER" pg_dump --format=custom --no-owner -U "$db_user" -d "$db_name" > "$DEST/$name"
tables=$(docker exec -i "$CONTAINER" pg_restore --list < "$DEST/$name" | grep -c 'TABLE DATA')
[ "$tables" -gt 0 ] || { echo "backup failed: $name has no table data" >&2; exit 1; }
chmod 700 "$DEST"
chmod 600 "$DEST/$name"

# Keep the newest $KEEP dumps (three days hourly); dumps from before the move age out after 14 days.
ls -1t "$DEST"/frcmob_*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
find "$DEST" -name 'neon_*.dump' -mtime +14 -delete
echo "backup ok $name tables=$tables size=$(du -h "$DEST/$name" | cut -f1)"

# Off-site copy. Neon's direct endpoint, never the "-pooler" one: pg_restore sets
# session state (search_path) that PgBouncer in transaction mode leaks onto shared
# connections; on 2026-09-26 a pg_dump through it hid every table from the app.
# One transaction, so Neon holds either the previous copy or this one, never half.
neon=$(env_value NEON_BACKUP_URL | sed -e 's#postgresql+psycopg://#postgresql://#' -e 's#-pooler\.#.#')
if [ -z "$neon" ]; then
  echo "offsite skipped: NEON_BACKUP_URL not set" >&2
  exit 0
fi
case "$neon" in *-pooler.*) echo "offsite refused: still pointed at the Neon pooler" >&2; exit 1 ;; esac
pg_env_file=$(mktemp "$DEST/.neon-env.XXXXXXXX")
trap 'rm -f "$pg_env_file"' EXIT HUP INT TERM
printf '%s' "$neon" | python3 -c '
import sys
from urllib.parse import parse_qs, unquote, urlsplit

url = urlsplit(sys.stdin.read())
if url.scheme not in {"postgres", "postgresql"} or not url.hostname or not url.username or not url.path.strip("/"):
    raise SystemExit("offsite refused: invalid Neon connection URL")
query = parse_qs(url.query)
fields = {
    "PGHOST": url.hostname,
    "PGPORT": str(url.port or 5432),
    "PGUSER": unquote(url.username),
    "PGPASSWORD": unquote(url.password or ""),
    "PGDATABASE": unquote(url.path.lstrip("/")),
    "PGSSLMODE": query.get("sslmode", ["require"])[-1],
}
if "channel_binding" in query:
    fields["PGCHANNELBINDING"] = query["channel_binding"][-1]
for key, value in fields.items():
    if "\n" in value or "\r" in value:
        raise SystemExit("offsite refused: invalid Neon connection field")
    print(f"{key}={value}")
' > "$pg_env_file"
docker run --rm --env-file "$pg_env_file" -e BACKUP_NAME="$name" -v "$DEST:/backup:ro" postgres:17 \
  sh -c 'pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction --dbname="$PGDATABASE" "/backup/$BACKUP_NAME"'
echo "offsite ok $name -> neon"
