#!/bin/sh
set -eu
: "${1:?Usage: fit-rating-calibrations.sh SEASON}"
season=$1
shift
repo_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
exec docker compose -f "$repo_dir/docker/docker-compose.oci.yml" run --rm --no-deps \
    --entrypoint python backend scripts/fit_rating_calibrations.py --season "$season" "$@"
