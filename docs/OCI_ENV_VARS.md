# Production environment variables

Production runs on one OCI VM with `docker/docker-compose.oci.yml` reading
`~/frcmob/.env` (mode 600, never in git). See [BACKEND_HOST.md](BACKEND_HOST.md) for
the host itself. This is the reference for what goes in that `.env`.

Never write secret values into this file. `<from .env>` means the value lives only
on the VM. Every key that was ever pasted into chat (TBA, FRC Events, the old Neon and
Upstash credentials, the Roboflow key) should be treated as compromised and rotated.

## 1. Connection and infrastructure — required

| Key | Value |
|---|---|
| `LOCAL_POSTGRES_DB` / `LOCAL_POSTGRES_USER` / `LOCAL_POSTGRES_PASSWORD` | `<from .env>` — the on-VM Postgres |
| `DATABASE_URL` | `postgresql+psycopg://<user>:<password>@postgres:5432/<db>` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `NEON_BACKUP_URL` | `<from .env>` — Neon **direct** host, used only by `docker/backup_database.sh` |
| `CORS_ALLOW_ORIGINS` | `https://scouting-app-iryg.vercel.app` |
| `APP_ENV` | `production` |
| `STRICT_STARTUP_ENV_VALIDATION` | `true` |
| `ENFORCE_ADMIN_AUTH_FOR_WRITES` | `true` |
| `UVICORN_WORKERS` | `2` |
| `BACKEND_TLS_HOST` | `141-148-171-128.sslip.io` (changes with the public IP) |

`DATABASE_URL` must use `postgresql+psycopg://` (psycopg3). If you ever point it back
at Neon, drop `&channel_binding=require`.

## 2. Admin auth — required

| Key | Value |
|---|---|
| `ADMIN_API_KEY` | `<from .env>` — keep it ASCII: non-ASCII characters break raw `X-Admin-Key` headers through Caddy |
| `ADMIN_API_HEADER` | `X-Admin-Key` |
| `ADMIN_SESSION_TOKEN_SECRET` | `<from .env>` |
| `ADMIN_SESSION_TTL_SEC` | `7200` |
| `ON_DEVICE_SYNC_REQUIRE_SIGNED_TOKEN` | `true` (startup refuses `false` in production) |

## 3. External APIs

| Key | Value |
|---|---|
| `TBA_AUTH_KEY` | `<from .env>` |
| `FIRST_FRC_API_BASE_URL` | `https://frc-api.firstinspires.org/v3.0` |
| `FIRST_FRC_API_USERNAME` / `FIRST_FRC_API_AUTH_KEY` | `<from .env>`, both or neither |
| `STATBOTICS_BASE_URL` | `https://api.statbotics.io/v3` |

## 4. ML

The shadow models (team strength, match outcome, synergy, roles) train on official
data. Without the blend knobs, ratings and predictions ship deterministic-only.

| Key | Value |
|---|---|
| `ML_SHADOW_ENABLED` | `true` |
| `ML_SHADOW_ROLLOUT_RATIO` | `1.0` |
| `ML_SHADOW_AUTO_TRAIN_ON_EVENT_BREAKDOWN` | `true` — trains once per regional-automation tick |
| `ML_SHADOW_AUTO_TRAIN_LIMIT_EVENTS` | `250` |
| `ML_SHADOW_AUTO_TRAIN_ACTIVATE` | `false` — startup refuses `true` in production; promote reviewed models explicitly |
| `ML_SHADOW_AUTO_TRAIN_RECOMPUTE_RATINGS` | `true` |
| `ML_SHADOW_AUTO_TRAIN_CURRENT_SEASON_ONLY` | `true` |
| `ML_MATCH_OUTCOME_BLEND` / `ML_TEAM_STRENGTH_BLEND` / `ML_SYNERGY_BLEND` / `ML_ROLE_BLEND` | `0.20` each |

## 5. Scheduler

| Key | Value |
|---|---|
| `AUTOMATION_REGIONAL_ENABLED` | `true` |
| `AUTOMATION_REGIONAL_HALFDAY_SCHEDULER_ENABLED` | `true` |
| `AUTOMATION_REGIONAL_HALFDAY_SEASON` | `2026` (bump each season — see `SEASON_TRANSITION.md`) |
| `AUTOMATION_REGIONAL_HALFDAY_INTERVAL_HOURS` | `12` |
| `AUTOMATION_REGIONAL_INCLUDE_ALL_EVENTS` | `true` — refresh every completed event, not just in-region ones |
| `AUTOMATION_REGIONAL_RUN_POST_COMPUTE` | `true` |
| `INTEL_SNAPSHOT_REFRESH_ENABLED` | `true` |
| `INTEL_SNAPSHOT_REFRESH_INTERVAL_MINUTES` | `2` |

Keys from the retired broadcast-video pipeline (`VIDEO_*`, `LIVE_ANALYSIS_*`,
`ANALYSIS_JOB_*`, `ANALYSIS_LOW_QUALITY_*`, `FRESHNESS_RECOVERY_*`, `STORAGE_CLEANUP_*`,
`MEDIA_CLEANUP_*`, `AUTOMATION_REGIONAL_*CALIBRAT*`, `AUTOMATION_REGIONAL_REQUIRE_VIDEO`)
are ignored and can be deleted from the `.env`.

## After a restart

```bash
ssh -i ~/.ssh/frcmob_oci ubuntu@<ip> 'sudo docker logs --tail 80 scouting-backend'
```

Look for `Background scheduler initialized on startup` and
`Scheduled regional post-event automation job added`. A
`RuntimeError: Startup env validation failed: ...` names the variable that is empty or
wrong; fix it and restart.
