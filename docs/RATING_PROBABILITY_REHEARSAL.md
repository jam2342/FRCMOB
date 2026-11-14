# Weekly probabilities with fixed state settings

This historical experiment changes only temperatures and logistic correction weights.
It reads the immutable bootstrap forecasts from a complete
[total-score rehearsal](RATING_WEEKLY_REHEARSAL.md). State hyperparameters, score
calibration, blend and team-rating calculations remain those of that bootstrap.
Team beliefs still learn from arriving match results; this experiment adds no rating
movement relative to the bootstrap. It never uses the production database or enables
an operational switch.

From `backend/`:

```sh
python scripts/rehearse_rating_probability.py --step freeze --out /private/probability/protocols
python scripts/rehearse_rating_probability.py --step run --season 2024 \
  --protocol /private/probability/protocols/HASH.json \
  --source /private/weekly/runs --cache /private/tba --reports /private/phase2 \
  --win-tables /private/phase3/tables --out /private/probability/runs
```

The source directory contains the completed season folders. Source checkpoint,
bootstrap, cache and every journal hash must match. Output stays outside the public
repository. Frozen policy/code/numerical-environment hashes and a per-season file
lock prevent mixed runs. The same command resumes; `--max-weeks 2` produces an
explicitly incomplete report after at most two more weeks.

Every Monday fit uses original saved bootstrap features with a decided, complete,
non-DQ result whose actual start and publication both precede 00:00 UTC. It requires
150 labels. No features are recomputed under fitted state settings. The shared
bounded MAP win-layer fitter preserves existing temperature/weight bounds, weekly
step limits and priors. Score coefficients and blend are copied unchanged.

Jobs run at virtual 09:00 UTC; valid fits publish at 10:00 within a one-hour budget.
A forecast at exactly either timestamp runs first. Failed fits keep the active
version. The recent in-sample Brier check rejects deterioration above .001; it is a
fit sanity check, not evidence of future improvement. The original saved active and
bootstrap predictions drive the existing 14-day, 150-match rollback check using only
published outcomes. A rollback restores bootstrap and skips the week's fit.

Parameters, forecast journals and checkpoints use content-addressed files. Refits
cannot replace earlier forecasts. Before/after checks cover both the new history
and the original source journals. Probability versions have separate identifiers;
the original state parameter hash and raw/score predictions are retained exactly.

Reports compare active with bootstrap and EPA on common matches, with 90% event
bootstrap intervals. Predeclared checks require a whole-season improvement, an
early-period upper Brier difference at most .001, newest-event upper difference at
most zero, first-match upper difference at most .001, no regression versus EPA,
complete history, unchanged state/scores and restoration after any rollback. This
is one fixed weekly recipe; recipe selection is not simulated.

Even passing development checks cannot approve serving. All 2023–2026 data have
already been examined; 2023 has provisional identity layers and the 2022 bootstrap
is unavailable. Cached label corrections lack their original publication times.
The held-out production-network comparison, first-match score calibration, full
recipe/RP/serving integration and clean 2027 evidence remain separate requirements.
