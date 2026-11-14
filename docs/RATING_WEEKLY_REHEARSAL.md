# Weekly total-score rehearsal

This runs the total-score candidate through a historical virtual clock. It uses the
existing state-fitting service in a private scratch SQLite database, with saved first
forecasts and resumable weekly checkpoints. It changes no operational switch or active
production bundle. Historical simulation files are not live prospective evidence.

Install the optional research accelerator outside the server image, from `backend/`:

```sh
uv pip install --python .venv/bin/python -r requirements-research.txt
python scripts/rehearse_rating_weekly.py --step freeze --out /private/weekly/protocols
python scripts/rehearse_rating_weekly.py --step run --season 2024 \
  --protocol /private/weekly/protocols/HASH.json --cache /private/tba \
  --reports /private/phase2 --win-tables /private/phase3/tables --out /private/weekly/runs
```

The same run command resumes its saved season. `--max-weeks 2` runs at most two more
weeks and produces an explicitly incomplete report. Omit it to finish the season.
The output directory must be outside the public repository. A per-season file lock
prevents concurrent writers, and changed inputs, policy, implementation or numerical
environment require a new directory. The command never uses `DATABASE_URL`.

## Clock and fitting

Actual starts and valid result-publication times drive replay. A result updates a
model only when published strictly before the next forecast. Deferred results cause a
rebuild in actual playing order, matching the state service's replay order. Upcoming
forecasts clone the event state and pointer map; reading a future matchup cannot move
teams between events or change running statistics.

Eight bounded rewind snapshots avoid replaying the entire season after every delayed
arrival. Snapshots at or after an insertion are discarded; older insertions fall back
to a full rebuild. Changing the tracked version set clears the rewind cache. Tests
compare the resulting state arrays and EPA values exactly against full replay.

Monday fits use results whose actual starts and stored-result timestamps are strictly
before 00:00 UTC. The simulated job starts at 09:00 and publishes at 10:00, with a
one-hour wall-time publication budget. The service keeps its existing optimizer,
400 evaluations per pass and up to two restarts after the initial search, hard bounds,
weekly movement limits, context/weight fits,
late-season freeze of prior coefficients, convergence rejection and recent in-sample
Brier sanity check. Calibration training recomputes historical features under the
refitted prefix state; future evaluation always uses the saved original forecast.

The fixed positive-counting-noise recipe skips the research optimizer's structural
zero trials. Those trials remain available to exploratory callers, but choosing zero
would violate this recipe's existing positive hard bounds and discard a valid bounded fit.

The state service's fitting inputs now exclude results stored at or after its cutoff.
This also prevents a delayed Sunday result arriving on Monday morning from entering a
midnight fit. Ordinary current-state readers retain their existing behavior.

Bootstrap settings and widths use earlier-year fit files. Win/score priors use only
earlier-year feature tables. In 2023 no earlier win table is available, so those layers
start at identity and are provisional. The cache has no earlier-season bootstrap for
2022; this command accepts 2023–2026 and records the missing fifth season as a release
blocker. It rejects 2027. All historical seasons were already examined development data.

## Numerical accelerator

The optional Numba kernel implements the existing total filter's sequential innovation
calculation with the same event transfers, running unit, masks, drift and counting
noise. It uses double precision without fast-math or cross-file compilation caches;
see [Numba compilation options](https://numba.readthedocs.io/en/stable/user/jit.html).
Its starting and fitted errors/variances are checked against the reference implementation
on every real weekly fit. A mismatch stops the run. Normal operational fitting uses
the reference implementation and does not import or require Numba.

## Updates, history and ratings

The virtual policy follows the service's last numerically valid candidate for warm
starts. Applying a fit is separate: it must pass the rating-impact check and publication
budget. An impact refusal keeps the previous active version while retaining the candidate
for counterfactual forecasts. An optimizer/bounds/sanity failure cannot become a candidate.
The actual scratch active-bundle pointer remains pinned; production activation guards
are never bypassed.

Common curve anchors start from the active population's median and p99 after at least
20 teams have eight recorded matches each, then move 5% toward that population each
Monday. A switch compares both models on the same anchors and requires median absolute
rating movement below one and maximum movement at most five. These private virtual
anchors exercise the impact machinery; serving scale integration remains separate.

The baseline rollback check uses only saved active/bootstrap forecasts with outcomes
published by the job time. On at least 150 common matches from the last 14 days, a
positive lower 90% event-bootstrap Brier bound restores the fixed bootstrap immediately
and skips that week's fit. It cannot restore an untested prior version.

Each week's forecast file and checkpoint has a content-addressed filename. Before and
after fitting/transitions, the runner reads and hashes all prior journal files. Existing
calibration parameters are also checked and protected by the actual database migration's
immutable-version trigger. A committed fit is reused after interruption even if its
weekly checkpoint pointer was not yet written. Modified journals or scratch results
block resume instead of being overwritten. Checkpoints, data, fitted parameters and
reports stay outside git.

## Results and limits

Every match saves the fixed bootstrap, virtual active version, latest valid challenger
and EPA forecast. EPA's weekly scale uses earlier published saved forecasts. A separate
whole-season hindsight fit is evaluated only after the full journal is frozen; it is a
diagnostic with deliberately leaked settings, and never replaces a journal forecast.
Failure to converge leaves that comparison unavailable.

Reports compare evolving versus bootstrap/EPA/hindsight, including early weeks,
week four onward, newest events, championships and first matches. They count weekly
updates, refusals and rollbacks, verify history preservation and rating steps, and check
restoration within one cycle. Recipe promotions/demotions stay zero: this rehearsal has
one fixed Tier A recipe and does not simulate challenger-recipe selection.

Incomplete runs cannot pass the full-season check. Even complete runs cannot grant
release approval: clean 2027 evidence, the held-out production-network/late-event check,
RP acceptance and serving integration remain open. Final cached label corrections lack
their original correction-publication timestamps. These reports are development evidence,
not proof of historical served-path parity or a completed phase 7 release rehearsal.
