#!/bin/sh
# Starts as root only long enough to hand the media volume to the app user (files
# written before the image dropped root are root-owned), then runs the command as
# that user. `docker exec` still enters as root, so backup/restore scripts keep working.
set -eu

if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/media
  find /app/media ! -user app -exec chown app:app {} +
  exec su-exec app "$@"
fi
exec "$@"
