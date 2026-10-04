# Backend host

Since 2026-09-24 the backend runs on one OCI VM, not on Container Instances. The
container instance this replaced was stopped on 2026-04-29, which is why production
was offline from then until the move. Until 2026-09-28 an RQ worker container ran
next to it for broadcast-video analysis; that pipeline is retired and the worker is
gone. Everything scheduled (official-data refresh, ratings, ML training) runs inside
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

Since 2026-09-26 everything reaches the API over HTTPS at
`https://141-148-171-128.sslip.io`: the Vercel `/api` rewrite and the uptime check
through Vercel. Caddy (`tls` service, profile `tls`) terminates
it and proxies to `backend:8000`. sslip.io resolves that name to the IP inside it,
so no domain is needed. Caddy gets and renews the Let's Encrypt certificate over
TLS-ALPN on 443 (port 80 stays closed) and keeps it in the `caddy_data` volume.

Every compose command on the VM needs `--profile tls`, or compose leaves Caddy out.
If the public IP changes, the sslip name changes with it: set `BACKEND_TLS_HOST` in
`.env` and update `vercel.json`.

## Layout on the VM

```
~/frcmob/
  .env                     # production env, mode 600 — never copied back into git
  backend/ docker/ .dockerignore   # git archive of main
```

The `.env` is the old container's env merged with the repo `.env` for keys the
container never set, minus every `*_TEXAS_*` key, with:
`APP_ENV=production`, `ML_SHADOW_AUTO_TRAIN_ACTIVATE=false`,
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

`Dockerfile.prod` installs torch from the CPU-only wheel index (the ML shadow models
need torch; the host has no GPU). Before 2026-09-28 the image carried the CUDA wheels,
ultralytics and EasyOCR and weighed ~11 GB.

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

- **Uptime:** `.github/workflows/uptime.yml` checks `https://scouting-app-iryg.vercel.app/api/health/deep`
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

## If the VM or its disk is lost

Neon has the database as of the last hourly backup. Point `DATABASE_URL` in `.env` at
`NEON_BACKUP_URL` (the pooler host is fine for the app), set `REDIS_URL` to any Redis
(or the Upstash one in `.env.bak-before-local-db`), and bring the stack up on a new VM.
To move back later, repeat the 2026-09-27 cutover: stop the backend, dump Neon
(direct host), restore into local Postgres, compare row counts per table, switch the env.

## Checks

```bash
curl -s https://scouting-app-iryg.vercel.app/api/health/deep   # api, database, redis, env validation
ssh -i ~/.ssh/frcmob_oci ubuntu@<ip> 'sudo docker ps; sudo docker logs --tail 50 scouting-backend'
```

## Why it's slow when it's slow

Queries are local now, so the database is rarely the reason. Remaining costs: external
APIs (TBA, Statbotics) on cold paths, event ingest (network-bound on TBA), and
the network between the user and Phoenix (~50-150 ms per request from most of the US).
