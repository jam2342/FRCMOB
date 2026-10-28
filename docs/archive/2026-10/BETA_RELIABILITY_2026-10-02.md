# Beta reliability and ML release audit — October 2, 2026

## Verified changes

Concurrent beta requests now run through a real loopback HTTP server, the real
write middleware and workspace authentication, and PostgreSQL 17.11. Each test
gets its own schema. No production workspace, join code, token, recording, or
scouting note is used.

- Simultaneous picklist saves at the same version produce one successful save
  and one conflict containing the winning document. A deliberate retry with the
  returned version succeeds.
- Concurrent joins using the same display name produce one member and one 409.
  Joins lock the workspace before checking its membership and capacity.
- Simultaneous first pit saves produce one entry and no unique-index error.
  Pit notes and photo mutations use the same workspace lock.
- Pit, picklist and recording writes recheck membership after acquiring that
  lock. A request authenticated before a leader removes the scout receives 401
  before saving, even if it retained a previously loaded member object.
- A committed recording whose response is treated as lost survives multiple
  concurrent retries as one session and one set of tracks. Another scout's copy
  gets its own run. A wrong-workspace submission gets 409. Reviewed-session
  exact replay keeps acceptance; altered replay gets 409.
- Another workspace cannot read or modify private picklists or pit notes.
- Implicit ML inference only selects active registry rows. An environment pin
  does not activate a candidate. Explicit version selection remains available
  for candidate evaluation and shadow prediction materialization.
- Training functions and the training CLI default to `activate=false`.
  Automatic shadow materialization names the just-trained candidate versions,
  so candidates can still be evaluated without becoming live models.
  Tiny synthetic one-epoch runs for all five model families write and reload
  actual candidate artifacts while preserving an existing active release.

The API response shapes and picklist conflict convention are unchanged. Pit
forms retain their existing last-save-wins behavior; this round prevents insert
races and stale authorization, and does not introduce pit-form version merging.
Workspace writes are serialized while their short database transactions run.
This is a tradeoff for consistent membership checks and insert limits.

## Validation

Final full backend run: **615 passed, 2 skipped, 3 warnings** in 15.22 seconds.
The skips are the opt-in Redis multi-instance check and the optional real
Ultralytics detector check; PostgreSQL integration checks ran. Warnings are Starlette's existing httpx TestClient deprecation and
the intentionally malformed/short test JWT key fixture. Backend lint and the
three changed verification/training scripts pass Ruff 0.15.4; `git diff --check`
passes. No frontend runtime code or database schema changed.
CI exposed jsdom storage mocks that did not intercept browser methods and a
recorder test that mistook a progress-step number for loaded team data. The
mocks now target the Storage prototype (or the Node shim), and the recorder
test waits for the loaded match controls. All **489 frontend tests**, lint,
style guards and the production build pass. Release CI also ran the opt-in
Redis check: **616 backend tests passed, 1 optional detector check skipped**.

The new regression tests were also run on a disposable copy of the original
`2a79778` backend: **16 failed, 7 passed**. Failures reproduced both picklist
saves reporting success, duplicate active names, a 500 on concurrent first pit
saves, and all three writes succeeding after removal. The ML checks reproduced
inactive-candidate selection and implicit training activation. This baseline
copy did not modify the working repository.

Local logs: `/tmp/frcmob-beta-final.log`, `/tmp/frcmob-beta-baseline.log`, and
`/tmp/frcmob-beta-models.log` (14 model-release checks passed).
These are scratch evidence, not committed reports or production logs.

Reproduce with Python 3.12, backend/test dependencies, Pillow and PyYAML, and
PostgreSQL server binaries installed:

```sh
cd backend
python scripts/verify_postgres_multiuser.py       # focused suite
python scripts/verify_postgres_multiuser.py tests # full backend suite
ruff check app tests scripts/verify_postgres_multiuser.py scripts/train_ml_shadow_models.py scripts/evaluate_models_pre_event.py
```

The runner creates a new private directory under `/tmp`, initializes its own
PostgreSQL cluster with TCP disabled, and stops/removes it on exit. The child
process inherits only a fixed executable path and locale. App settings are
loaded without dotenv sources or inherited application settings. An optional
`--postgres-bin` points at an installed server binary directory. It does not
start a system service, use Docker, or target an existing database.

## Production ML finding — read-only evidence

The live Archimedes schedule reported `blended_det_ml_v1` and
`match_outcome_torch_20261002_052551`. A read-only registry query found **zero
active models**, and both preferred-version settings were empty. Automatic
training was enabled but automatic activation was false. Live rollout was 1.0;
match-outcome, team-strength, synergy and role blends were all 0.2.

Root cause: `_load_model_registry_row()` fell back to the newest registry row
when no active row existed. Daily inactive candidates therefore influenced live
predictions and ratings despite activation being off. This round removes that
fallback. A schedule regression test uses the same 0.2 blend configuration and
confirms that an inactive candidate produces a deterministic prediction.

Read-only production command:

```sh
ssh -i ~/.ssh/frcmob_oci ubuntu@141.148.171.128 \
  'docker exec scouting-backend python scripts/evaluate_models_pre_event.py --season 2026'
```

Observed comparison on 54 held-out events:

| Pre-event predictor | Matches | Accuracy | Brier | Log loss |
| --- | ---: | ---: | ---: | ---: |
| October 2 match-outcome candidate | 2,220 | 0.5113 | 0.4104 | 2.1317 |
| Deterministic formula | 2,220 | 0.5923 | 0.2425 | 0.6823 |

373 matches lacked prior-event ratings and were excluded. These are prior-event
feature evaluations, not prospective live accuracy measurements. The team
strength candidate (`team_strength_torch_20261002_052547`) had MAE 0.2106 and
Spearman 0.6425 on 990 samples, versus previous-event fuel rate's 0.2171 and
0.6674. Its MAE improved slightly while its ranking correlation declined.
This evidence does not justify activating either candidate.

After deploying the fix, inactive evaluation requires explicit versions:

```sh
python scripts/evaluate_models_pre_event.py --season 2026 \
  --match-outcome-model-version match_outcome_torch_20261002_052551 \
  --team-strength-model-version team_strength_torch_20261002_052547
```

## Approved production rollout

The owner approved publishing, deployment, disabling live ML blending and
rebuilding affected ratings/synergy on October 2. At the end of local validation,
production configuration and data were unchanged. Deployment evidence will be
recorded below after the rollout completes.

1. Review and publish the backend patch through the normal release process.
   No migration is required.
2. Set `ML_SHADOW_ROLLOUT_RATIO=0` and all four `ML_*_BLEND` settings to zero.
   Keep automatic activation false. Offline candidate training may continue.
3. Deploy the backend patch using `docs/BACKEND_HOST.md`. Verify deep health and
   confirm live schedule predictions use the deterministic source with zero blend.
4. Recompute affected ratings and synergy projections with the deterministic
   settings via `recompute_event_ratings()` and `precompute_event_synergy()`.
   Avoid `refresh_event()`, which also re-ingests upstream data.
   Existing stored ratings/projections may already contain the previous ML blend;
   switching live inference alone does not rebuild them.
5. Confirm cold web/native event packs refresh against the resulting public
   data. Continue beta signing and physical-device verification separately.

Physical phone performance, production authenticated beta writes, native push,
store signing, distribution, and invitations remain outside this local round.

## Completed production rollout — October 2

[PR #79](https://github.com/jam2342/Scouting-app/pull/79) merged as
`a695af1a4979fd7873cefd59f095c457efe20f5f`. Both PR CI runs and the merged
commit's CI passed. The Android CI check passed. GitHub deployment 6813989833
records a successful Production frontend deployment of that same commit.

The OCI backend was built from that commit and restarted. SHA-256 checks on
all seven changed runtime source files matched the release. Live settings have
rollout and all four ML blends at zero; automatic activation is false. No
migration was needed. Database/Redis/Caddy containers stayed running.
Rollback protection includes the prior backend image and private environment
and source backups. A 39,190,655-byte PostgreSQL dump was checked with
`pg_restore --list`; this round did not rehearse a full restore.

A full Archimedes canary rebuilt 75 ratings and 282 projections in 24.09 seconds.
Rebuilding its event dependencies and projections with the already refreshed
season aggregates produced exactly the same 282 saved projections (SHA-256
`b9bdd61b14a558316965bd5f1028137886a62489e677b172915b247c186aaaed`)
in 0.87 seconds. The serial batch then refreshed the season strength/prior once,
used the same event-strength, event-pair and projection builders for each event,
and committed/verified each event separately. No upstream event re-ingest or
model training was requested. The batch has a resumable JSON manifest under
`/app/media/maintenance/beta-20261002-rebuild.json` on the VM.

At **17:47:48 UTC**, all **285 stored 2026 events** were complete, with
**10,872 ratings** and **42,368 projections** rebuilt and no failed events.
Final independent database queries found:

- zero ratings with an applied team-strength blend, an ML algorithm suffix, or
  a positive role blend;
- zero projections with an ML source, including their pair breakdowns;
- zero active model registry rows.

The public deep health check returned 200 with API, database, Redis and strict
environment validation green. Archimedes, Einstein and Michigan schedules
returned deterministic predictions, zero ML prediction blend and zero blended
prediction counts. Archimedes has 141 deterministic predictions, no ML model
version and no ML predictions.

Browser checks used the live site: the existing Predictions tab refreshed its
cached data and removed all 141 ML labels; desktop and 390-pixel phone layouts
had no page overflow. A fresh session loaded Team Center and Match Center with
real data and no page errors. Predictions phone cards and Team Center were
visually inspected. Anonymous workspace, picklist and pit requests returned 401.
Native device packs, authenticated production beta writes, physical phone
recording, native push and signing remain separate verification work.

Local scratch evidence: `/tmp/frcmob-beta-final-db-evidence.json`,
`/tmp/frcmob-beta-deploy-build.log`, `/tmp/frcmob-backend-ci-release.log`,
`/tmp/frcmob-frontend-release-tests.log`, and the
`/tmp/frcmob-beta-*.png` browser captures. These are not committed secrets or
production credentials.
