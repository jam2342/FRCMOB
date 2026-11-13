# Versioned prediction settings and history

The settings and log features ship disabled:

- `RATING_PREDICTION_LOG_ENABLED=false` keeps the current request and ingest paths.
- `RATING_AUTOTUNE=false` prevents fitting and activation. If an active bundle already
  exists, turning this switch off pins that bundle; it does not erase history or
  roll it back. Keep prediction logging enabled while a bundle is active.

No production configuration or cron installation is included in this change.

## Storage and serving

Migration `20261113_0031` adds four tables. A calibration version contains the score
calibration, temperatures, and correction weights together. The active bundle points
at one version, so a request cannot mix layers from different fits. Fit parameters,
recipe, cutoff, source commit and version are immutable at the database level.
Status and evaluation metrics can change. Each activation or rollback increments the
bundle version, which joins the season-run cache fingerprint.

The recipe is named `live-forecast-logistic-v1`: it fits the production state's captured
forecasts. It is **not** the phase 2 research state model, and the research candidate's
measured gains do not transfer automatically. This change leaves team-state settings,
team ratings and priors unchanged. The state MAP candidate pipeline is documented in [RATING_STATE_FITTING.md](RATING_STATE_FITTING.md);
its serving adapter and the phase 6 component/RP work remain follow-ups. Rating impact from these prediction-only layers is zero.

Win chances use raw score means plus expected fouls and the raw margin variance.
Score calibration changes displayed alliance points, excluding fouls. The custom matchup
uses the same layers as upcoming scheduled matches. Bonus threshold models and strength
percentiles remain from the state model; expected win RPs follow the served win chance.

When logging is enabled, schedule requests and the scheduler's existing live-ingest
cycle capture upcoming forecasts. The first capture is kept; the latest changed forecast
before the known start becomes `final`. Identical reads do not rewrite it. Captures include
raw features, the original production probability, and the active bundle version.
Unknown start times, started matches, and complete matches cannot acquire live captures.
The captures describe forecasts made by the system; a scheduler capture does not prove
that a person viewed it.

An actual start or result locks an existing row. PostgreSQL and SQLite triggers refuse
updates or deletes after locking, and always preserve the first capture. Played schedule
rows read the saved forecast; rebuilding or changing settings cannot rewrite it. If a
late actual start invalidates the final capture, the reader uses a valid first capture
or reports that no valid pre-match capture exists. Such a final is excluded from fitting.
A missing row for a played match is reconstructed by replay, immediately locked and
marked `backfilled=true`. Backfills never count as prospective evidence. Season freeze
also locks remaining log rows and preserves the existing archive behavior for 2026.

With logging enabled, Redis permits one rebuild per season across workers. A worker
that misses the lock keeps its prior run; a cold worker returns a retryable 503. Redis
failure fails closed. With both features disabled, existing rebuild behavior remains.

## Weekly job

Run the fit command outside web workers:

```sh
cd backend
APP_ENV=development python scripts/fit_rating_calibrations.py --season 2027
```

On the VM, `docker/fit-rating-calibrations.sh 2027` runs the command in a throwaway
backend container. Install it in the host's **UTC** crontab at `0 9 * * 1`, using the
absolute repository path. The script reads the image's `BUILD_SHA` (or
`APP_COMMIT_SHA`), runs with lower priority, limits BLAS threads and Linux CPU affinity
to one core, and takes a distributed fit lock. It writes only rating tables; match and
prediction-log data are read-only inputs. A dedicated database job role can enforce
those permissions. A repeated run for the same Monday returns the stored fit.

The cutoff is Monday 00:00 UTC. Only locked, non-backfilled forecasts with valid
pre-start captures and earlier actual playing times are fitted. TBA week labels are
never used. At least 150 decided captured matches are required. The baseline is the
captured production forecast, not a later reconstruction of that forecast.

Hard bounds and weekly step limits reject a broken fit rather than clipping it silently.
Temperatures stay in [0.25, 4] with at most a factor 1.5 step. Score coefficients and
correction weights have fixed bounds and maximum additive steps of 0.15 and 0.25;
blend stays in [0, 1] with a 0.25 step limit. A last-two-weeks in-sample Brier regression
over 0.001 is a fit sanity failure, not promotion evidence.

Versions are evaluated only on matches and captures after both their fit cutoff and
creation time. The gate joins exact common match keys and bootstraps whole events.
It requires 1,500 common decided matches across at least two UTC weeks and two events,
a Brier gain of at least 0.0015 with its 90% interval excluding zero, log-loss regression
at most 0.002, points MAE regression at most 1%, and 80% coverage in [76%, 84%]. First-match
and playoff Brier must be within 0.003; late-context Brier's upper difference bound must
be at most zero. First-match score bias must be within ±3%. Missing required slices fail
closed. 2026 and earlier cannot be promoted: those seasons are development evidence.

The initial implementation deliberately makes **every version** earn prospective evidence,
including a weekly refit within the same recipe. This is more conservative than the plan's
automatic refits within a validated recipe. The weekly job tries the oldest unactivated
candidate first. It does not choose among newly fitted variants using the evaluation week.
Removing this extra restriction requires the full rehearsal.

The frozen production baseline remains a safety comparator. Over the last 14 days,
with at least 150 common matches, a confidently worse active fit rolls back to production.
This does not promote a challenger. Freezing promotions leaves this safety check running;
freezing an entire season stops the job.

## Admin controls

All endpoints require the existing admin credential and are private to caches:

- `GET /admin/rating/calibrations?season=2027`: versions, metrics, decisions and active pointer.
- `POST /admin/rating/calibrations/{id}/activate`: recompute the evidence gate, then activate.
- `POST /admin/rating/freeze` with `{"season":2027,"frozen":true}`: stop fits and promotions.
- `POST /admin/rating/rollback` with `{"season":2027}`: restore the prior bundle, or production.

Admin activation cannot bypass the bounds or evidence gate. No release gate has been
waived, and no candidate is approved by adding this machinery. Keep autotune disabled
until capture-only operation and the full rehearsal have been checked.
