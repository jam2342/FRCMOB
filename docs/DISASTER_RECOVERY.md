# Disaster recovery: rebuilding the backend

Everything server-side runs on one OCI A1 VM (backend, Postgres, Redis, Caddy; see
`BACKEND_HOST.md`). This is how to get it back if that VM is lost. Re-run the
rehearsal (end of this page) after big infrastructure changes.

**Last rehearsal, 2026-10-16:** the Neon copy (36 MB) dumped in 23 s and restored in 40 s;
a backend on it was healthy 11 s later and served 2026arc's 141 matches. Row counts matched
production except 30 matches newer than the last hourly copy, the expected lag.

## What survives losing the VM

| Piece | Where the copy lives | Freshness |
|---|---|---|
| Database | Neon (`NEON_BACKUP_URL`), restored into by `docker/backup_database.sh` | hourly |
| Code | GitHub (`main`) | every merge |
| Secrets (`.env`) | Jamal's Mac: `~/.config/frcmob/backend.env` (mode 600, never in git) | refresh after any `.env` change: `scp -i ~/.ssh/frcmob_oci ubuntu@<ip>:frcmob/.env ~/.config/frcmob/backend.env` |
| TLS certificate | nothing needed: Caddy gets a new one on first start | — |
| Frontend | Vercel, unaffected | — |
| Pit photos | OCI Object Storage bucket `frcmob-media-backup` (`pit_photos/`), copied by `docker/backup_database.sh` | hourly |

Pit photos come back with `rclone` from the bucket once the new VM is in the
`frcmob-backend-vm` dynamic group (`BACKEND_HOST.md`, "Pit photos off-site"). Without
that step a Neon restore brings back the rows that point at them, and the images 404.

## Rebuild on a new VM (~30–45 min, most of it waiting)

1. **VM:** OCI console → Compute → create an `VM.Standard.A1.Flex` (4 OCPU / 24 GB,
   Ubuntu 24.04, Phoenix), add Jamal's `frcmob_oci` public key. Security list: allow
   only 22 and 443 (and ICMP).
2. **Docker:** `curl -fsSL https://get.docker.com | sudo sh`
3. **Code and secrets:** from the Mac, in `Scouting app/`:
   ```bash
   ssh -i ~/.ssh/frcmob_oci ubuntu@<new-ip> 'mkdir -p ~/frcmob'
   git archive main backend docker .dockerignore | ssh -i ~/.ssh/frcmob_oci ubuntu@<new-ip> 'cd ~/frcmob && tar -x'
   scp -i ~/.ssh/frcmob_oci ~/.config/frcmob/backend.env ubuntu@<new-ip>:frcmob/.env
   ```
   If the IP changed, update `BACKEND_TLS_HOST` in `.env` (`<a-b-c-d>.sslip.io`).
4. **Database:** start Postgres and Redis, then fill Postgres from Neon:
   ```bash
   cd ~/frcmob && sudo docker compose --env-file .env -f docker/docker-compose.oci.yml --profile tls up -d postgres redis
   sudo bash docker/restore-from-neon.sh
   ```
   It refuses a database that already has tables.
5. **App:** build and start everything (separate SSH calls; long builds under `nohup`):
   ```bash
   sudo docker compose --env-file .env -f docker/docker-compose.oci.yml --profile tls build backend
   sudo docker compose --env-file .env -f docker/docker-compose.oci.yml --profile tls run --rm --no-deps backend python -m alembic upgrade head
   sudo docker compose --env-file .env -f docker/docker-compose.oci.yml --profile tls up -d
   curl -fsS http://127.0.0.1:8000/health/deep
   ```
6. **Cron:** reinstall root's hourly backup: `17 * * * * /home/ubuntu/frcmob/docker/backup_database.sh`
   (see `BACKEND_HOST.md`).
7. **If the IP changed:** point everything at the new host. That means the Vercel rewrite in
   `ScoutingApp/vercel.json`, `NATIVE_WS_URL`/`NATIVE_API_URL` in `src/platform/runtime.ts`
   (also the CSP's socket origin), the deploy workflow's host and `DEPLOY_KNOWN_HOSTS`
   secret, and the deploy key's line in `~/.ssh/authorized_keys`. A real domain makes
   this one DNS change.

## Rehearsal (no new VM, production untouched)

```bash
scp -i ~/.ssh/frcmob_oci docker/restore-from-neon.sh ubuntu@<ip>:/tmp/
ssh -i ~/.ssh/frcmob_oci ubuntu@<ip> 'sudo bash /tmp/restore-from-neon.sh --rehearse'
```
restores Neon into a scratch database `frcmob_dr_rehearsal`. To boot a throwaway backend on
it, use `docker compose … run -d --rm --no-deps --name frcmob-dr-backend -p 127.0.0.1:8099:8000`
with `DATABASE_URL` pointed at the scratch database, `REDIS_URL` at a spare Redis database
(`/9`) and `AUTOMATION_REGIONAL_ENABLED=false`, then curl `127.0.0.1:8099/health/deep`.
Afterwards, drop the scratch database and `FLUSHDB` Redis db 9.
