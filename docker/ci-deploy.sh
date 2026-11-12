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
# The full sha: the image records it (BUILD_SHA) and /health/deep reports it back.
if [[ ! "$request" =~ ^deploy\ ([0-9a-f]{40})$ ]]; then
  echo "refusing: expected 'deploy <40-character sha>', got '${request:0:80}'" >&2
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

# The TLS container is redone when its config or its compose definition changes.
tls_config() { cat "$APP/docker/Caddyfile" "$APP/docker/docker-compose.oci.yml" 2>/dev/null | sha256sum | cut -d' ' -f1; }
caddy_before=$(tls_config)
tar -xf "$bundle" -C "$APP"
caddy_after=$(tls_config)

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
# Baked into the image, so the running backend can say which commit it is.
echo "$sha" > "$APP/backend/BUILD_SHA"
"${COMPOSE[@]}" build backend
echo "== migrate"
"${COMPOSE[@]}" run --rm --no-deps backend python -m alembic upgrade head
echo "== restart"
"${COMPOSE[@]}" up -d backend
if [[ "$caddy_before" != "$caddy_after" ]]; then
  # Validate with the service's own mounts and env (certificates included), then
  # recreate it: a restart would keep the old mounts, and the Caddyfile is a
  # single-file bind mount that a replaced file only reaches through a new container.
  echo "== TLS config changed: validate and recreate"
  "${COMPOSE[@]}" run --rm --no-deps tls caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  "${COMPOSE[@]}" up -d --no-deps --force-recreate tls
fi

echo "== health"
# Healthy, and running this commit. A backend from before /health/deep reported its
# build has no "build" key at all; that still counts, so older commits can be redeployed.
is_this_build() { grep -q "\"build\":\"$sha\"" <<<"$1" || ! grep -q '"build":' <<<"$1"; }
for _ in $(seq 1 30); do
  if body=$(curl -fsS --max-time 10 http://127.0.0.1:8000/health/deep 2>/dev/null) && grep -q '"ok":true' <<<"$body" && is_this_build "$body"; then
    echo "$body"
    echo "$sha" > "$STATE/last_deployed_sha"
    echo "== deployed $sha"
    exit 0
  fi
  sleep 5
done
echo "health check failed after deploy; last answer: ${body:-none}"
exit 1
