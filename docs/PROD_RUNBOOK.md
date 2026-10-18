# Production runbook

> Where the backend runs, how to redeploy it, and how migrations are applied:
> [BACKEND_HOST.md](BACKEND_HOST.md). Environment reference: [OCI_ENV_VARS.md](OCI_ENV_VARS.md).

Production is one backend container (API + in-process scheduler) with Postgres and
Redis beside it. Match data comes from official sources (TBA, FRC Events, Statbotics).
Team-level positional data comes from the on-device recorder. The server-side
broadcast-video pipeline was retired on 2026-09-28: broadcast footage could not
identify individual robots reliably (see `CLAUDE.md`, "Video Step 2").

## Smoke test after every deploy

```bash
curl -s https://scouting-app-iryg.vercel.app/api/health/deep
```

Healthy: `"ok": true`, `"database": true`, `"redis": true`, no `env_validation.errors`.
The uptime workflow (`.github/workflows/uptime.yml`) runs the same check every two hours.

Then open the live site: Home should show today's events and Team Center a rating.

## Official data freshness

The regional automation tick (every 12 h) re-ingests each completed event of the
season, then recomputes its ratings.

```bash
curl -s https://scouting-app-iryg.vercel.app/api/automation/regional/season/2026/status
```

`last_run_at` should be under ~14 h old; `last_result.failed_event_count` should be 0
or close to it. The ops smoke check logs a warning when the tick goes stale.

## On-device session acceptance and promotion

Phone recordings sync to `POST /tracks/on-device-session` as `provisional`. The default
acceptance threshold is **0.80** (`ON_DEVICE_SYNC_MIN_QUALITY_SCORE`): it admits a v2
session only when calibration, pose stability, identity and timing evidence are
collectively strong. A static handheld pose scores below the gate even with perfect
manual identity. Legacy or unrecognized payload schemas always score `0`, stay
ingestible, and need `force=true` in the admin review endpoint.

The PWA converts capture timestamps to match-elapsed seconds and stores
`capture_to_match_offset_sec`. Review it alongside the shift-order source (the client's
value, else the official score breakdown: the alliance with more auto fuel sits out
shift 1) before accepting.

What accepting a session does:

- its tracks appear in the public Team Center heatmap and "Attack vs Defense" view;
- auto-scout can draft scouting forms from it (drafts still need human approval);
- it does **not** reach ratings (they load only official-truth findings, plus legacy
  broadcast rows that are all flagged ineligible);
- it does **not** reach training: nothing trains on phone recordings (the auto-scout
  training export was removed on 2026-10-05), and every session is still marked
  `training_eligible=false` with the blocker `real_phone_ground_truth_validation_pending`.

Do not lift that quarantine based on a desktop or emulator benchmark. First validate
several physical phones over complete matches against reviewed ground truth.
Recommended gates: at least 30 team-match samples across two phone performance tiers,
median field-position error at most 0.75 m, p90 at most 1.5 m, identity precision at
least 98%, match-clock alignment error at most 1.0 s, at least 2 sustained inference fps,
and no more than 20% inference-time drift over a match. Enable training before ratings;
rating use needs a separate evaluation showing no regression on held-out match and team
metrics.

## Tripwires

| Signal | Threshold | Meaning |
|---|---|---|
| `/health/deep` `ok` | false | API, database, Redis or env validation broken |
| Regional automation `last_run_at` | older than 14 h | Scheduler stalled or locked |
| `/home/ubuntu/backups/backup.log` | no dump in 2 h | Hourly backup broken |
| On-device detector holdout mAP@0.5 | < 0.71 | Detector drifted or regressed |

The scheduler runs inside each uvicorn process; a Redis lock per job keeps one copy
running. If Redis is down the lock fails open and a job can briefly run twice, which is
harmless but noisy.

## Monthly: re-validate the detector

The on-device ONNX model is exported from `frc_robot_detector_v2.pt`. Once a month, on
the training machine (`pip install -r backend/requirements-training.txt`):

```bash
cd backend && PYTHONPATH=. python scripts/verify_holdout.py
```

The number should not move. It is measured on the locked Einstein holdout, which is
never trained or split on.
