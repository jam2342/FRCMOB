# State fitting candidate

`RATING_STATE_FIT_ENABLED=false` is a separate, off-by-default switch. The new
`state-map-total-v1` recipe learns state uncertainty, drift, counting noise and
season-start priors from actual alliance scores. It stores the fitted state and
three calibration layers together in `rating_calibrations`.

This is a candidate pipeline. It does not replace `SeasonTeamModel`, alter team
ratings, or serve candidate probabilities. Admin activation explicitly rejects
state candidates until serving component integration, the rating-impact gate and
full rehearsal exist. The late-event accuracy problem remains a release blocker.

## Bootstrap

Export a private artifact from earlier-season research fits and feature tables:

```sh
cd backend
python scripts/build_rating_state_bootstrap.py --season 2027 \
  --reports /private/reports/phase2 --win-tables /private/reports/phase3/tables \
  --out /private/state-bootstraps
```

The output filename is its content hash. Keep this artifact outside git; it contains
fitted parameters. State priors use earlier-season log means and widths floored at
0.3. Counting-noise priors use earlier phase 2 bundle fits. Score calibration,
temperatures and correction weights start from earlier feature-table fits, beginning
with 2023; the known 2022 chain is not used to initialize the win layers. Only source
seasons before the target are selected. Historical runs are development evidence;
2027 remains the clean test.

On first import the job snapshots the previous season's actual results before
January 1 of the target year. It computes the previous season's average strengths
with its own fitted model and match/event drift set to `1e-6`, and records the prior
scoring unit and source hash. Missing previous-season data fails closed. The bootstrap
and prior context become an immutable calibration version. Re-importing identical
content is idempotent; a changed context creates a new version.

## Weekly fit

With state fitting enabled, use the existing external job:

```sh
python scripts/fit_rating_calibrations.py --season 2027 \
  --state-bootstrap /private/state-bootstraps/HASH.json
```

The bootstrap argument is needed only to import or refresh it; later runs use the
stored version. The Docker wrapper forwards additional arguments. The private file
must be available inside that job container through an operator-configured read-only
mount. No production switch, mount, or cron installation is included here.

The command can fit state candidates while `RATING_AUTOTUNE=false`; live calibration
activation stays pinned. State capture additionally requires
`RATING_PREDICTION_LOG_ENABLED=true`. Fitting never runs in a web request.

Only complete scored matches with six distinct teams and an actual playing time
before Monday 00:00 UTC enter a fit. Scheduled times and TBA week labels cannot prove
eligibility. Non-foul totals are modeled; DQ sides or missing totals are excluded from
state observations. Exclusion counts are recorded. Current-season rookie flags,
training results and the frozen prior context receive an input hash.

MAP optimization uses the research likelihood with counting noise. It warm-starts
from an earlier accepted candidate, stays within four times the bootstrap and a
factor 1.5 weekly step, and freezes beta/rookie fitting after 28 playing-time days.
Score calibration and logistic fits minimize their regularized objectives inside the
existing hard bounds and weekly limits. These are bounded fits, not unconstrained
fits clipped afterward. Nonconvergence, invalid bounds or a last-two-week in-sample
Brier regression over 0.001 reject the version. The optimizer has a 3,000-second wall
time budget checked between replays, within the one-hour job lease.

The database season lock is released during optimization. Before publishing, the job
rechecks the freeze flag, predecessor, bootstrap and input hash. A concurrent freeze,
changed earlier result or superseding fit prevents publication. Fit cutoffs must
advance: a later fit cannot initialize an earlier cutoff. Retries at an already
recorded cutoff return the stored version.

## Prospective forecasts

The existing scheduler's live-ingest cycle captures state forecasts after capturing
production forecasts. It evaluates the frozen bootstrap and at most the two newest
eligible state candidates. Models replay only actual earlier results, and each future
event gets an isolated forecast copy so hypothetical future matches cannot change
another event's beliefs.

The candidate must have exactly the same result-history fingerprint as the saved
production forecast. Stale runs, backfills, unknown or passed start times, started or
scored matches, invalid lineups, and missing baseline snapshots cannot create valid
comparison evidence. The result fingerprint is cached per immutable production run.

`rating_state_predictions` stores the first saved prospective forecast per version
and match, its raw and calibrated scores, probability, variance, lineups, source
hashes, original production forecast and the forecast actually served. This first-
forecast comparison is distinct from the served history's final pre-match forecast.
Database triggers reject updates and deletes of this evidence.

Scoring reads those frozen forecasts, never a replay informed by later outcomes.
It verifies actual start time, fit creation time, lineups and calibration hash;
invalidated captures are excluded. Reports compare Brier, log loss, score likelihood
and points MAE with paired event-bootstrap intervals against both saved comparators.
These prospective summaries appear in each version's admin metrics. They are not an
activation verdict: rating impact and the full rehearsal remain unverified.

## Development verification

### Total-state preview

`RATING_STATE_ADAPTER_ENABLED=false` separately gates an admin-only, read-only
endpoint:

```text
GET /admin/rating/calibrations/{id}/preview?event_key=2026example
```

It replays actual results available at request time with the selected candidate's
state parameters and frozen bootstrap context. The response uses the app's rating
row schema, includes the as-of time and input/settings hashes, and never writes
ratings, forecasts or activation state. It is current-state inspection, not a
historical prediction or accuracy test. Public rating and forecast routes continue
to use the existing model even when this preview switch is on.

`ratings/data/component_specs.json` explicitly declares Tier A (total without fouls)
for 2022–2026. An undeclared season fails closed; 2027 must be declared after its
scoring inputs are known. The spec parser supports explicit alliance or ordered
robot point sources for future split models and retains an `other` residual. Missing,
boolean and nonfinite values stay unknown; negative residuals remain visible as
closure errors. The state adapter itself only accepts Tier A.

The adapter retains covariance when summing alliance uncertainty and isolates
hypothetical forecasts from the replay state. Team rows expose total points and
latent contribution uncertainty, with auto, teleop, climb, driver and ranking-point
estimates unavailable. No alliance score calibration is allocated to individual
teams. Points stay hidden until 24 eligible current-season alliance scores exist;
bootstrap-only estimates remain provisional. Ratings use the same declared season
anchors as the live model, never candidate-specific anchors. Without anchors,
0–100 ratings and ranges stay unavailable. This preview does not complete serving
integration, the rating-impact comparison or the release rehearsal.

### Cached state-fit check

A repeatable scratch-database command is available for cached 2023/2024 prefixes:

```sh
python scripts/rehearse_rating_state_fit.py --season 2024 --cache /private/tba \
  --bootstrap /private/state-bootstraps/HASH.json --matches 300 \
  --out /private/state-rehearsal
```

It uses in-memory SQLite, reads the preceding cached season, runs the actual state-fit
service and writes a content-addressed report with source hashes. It cannot run 2025,
2026 or 2027, and makes no blind-validation claim. This checks integration and numerical
behavior; it is not the multi-season release rehearsal or evidence of improved accuracy.
