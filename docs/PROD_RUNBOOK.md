# Production runbook — FRC analysis pipeline

Quick reference for the first week (or any time) after deploying a new model.
Most of this is "run this query, look for this value, panic if not." If you
have to debug the pipeline blind you've lost — these queries make every
failure mode visible.

## The 30-second smoke test (run after every deploy)

After the new image is up, trigger one analysis and check the run completed
with the FRC model:

```bash
# Replace with your real DB connection (production DATABASE_URL)
psql "$DATABASE_URL" -c "
  select
    id, match_key, status,
    (summary->'tracking_backend_meta'->>'model_degraded')::bool as degraded,
    summary->'tracking_backend_meta'->>'model_source' as model
  from analysis_runs
  order by id desc limit 5;
"
```

**Pass criteria:**
- `status = completed`
- `degraded = false` (or null — see observability gap below)
- `model` ends with `frc_robot_detector_v2.pt` (when populated)

If `degraded = true` on a fresh deploy → the v2 weights aren't reaching the
worker. Check the worker startup log line `worker.model_provisioning {...}`
to see whether it found the file or fell back.

---

## Daily health (run once a day for the first week)

### 1. Are runs completing?
```sql
select status, count(*)
from analysis_runs
where created_at > now() - interval '24 hours'
group by status
order by count(*) desc;
```
**Healthy:** mostly `completed`, a small tail of `failed`/`rejected_low_quality`.
**Sick:** lots of `queued`/`running` not progressing → worker stuck or down.

### 2. Are findings being written?
```sql
select count(*) as findings_24h,
       count(distinct match_key) as matches_24h,
       count(distinct team_key) as teams_24h
from team_match_findings
where created_at > now() - interval '24 hours';
```
Findings/match ≈ 6 (3 red + 3 blue) is the floor for well-completed matches.

### 3. The single most important number — % degraded
```sql
select
  round(100.0 * count(*) filter (
    where (summary->'tracking_backend_meta'->>'model_degraded')::bool = true
  ) / nullif(count(*),0), 2) as pct_degraded
from team_match_findings
where created_at > now() - interval '24 hours';
```
**Target: ~0%.** Anything > 5% means the FRC model isn't loading reliably for
some runs. Investigate the worker log for `DEGRADED ANALYSIS match=...`.

### 4. Are metrics non-zero (the real "is ML working" check)?
```sql
select
  count(*) as findings,
  count(*) filter (where coalesce(fuel_scoring_rate,0) <> 0
                      or coalesce(cycle_time_sec,0) <> 0
                      or coalesce(auto_contribution,0) <> 0
                      or coalesce(defensive_engagement_sec,0) <> 0
  ) as with_signal,
  avg(coalesce(fuel_scoring_rate,0))::numeric(6,3) as avg_fuel,
  avg(coalesce(cycle_time_sec,0))::numeric(6,1) as avg_cycle
from team_match_findings
where created_at > now() - interval '24 hours';
```
**Healthy:** `with_signal / findings > 0.7`. If most findings are all-zero,
the model is loading but not detecting anything — likely a video/calibration
issue, not a model issue.

### 5. Quality score distribution
```sql
select
  width_bucket(
    (summary->'analysis_context'->>'overall_quality_score')::float, 0, 1, 10
  ) as bucket,
  count(*)
from team_match_findings
where created_at > now() - interval '24 hours'
group by bucket order by bucket;
```
v2 on our test match scored 0.82. Production should center around 0.6–0.85.
Mass below 0.4 = something systematically wrong.

---

## Queue / worker health

```bash
# Run from a host that can reach REDIS_URL
python3 -c "
import os, redis
from rq import Queue
from rq.registry import FailedJobRegistry, StartedJobRegistry, ScheduledJobRegistry, DeferredJobRegistry
c = redis.from_url(os.environ['REDIS_URL'])
q = Queue('default', connection=c)
print('queued   :', q.count)
print('started  :', len(StartedJobRegistry(queue=q).get_job_ids()))
print('scheduled:', len(ScheduledJobRegistry(queue=q).get_job_ids()))
print('deferred :', len(DeferredJobRegistry(queue=q).get_job_ids()))
print('FAILED   :', len(FailedJobRegistry(queue=q).get_job_ids()))
"
```
**Healthy:** failed << total, queued doesn't grow unbounded.
**Worker dead:** queued grows continuously while started stays 0.

---

## ML actually contributing (the blends)

The whole point of the .env fix was non-zero ML blends. Verify in production:

```bash
# In the backend container
python3 -c "
from app.core.config import settings as s
print('shadow_enabled  :', s.ml_shadow_enabled,        '(should be True)')
print('rollout         :', s.ml_shadow_rollout_ratio,  '(should be 1.0)')
print('match_outcome   :', s.ml_match_outcome_blend,   '(should be 0.20)')
print('team_strength   :', s.ml_team_strength_blend,   '(should be 0.20)')
print('synergy         :', s.ml_synergy_blend,         '(should be 0.20)')
print('role            :', s.ml_role_blend,            '(should be 0.20)')
"
```
If any blend is 0.0 in prod, the deploy env isn't picking up `.env` — the ML
will silently contribute nothing (the exact original failure mode).

---

### 6. Match-outcome ML actually shipping predictions

Local verification (with realistic seeded data) confirmed the **full ML chain
ships ML-blended predictions through `/events/{key}/schedule-with-synergy`** —
each match's `prediction` block populates `red_win_prob_ml`, `prediction_blend`,
`model_version`, and `source_label: blended_det_ml_v1`.

**If the DB already has accumulated season data (typical after a deploy mid-
or post-season), light up ML predictions immediately with a one-shot backfill
— don't wait for the per-event scheduler ramp:**

```bash
# 1. Pre-flight: how much trainable data is actually in the DB right now?
PYTHONPATH=. python scripts/survey_shadow_training_data.py --season 2026
# Look for: "team_strength_pool_clears_threshold": true AND
#           "match_outcome_pool_clears_threshold": true
# (the trainer's hardcoded 20-row-per-model minimum)

# 2. Dry-run: what events would the backfill touch?
PYTHONPATH=. python scripts/backfill_shadow_models.py --season 2026 --dry-run

# 3. Run it. Idempotent (replace_predictions=True). Trains the shadow models on
#    the cumulative season pool, activates them, materializes ml_shadow_predictions
#    for every event with findings.
PYTHONPATH=. python scripts/backfill_shadow_models.py --season 2026
```

**Note about the per-event ramp without a backfill:** the trainer requires
≥ 20 labeled snapshot rows per model. One fresh event with ~12 teams produces
~12 team-strength + ~10-15 match-outcome rows — below threshold. Without a
backfill, the first 1-2 events ship deterministic-only predictions; by event
~3 the cumulative `limit_events` pool clears it and ML lights up automatically.
The backfill skips all that for already-accumulated data.

Check this once real events have run through the scheduler:

```sql
-- Are shadow match-outcome predictions being materialized?
select model_key, count(*), max(created_at) as latest
from ml_shadow_predictions
group by model_key order by latest desc;
```
**Healthy:** rows growing, latest within the last day for active events.
**Dormant:** zero rows after a real event has completed via the regional
automation scheduler → `auto_train_shadow_models_for_event_breakdown` isn't
being triggered. Check the worker logs and `materialize_shadow_predictions_for_event`.

Also check a real match's prediction payload:
```bash
curl -sf -H "X-Admin-Key: $ADMIN_KEY" \
  "$API/events/{event_key}/schedule-with-synergy" \
  | jq '.matches[0].prediction'
```
Look for `red_win_prob_ml` to be **non-null** and `prediction_blend > 0`. If
both are null/0, ML isn't influencing predictions (rest of the chain still works).

---

## Tripwires (set up alerts on these)

| Signal | Threshold | What it means |
|---|---|---|
| `pct_degraded` (query 3) | > 5% | FRC model not loading for some runs |
| `with_signal/findings` (query 4) | < 0.7 | Pipeline producing empty findings |
| RQ `FailedJobRegistry` size | > 50 | Jobs systematically failing |
| RQ `queued` size | growing unbounded | Worker dead or saturated |
| Any blend knob in prod | == 0.0 | `.env` not loaded; ML is silently off |
| Holdout mAP@0.5 monthly | < 0.71 | Model has drifted / regression |

---

## Observability gaps to be aware of

- **`tracking_backend_meta.model_source`** is sometimes blank on persisted
  findings (when a motion-fallback pass blends with the YOLO pass). Trust
  the run-level `model_degraded` flag instead; that's reliable.
- The scheduler runs in-process with `UVICORN_WORKERS`. Each worker has its
  own scheduler instance, deduped by a Redis distributed lock. If the lock
  itself fails (Redis down), you can briefly get duplicate runs — harmless
  but noisy.

---

## When to roll back to v1

Rollback is instant: change `VIDEO_TRACKING_YOLO_MODEL` in the deploy env from
`media/models/frc_robot_detector_v2.pt` to `media/models/frc_robot_detector_v1.pt`
and restart the worker container. Both files are baked into the image; no
rebuild needed.

Rollback if:
- `pct_degraded` jumps above 10% and v1 doesn't show the same
- Metrics suddenly collapse to ~zero on multiple matches
- Holdout mAP regresses materially (see `verify_holdout.py`)

---

## Monthly: re-validate v2 hasn't drifted

Run `backend/scripts/verify_holdout.py` against the locked Einstein holdout
once a month. The number should not move. If it does, something changed about
the model or its environment that you didn't intend.
