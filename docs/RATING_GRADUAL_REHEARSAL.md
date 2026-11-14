# Gradual weekly state updates

This offline experiment applies smaller parameter steps from the actually active
model. It uses the same private scratch database, frozen first forecasts, clock and
resume machinery as the [weekly rehearsal](RATING_WEEKLY_REHEARSAL.md). It enables
no operational switch and never changes the database's actual active-bundle pointer.

From `backend/`, supply the application on both commands:

```sh
python scripts/rehearse_rating_weekly.py --step freeze --application gradual \
  --out /private/gradual/protocols
python scripts/rehearse_rating_weekly.py --step run --application gradual --season 2024 \
  --protocol /private/gradual/protocols/HASH.json --cache /private/tba \
  --reports /private/phase2 --win-tables /private/phase3/tables --out /private/gradual/runs
```

The default application remains `direct`. Source, policy or environment changes
require a fresh protocol and directory. Old frozen runs require their original code.
The same command resumes; `--max-weeks` produces an explicitly incomplete report.

At Monday 09:00 UTC, the goal fit uses results started and published strictly before
00:00. It needs 150 complete matches and retains the MAP prior, two confirmation
restarts, 400-evaluation pass budget, 3,000-second wall limit, hard bounds, factor-1.5
weekly parameter limits and late-season beta/rookie freeze. Its parent is the virtual
active version, including bootstrap after a rollback. An unapplied candidate cannot
become the starting state or calibration prior.

The predeclared state fractions are 1, 1/2, 1/4, 1/8, 1/16, 1/32 and 1/64, in that
order. Parameters interpolate in logs; frozen parameters remain exactly unchanged.
Each trial must improve or preserve the same regularized likelihood within 1e-9,
and pass median rating movement below one and maximum movement at most five. Trial
models replay only results known by 09:00 and use the same preview anchors. Trial
selection never advances the real model or its anchors and never uses future accuracy.
An unconverged goal is rejected; there is no zero-step fallback.

The first admissible state gets one score/temperature/logistic refit on the cutoff
prefix, with the existing bounds and weekly limits relative to the active layers.
Historical features are recomputed for that chosen state. The recent in-sample
Brier sanity check still rejects deterioration above .001. A layer or sanity failure
rejects the update; it does not search more fractions for a better accuracy result.
Compiled runs verify starting, goal and chosen-state innovations against the reference.

At 10:00 the shared driver rechecks rating movement with newly available results
and updates anchors once. A forecast exactly at publication uses the preceding
version. A refused publication leaves the active version in place; the next fit
starts from that active version. Applied versions store their parent hash, full goal,
chosen fraction, chosen state and corresponding layers immutably in the scratch DB.

The existing baseline rollback rule remains: on sufficient saved recent evidence of
deterioration it restores bootstrap immediately and skips that week's fit. Emergency
restoration can have larger rating movement than a normal update. Reports include a
separate common-anchor impact preview for each rollback; it is not another fitted
candidate or a claim about a deployed public scale.

Reports retain whole-season, early, hindsight and EPA checks. Gradual mode also
requires newest-event upper Brier difference at most zero and first-match upper
difference at most .001, using the probability-only experiment's existing margins.
They record applied fractions, parent checks, goal/step/layer rejections and actual
publication refusals. First-match score bias remains a separate release requirement.
The generic `numerical_rejections` count includes all rejected fit records; the
additional breakdown distinguishes nonconvergence from application/layer failures.

This is one predeclared application policy on examined 2023–2026 development data,
not a recipe-selection simulation. Repeated empty weeks can reuse the same labels.
Missing 2022 bootstrap data, historical correction times, provisional 2023 layers,
the held-out production-network comparison, RP/serving integration and clean 2027
remain limitations or release blockers. Reports cannot approve serving.
