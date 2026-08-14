# OCI Container Instance — environment variables

This is the complete list of env vars to paste into the **OCI Container
Instance UI** under "Environmental variables" (Key/Value pairs). Click
"+ Another variable" for each row.

> **⚠️ Two security notes before you start**
>
> 1. **Don't commit any secret values into this file.** Where you see
>    `<from your local .env>` or `<your-...>`, fill it in **only in the OCI UI**,
>    not here.
> 2. **Rotate the Neon password and Upstash token** after deploy. They were
>    exposed in chat history. Neon dashboard → reset role password;
>    Upstash console → regenerate token.

---

## Important before you create the instance

- **You need TWO container instances**, not one — `backend` and `worker` — both
  configured with the **same env vars below**. Without the worker, every analysis
  job queues forever and YouTube pipeline + ML never run.
- **The v2 model must be inside the image** (the "bake" path). Confirm with
  `docker run --rm <your-image> ls /app/media/models/` — you should see
  `frc_robot_detector_v2.pt`. `.dockerignore` already whitelists it; this only
  matters if your build step actually included it.
- **Container egress** must reach Neon (us-east-1) and Upstash. Verify the
  VCN security list allows outbound HTTPS (443) and TLS Redis (6379).

---

## 1. Connection / infra — REQUIRED

| Key | Value |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://<neon-user>:<neon-password>@<neon-host>/<db>?sslmode=require` |
| `REDIS_URL` | `rediss://default:<upstash-token>@<upstash-host>:6379` |
| `CORS_ALLOW_ORIGINS` | `https://scouting-app-iryg.vercel.app` |
| `APP_ENV` | `production` |
| `CLOUD_PROVIDER` | `oci` |
| `STRICT_STARTUP_ENV_VALIDATION` | `true` |
| `ENFORCE_ADMIN_AUTH_FOR_WRITES` | `true` |
| `UVICORN_WORKERS` | `2` |

### Notes on `DATABASE_URL`

- **Must use `postgresql+psycopg://`** (not plain `postgresql://`). The codebase
  uses psycopg3; the SQLAlchemy URL prefix tells it which driver.
- Use the **`-pooler.`** Neon host (you already have that).
- Keep `sslmode=require`. **Drop `&channel_binding=require`** if it was in the
  raw connection string — psycopg3 + Neon poolers sometimes choke on it.

### Notes on `REDIS_URL`

- The `rediss://` scheme (two `s`) is correct for TLS.
- This is the **native Redis protocol**, not Upstash's REST API. The REST URL
  and token Upstash gives you (`UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`)
  are **not used** by this codebase — leave them out.

---

## 2. Admin auth secrets — REQUIRED (copy from your local `.env`)

| Key | Value |
|---|---|
| `ADMIN_API_KEY` | `<from your local .env line 9>` |
| `ADMIN_API_HEADER` | `X-Admin-Key` |
| `ADMIN_SESSION_TOKEN_SECRET` | `<from your local .env line 11>` |
| `ADMIN_SESSION_TTL_SEC` | `7200` |

If the values here don't match what the frontend sends, every admin-gated write
returns 401.

---

## 3. External APIs

| Key | Value |
|---|---|
| `TBA_AUTH_KEY` | `<from your local .env>` |
| `FIRST_FRC_API_BASE_URL` | `https://frc-api.firstinspires.org/v3.0` |
| `FIRST_FRC_API_USERNAME` | `<from your local .env if set>` |
| `FIRST_FRC_API_AUTH_KEY` | `<from your local .env if set>` |
| `STATBOTICS_BASE_URL` | `https://api.statbotics.io/v3` |

---

## 4. ML — the knobs that make ML actually do something

These are the values that turn ML from "trained but dormant" into "actually
influencing predictions". Without them set, ratings/synergy/predictions ship
deterministic-only.

| Key | Value |
|---|---|
| `VIDEO_TRACKING_YOLO_MODEL` | `media/models/frc_robot_detector_v2.pt` |
| `VIDEO_TRACKING_YOLO_MODEL_FALLBACKS` | `yolo11n.pt` |
| `VIDEO_TRACKING_YOLO_MODEL_URL` | *(leave blank — bake path)* |
| `ML_SHADOW_ENABLED` | `true` |
| `ML_SHADOW_ROLLOUT_RATIO` | `1.0` |
| `ML_SHADOW_AUTO_TRAIN_ON_EVENT_BREAKDOWN` | `true` |
| `ML_SHADOW_AUTO_TRAIN_LIMIT_EVENTS` | `250` |
| `ML_SHADOW_AUTO_TRAIN_ACTIVATE` | `true` |
| `ML_SHADOW_AUTO_TRAIN_RECOMPUTE_RATINGS` | `true` |
| `ML_SHADOW_AUTO_TRAIN_CURRENT_SEASON_ONLY` | `true` |
| `ML_MATCH_OUTCOME_BLEND` | `0.20` |
| `ML_TEAM_STRENGTH_BLEND` | `0.20` |
| `ML_SYNERGY_BLEND` | `0.20` |
| `ML_ROLE_BLEND` | `0.20` |

If you ever switch to object-storage delivery for the detector, set
`VIDEO_TRACKING_YOLO_MODEL_URL` to the bucket URL. Leave blank for now.

---

## 5. Automation / scheduler — the production trigger path

These drive the scheduler that auto-enqueues analysis jobs. Without them the
worker boots but never has work to do.

| Key | Value |
|---|---|
| `AUTOMATION_REGIONAL_ENABLED` | `true` |
| `AUTOMATION_REGIONAL_HALFDAY_SCHEDULER_ENABLED` | `true` |
| `AUTOMATION_REGIONAL_HALFDAY_SEASON` | `2026` |
| `AUTOMATION_REGIONAL_HALFDAY_INTERVAL_HOURS` | `12` |
| `AUTOMATION_REGIONAL_HALFDAY_ALL_MATCHES_IN_REGION_EVENTS` | `true` |
| `AUTOMATION_REGIONAL_HALFDAY_INCLUDE_ENDED_TODAY` | `true` |
| `AUTOMATION_REGIONAL_REQUIRE_VIDEO` | `true` |
| `AUTOMATION_REGIONAL_REQUIRE_CALIBRATION` | `true` |
| `AUTOMATION_REGIONAL_RUN_POST_COMPUTE` | `true` |
| `AUTOMATION_REGIONAL_CLONE_EVENT_CALIBRATION` | `true` |
| `AUTOMATION_REGIONAL_INTERVAL_MINUTES` | `720` |
| `LIVE_ANALYSIS_ENABLED` | `true` |
| `LIVE_ANALYSIS_REGIONAL_AUTO_ENABLED` | `true` |
| `LIVE_ANALYSIS_REGIONAL_AUTO_INTERVAL_SEC` | `120` |
| `LIVE_ANALYSIS_REGIONAL_AUTO_MAX_EVENTS` | `16` |
| `INTEL_SNAPSHOT_REFRESH_ENABLED` | `true` |
| `INTEL_SNAPSHOT_REFRESH_INTERVAL_MINUTES` | `2` |

---

## 6. Misc tuning (safe defaults — only set if you want to override)

These all have sane code defaults. Skip unless you have a reason.

| Key | Value | Why you'd set it |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Already the default. `DEBUG` if you need verbose logs during the first deploy. |
| `LOG_FORMAT` | `json` | Switch from `text` if your log aggregator wants structured JSON. |
| `MEDIA_CLEANUP_ENABLED` | `true` | Default true; explicit if you want to be sure. |
| `STORAGE_CLEANUP_ENABLED` | `true` | Same. |

---

## After the instance boots

1. Open the container's logs in the OCI console. You're looking for these lines:

   ```
   startup.model_provisioning {'present': True, ...}
   Background scheduler initialized on startup
   Scheduled regional post-event automation job added (interval: 12 hours)
   ```

   On the **worker** container also look for:
   ```
   worker.model_provisioning {...}
   Starting worker queue=default redis=rediss://...
   ```

2. If you see `RuntimeError: Startup env validation failed: ...`, the message
   names exactly which var is empty or wrong. That guard is **working as
   designed** — fix the var, restart the instance.

3. Once stable, run the day-one ML backfill per
   [PROD_RUNBOOK.md](./PROD_RUNBOOK.md) section 6:

   ```bash
   # From your OCI cloud shell, or via 'oci container-instances container exec'
   PYTHONPATH=. python scripts/survey_shadow_training_data.py --season 2026
   PYTHONPATH=. python scripts/backfill_shadow_models.py --season 2026
   ```

   On OCI Container Instances you can run these via the **"Run command"** action
   in the instance UI, or via the OCI CLI:
   ```
   oci container-instances container-instance retrieve-logs --container-instance-id <id>
   ```

---

## Quick sanity grid (count what you typed)

When you're done, you should have entered roughly:

- 8 in section 1 (connection)
- 4 in section 2 (admin)
- 5 in section 3 (external APIs)
- 14 in section 4 (ML)
- 17 in section 5 (automation)

**≈ 48 total variables.** If you've typed many fewer, you're probably missing a
category. If you typed many more, you might be over-specifying defaults.
