#!/usr/bin/env bash
# Backend deploy, run on the VM by the CI deploy key (.github/workflows/deploy.yml).
#
# Installed once OUTSIDE ~/frcmob (the deploy rewrites that tree, and bash reads a
# running script as it goes) and pinned as the key's forced command in
# ~/.ssh/authorized_keys, so the key can do this and nothing else:
#
#   command="/home/ubuntu/frcmob-deploy/ci-deploy.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA… frcmob-ci-deploy
#
# Input: a `git archive <sha> backend docker .dockerignore` tarball on stdin, and
# "deploy <sha>" as the requested command. Same steps as docs/BACKEND_HOST.md, in the
# order that's safe for migrations: build → alembic upgrade → up -d → health check.
set -euo pipefail

APP=/home/ubuntu/frcmob
STATE=/home/ubuntu/frcmob-deploy
COMPOSE=(sudo docker compose --env-file "$APP/.env" -f "$APP/docker/docker-compose.oci.yml" --profile tls)

request="${SSH_ORIGINAL_COMMAND:-}"
if [[ ! "$request" =~ ^deploy\ ([0-9a-f]{7,40})$ ]]; then
  echo "refusing: expected 'deploy <sha>', got '${request:0:80}'" >&2
  exit 2
fi
sha="${BASH_REMATCH[1]}"

mkdir -p "$STATE/logs"
log="$STATE/logs/$(date -u +%Y%m%dT%H%M%SZ)-${sha:0:12}.log"
exec > >(tee -a "$log") 2>&1
# One deploy at a time; a second one waits up to 20 min for the first.
exec 9>"$STATE/deploy.lock"
flock -w 1200 9 || { echo "another deploy is still running"; exit 75; }

echo "== deploy $sha $(date -u +%FT%TZ)"
bundle=$(mktemp "$STATE/bundle.XXXXXX.tar")
trap 'rm -f "$bundle"' EXIT
cat > "$bundle"
# Only the paths a backend deploy may touch, and nothing that climbs out of them.
listing=$(tar -tf "$bundle")
if [[ -z "$listing" ]] || grep -qvE '^(backend/|docker/|\.dockerignore$)' <<<"$listing" || grep -qE '(^/|\.\./)' <<<"$listing"; then
  echo "refusing: the bundle holds paths outside backend/, docker/ and .dockerignore"
  exit 2
fi

caddy_before=$(sha256sum "$APP/docker/Caddyfile" 2>/dev/null | cut -d' ' -f1 || true)
tar -xf "$bundle" -C "$APP"
caddy_after=$(sha256sum "$APP/docker/Caddyfile" | cut -d' ' -f1)

# tar -x only adds and overwrites, so a file deleted from the repo stayed on disk and
# in every image built after it (five removed modules were still there on 2026-10-17).
# Prune the code folders only: never media/ (pit photos), .env or anything else.
pruned=0
while IFS= read -r -d '' path; do
  if ! grep -qxF -- "${path#"$APP/"}" <<<"$listing"; then
    rm -f -- "$path"
    pruned=$((pruned + 1))
  fi
done < <(find "$APP/backend/app" "$APP/backend/alembic" "$APP/backend/scripts" "$APP/backend/tests" "$APP/backend/config" \
  -type f -not -path '*/__pycache__/*' -print0 2>/dev/null)
find "$APP/backend/app" "$APP/backend/alembic" "$APP/backend/scripts" "$APP/backend/tests" "$APP/backend/config" \
  -mindepth 1 -type d -empty -delete 2>/dev/null || true
echo "== pruned $pruned file(s) no longer in the repo"

echo "== build"
"${COMPOSE[@]}" build backend
echo "== migrate"
"${COMPOSE[@]}" run --rm --no-deps backend python -m alembic upgrade head
echo "== restart"
"${COMPOSE[@]}" up -d backend
if [[ "$caddy_before" != "$caddy_after" ]]; then
  # The Caddyfile is a single-file bind mount: a replaced file needs a restart to be seen.
  echo "== Caddyfile changed: validate and restart TLS"
  sudo docker run --rm -e BACKEND_TLS_HOST=141-148-171-128.sslip.io -v "$APP/docker/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.10-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  sudo docker restart scouting-tls
fi

echo "== health"
for _ in $(seq 1 30); do
  if body=$(curl -fsS --max-time 10 http://127.0.0.1:8000/health/deep 2>/dev/null) && grep -q '"ok":true' <<<"$body"; then
    echo "$body"
    echo "$sha" > "$STATE/last_deployed_sha"
    echo "== deployed $sha"
    exit 0
  fi
  sleep 5
done
echo "health check failed after deploy; last answer: ${body:-none}"
exit 1
