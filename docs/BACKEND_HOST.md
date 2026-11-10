# Backend host

Since 2026-09-24 the backend runs on one OCI VM, not on Container Instances. The
container instance this replaced was stopped on 2026-04-29, which is why production
was offline from then until the move. Until 2026-09-28 an RQ worker container ran
next to it for broadcast-video analysis; that pipeline is retired and the worker is
gone. Everything scheduled (official-data refresh, live ingest, ratings) runs inside
the backend's own scheduler.

| | |
|---|---|
| Instance | `frcmob-backend`, `VM.Standard.A1.Flex`, 4 OCPU / 24 GB, 100 GB boot volume, Phoenix AD-1 |
| Cost | Always Free (A1 up to 4 OCPU / 24 GB and 200 GB block storage are free on Pay-As-You-Go) |
| OS | Ubuntu 24.04 aarch64, Docker + Compose v2 from apt |
| Public IP | the one in `ScoutingApp/vercel.json` — ephemeral, survives stop/start, released only if the VM is terminated |
| Services | `docker/docker-compose.oci.yml`: `scouting-tls` (Caddy, :443) → `scouting-backend` (:8000), plus `scouting-postgres` and `scouting-redis`, all `restart: unless-stopped` |
| SSH | `ssh -i ~/.ssh/frcmob_oci ubuntu@<ip>` (key lives on Jamal's Mac, not in git) |
| Data | Postgres 17 (`scouting-postgres`) and Redis 7 (`scouting-redis`) on this VM since 2026-09-27; Neon (us-east-1) holds an hourly off-site copy |

Inbound, the security list allows only 22 (SSH) and 443 (HTTPS) plus ICMP. The
backend's 8000 is published on the VM's loopback only. The VCN has one security list (`Default Security List for FRCSCOUTING`, on
`frc-public-subnet`); the `ig-quick-action-NSG` group is not attached to the VM, so
rules added there do nothing. Docker publishes ports through its own chains, so the
host's INPUT REJECT rule doesn't block them.

## TLS

Since 2026-11-10 everything reaches the VM through Cloudflare's proxy (SSL mode
Full (strict)): `frcmob.com/api/*` from the web and native apps, `api.frcmob.com` for
deploy health checks, CI's regression job and Vercel's old `/api` rewrite. Caddy
(`tls` service, profile `tls`) terminates TLS with one Cloudflare Origin CA
certificate for `frcmob.com`, `www` and `api`, valid to 2041, kept root-only in
`/home/ubuntu/frcmob-tls/` (`TLS_CERTS_DIR`, mounted at `/etc/caddy/certs`) and never
in git, and proxies to `backend:8000`. Port 80 stays closed.

**Only Cloudflare gets in.** Every site starts with `cloudflare_only`, which drops any
connection whose peer isn't a Cloudflare edge range, so Cloudflare's rate limiting and
cache can't be bypassed by calling the IP. Caddy takes the visitor's address from
`CF-Connecting-IP` only from those ranges (`trusted_proxies`) and passes it on as
`X-Forwarded-For`. Both lists are in the Caddyfile; refresh them from
cloudflare.com/ips if Cloudflare adds ranges. Checks from the VM itself use
`http://127.0.0.1:8000`. The old `141-148-171-128.sslip.io` name was retired the same
day.

When the Caddyfile or `docker-compose.oci.yml` changes, `ci-deploy.sh` validates the
`tls` service with its own mounts and recreates it.

Every compose command on the VM needs `--profile tls`, or compose leaves Caddy out.
If the public IP changes, update the three proxied A records in Cloudflare (and the
deploy workflow's SSH host); nothing in the app names the IP.

## Layout on the VM

```
~/frcmob/
  .env                     # production env, mode 600 — never copied back into git
  backend/ docker/ .dockerignore   # git archive of main
```

The `.env` is the old container's env merged with the repo `.env` for keys the
container never set, minus every `*_TEXAS_*` key, with:
`APP_ENV=production`,
`ON_DEVICE_SYNC_REQUIRE_SIGNED_TOKEN=true`. Leftover `VIDEO_*`, `LIVE_ANALYSIS_*` and
`ANALYSIS_JOB_*` keys from the broadcast pipeline are ignored (settings use
`extra="ignore"`). The local repo `.env` points
`DATABASE_URL` at localhost — do not ship it as-is.

## Redeploy after merging to main

```bash
cd "Scouting app"
git archive main backend docker .dockerignore \
  | ssh -i ~/.ssh/frcmob_oci ubuntu@<ip> 'cd ~/frcmob && tar -x'
ssh -i ~/.ssh/frcmob_oci ubuntu@<ip> \
  'cd ~/frcmob && sudo docker compose --env-file .env -f docker/docker-compose.oci.yml --profile tls up -d --build'
```

The image has no torch since 2026-10-17 (it was only there for the ML shadow models,
now removed). Before 2026-09-28 it also carried the CUDA wheels, ultralytics and EasyOCR
and weighed ~11 GB.

## Database and Redis on the VM

Until 2026-09-27 the app used Neon (us-east-1) and Upstash Redis. From Phoenix every
query cost ~58 ms and every Redis command ~81 ms, so a page making 20 queries lost
over a second to distance. Both now run on the VM on the compose network (never
published): measured on the VM, match schedule 911 → 18 ms, event ratings 784 → 36 ms,
event team intel 492 → 27 ms.

- **Credentials** live only in `~/frcmob/.env`: `LOCAL_POSTGRES_DB/USER/PASSWORD` and
  `DATABASE_URL=postgresql+psycopg://<user>:<password>@postgres:5432/<db>`,
  `REDIS_URL=redis://redis:6379/0`. `NEON_BACKUP_URL` is the old Neon URL, used only by
  the backup. `.env.bak-before-local-db` is the pre-move env (Neon + Upstash).
- **Data** is in the `postgres_data` and `redis_data` volumes. Redis is append-only with
  `noeviction` so scheduler locks and automation bookkeeping survive restarts.
- **Pools:** `DB_POOL_SIZE=10`, `DB_MAX_OVERFLOW=20` per process against
  `max_connections=200`. The old 5+5 cap only ever applied to Neon's pooler.

## Migrations

Take a backup first (`sudo docker/backup_database.sh`), then run them from the new image:

```bash
cd ~/frcmob && sudo docker compose --env-file .env -f docker/docker-compose.oci.yml \
  run --rm --no-deps backend python -m alembic upgrade head
```

Rehearse anything risky on a restored copy (a throwaway `postgres:17` container fed a
dump from `/home/ubuntu/backups`) before touching production.

## Monitoring and backups

- **Uptime:** `.github/workflows/uptime.yml` checks `https://frcmob.com/api/health/deep`
  every 2 hours (Vercel rewrite → VM → Postgres/Redis) and fails, emailing whoever last
  edited the cron, if the API, database or Redis is down. Run it by hand from the Actions
  tab (`workflow_dispatch`).
- **`/health/deep`** reads a real table (not `SELECT 1`), pings Redis and repeats the
  startup env validation.
- **Backups, hourly** (root cron at :17): `docker/backup_database.sh` dumps the local
  database (`pg_dump` inside `scouting-postgres`), verifies it with `pg_restore --list`,
  keeps the newest 72 in `/home/ubuntu/backups` (mode 600, root only, log in `backup.log`),
  then restores that dump into Neon in one transaction — the off-site copy. Never point
  anything at Neon's `-pooler` host for dump/restore (it leaks `search_path`; the script refuses).
- **Pit photos off-site, hourly** (same script, since 2026-10-18): `rclone copy` adds new
  photos to the Object Storage bucket `frcmob-media-backup` (namespace `axdzkwqzv9hk`,
  Phoenix, private, Always Free 20 GB; photos are capped at 5 GB). rclone v1.75 is at
  `/usr/local/bin/rclone` (official release, checksum-verified; Ubuntu's 1.60 has no Oracle
  backend); its config `/root/.config/rclone/rclone.conf` holds no secret: the VM signs in
  as itself through dynamic group `frcmob-backend-vm` (Default domain, matches this
  instance's OCID). Policy `frcmob-media-backup` allows only `OBJECT_CREATE`,
  `OBJECT_INSPECT` and `OBJECT_READ` on that bucket, so the VM can't delete or overwrite
  a backed-up photo; deleting needs a person signed in to OCI. **A new VM needs its OCID
  added to the dynamic group's matching rule.** New IAM rules take about a minute to apply.
  Restore: `sudo /usr/local/bin/rclone copy oos:frcmob-media-backup/pit_photos <media volume>/pit_photos`.
  The same bucket's `archive/ocir-2026-10-18/` holds the record of the old container-registry
  images (deleted 2026-10-18): see `docs/archive/2026-10/OCIR_ARCHIVE_2026-10-18.md`.

## If the VM or its disk is lost

Neon has the database as of the last hourly backup, and the Object Storage bucket has
the pit photos (see above for the restore command; on a new VM, add its OCID to the
`frcmob-backend-vm` dynamic group first). Point `DATABASE_URL` in `.env` at
`NEON_BACKUP_URL` (the pooler host is fine for the app), set `REDIS_URL` to any Redis
(or the Upstash one in `.env.bak-before-local-db`), and bring the stack up on a new VM.
To move back later, repeat the 2026-09-27 cutover: stop the backend, dump Neon
(direct host), restore into local Postgres, compare row counts per table, switch the env.

## Checks

```bash
curl -s https://frcmob.com/api/health/deep   # api, database, redis, env validation
ssh -i ~/.ssh/frcmob_oci ubuntu@<ip> 'sudo docker ps; sudo docker logs --tail 50 scouting-backend'
```

## Why it's slow when it's slow

Queries are local now, so the database is rarely the reason. Remaining costs: external
APIs (TBA, FRC Events) on cold paths, event ingest (network-bound on TBA), and
the network between the user and Phoenix (~50-150 ms per request from most of the US).
