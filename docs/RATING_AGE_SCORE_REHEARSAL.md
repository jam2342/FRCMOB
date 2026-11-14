# First-match score correction by season age

This is an offline score-only study on a completed fixed-prior gradual rehearsal.
It freezes that source's state parameters, win probabilities, nuisance score
coefficients, publications and rollbacks. It does not select or activate a recipe.

The first-qualification intercept has two endpoints: season start and day 14.
The weights interpolate linearly using the already captured `season_frac` feature
(120 days per unit), then hold the later endpoint. Playoffs and all other contexts
retain their source scores exactly. Slopes and alliance-composition terms retain
the source fits. Each endpoint has the original intercept prior width, hard bound
±1.2 and weekly step ±.15. A convex combination preserves those limits at every age.

Bootstrap endpoints average conditional fits from earlier seasons' existing phase 3
tables only. Their nuisance coefficients are the corresponding earlier full fits.
Table years and hashes must agree with the source bootstrap. With no eligible
earlier layer season, 2023 uses provisional zero endpoints. Weekly endpoint fits use
the source's selected state replay, original nuisance coefficients and fixed endpoint
prior. Training fingerprints must match the source. They cannot change state or win
decisions. Source bootstrap restoration also restores the endpoint prior.

```bash
cd backend
APP_ENV=development .venv/bin/python scripts/rehearse_rating_age_score.py \
  --step freeze --sources /private/source-runs --tables /private/phase3/tables \
  --out /private/age-study/protocols

APP_ENV=development .venv/bin/python scripts/rehearse_rating_age_score.py \
  --step run --sources /private/source-runs --tables /private/phase3/tables \
  --protocol /private/age-study/protocols/<sha>.json --season 2026 \
  --cache /private/tba --out /private/age-study/runs
```

Only examined 2023–2026 are accepted. Freeze records source files, earlier tables,
implementation and environment hashes. Source SQLite is opened read-only and all
source hashes are rechecked. Fits and forecast journals are content-addressed;
completed checkpoints resume without refitting. An interrupted fit that has not
reached its checkpoint can be recomputed; existing objects remain immutable.

Study checks apply the existing ±3% first-score bias limit overall and separately
to the first 14 days and afterward. Qualification and first-qualification MAE need
a paired event-cluster 90% upper bound at or below zero against the source. Reports
also show championships, newest events and a fixed source-score top decile. That
last diagnostic does not test each model's own top decile. Win-probability changes
must be exactly zero and nonfirst scores must be unchanged.

Passing this conditional study is insufficient for serving. A full fitted recipe,
independent component MAP, RP/serving integration, the paired held-out production
network check and clean 2027 evidence remain separate requirements. Historical
correction-timestamp and replay-feature limitations still apply.
