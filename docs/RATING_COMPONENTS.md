# Candidate component discovery and bonus RP

The total-state candidate still uses Tier A (points without fouls). These tools
help declare inputs for a new game and inspect a separate flag-only bonus ranking
point model. They do not change the public rating or prediction pipeline. State
activation remains blocked by serving integration, rating impact and the release
rehearsal.

## Discover cached inputs

```sh
cd backend
python scripts/discover_components.py --event 2026example --cache /private/tba
```

The cache is the raw export produced by `fetch_tba_season.py`, stored as
`YEAR/EVENT.json`. Discovery reads one event, prints JSON and makes no network calls
or data/spec writes. It reports numeric fields, combinations of up to three point
fields plus adjustments that close the non-foul total on at least 99% of eligible
matches, ordered Robot1–3 fields, and boolean flag candidates. Missing fields count
as closure failures. DQ matches, unplayed scores and invalid lineups are excluded
and counted. A small sample or an accidental score identity can suggest an incorrect
split; a person must review the proposal and the component-tier accuracy gate must
still pass before a finer model serves.

Robot status strings need an explicit point/category mapping; discovery never
converts them into points. Flag inventory includes non-RP achievements and penalties
to review. For a flag with both earned and unearned observations, numeric fields that
separate them are shown with the largest unearned and smallest earned values. These
are intervals observed in that event, not manual rules. Coopertition, overrides and
multiple criteria can prevent a single separator. Nothing is activated automatically.

## Declared flags

Each component spec can declare `bonus_rps`, with a unique name and explicit boolean
breakdown path. Historical declarations cover cargo/hangar (2022), activation/
sustainability (2023), melody/ensemble (2024), auto/coral/barge (2025), and energized/
supercharged/traversal (2026). Coopertition and subsidiary achievements are not
declared as bonus RPs. No 2027 spec or flags are assumed.

An actual JSON boolean is required: absent, null, numeric and string values stay
unknown. `false` is an observed failure, not a missing result. DQ sides and all
playoff results are excluded from flag observations. Both the database and cached
replays require actual playing times; scheduled times cannot substitute for them.

## Flag-only candidate

`flag-rp-laplace-v1` has one Gaussian logit contribution per team and RP. The alliance
probability is the logistic of the three means. A Bernoulli observation takes one
Newton/Laplace step, retaining the event's full team covariance. Match variance grows
after play and event variance grows on transfer. Unseen teams start from the season's
as-of Beta(1,1) earned rate split over three robots; before any flags that rate is
one half. No end-of-season rate, point count or threshold initializes a forecast.

The fixed development defaults are prior variance 1, match variance 0.01 and event
variance 0.1, all in logit units. They are unfitted and have not earned a release
gate. Probabilities use the posterior mean logit, without integrating its Gaussian
uncertainty. `logit_sd` reports latent uncertainty; it is not a calibrated probability
interval. The model is separate from the total-point adapter and cannot supply a
fabricated climb or auto contribution.

With `RATING_STATE_ADAPTER_ENABLED=true`, the existing private state preview includes
`bonus_rp_preview` for declared flags. It replays earlier qualifying results, then
forecasts only future, unscored quals with six distinct confirmed teams. Hypothetical
forecasts do not mutate its state. The response includes recipe/settings/input hashes,
observation counts and provisional/serving-ineligible labels. It writes no database
state, does not capture prospective RP evidence and does not serve these probabilities
on public routes. The switch defaults false.

## Development check

```sh
python scripts/evaluate_flag_rp.py --season 2024 --cache /private/tba
```

This prints a chronological predict-before-observe check against the as-of season
rate, with Brier, log loss and 90% paired event-bootstrap intervals. It reports the
whole season and newest fifth of events, identified by first actual qualification
time. Same-time forecasts are made before any of those outcomes are observed. The
recipe is fixed before evaluation; the command does not tune hyperparameters. The
CLI only accepts the already examined 2022–2026 seasons. These are development
checks, not fresh blind evidence. Comparison with the live threshold model, parameter
fitting/versioning, prospective RP capture, component-tier evaluation and the release
rehearsal remain required before adoption.

## Compare with the current RP recipe

```sh
python scripts/compare_flag_rp.py --cache /private/tba-all \
  --priors-2025 /private/prod/team_prior_strengths_2025.csv \
  --snapshots /private/prod
```

This separate 2026-only check first reproduces saved `served_2026*.json` RP values
using the live hub/tower model, stored prior-season strengths, existing calibration,
and retrospective event thresholds. The API rounds bonus chances to three decimals;
reproduction must match within 0.00055 with no missing values. Failed or absent parity
evidence stops the command before the outcome comparison.

The actual comparison then replays both fixed recipes on the same earlier history,
including cached nonofficial events. It requires actual playing times and updates
both models only from results whose `post_result_time` is strictly before the current
start. Missing or invalid publication times cannot supply updates. Event thresholds
use that same earlier published history. Current-match, later-match and retrospective thresholds cannot
enter a forecast. The live recipe keeps its existing DQ observation behavior; flag
updates exclude DQ sides and nonboolean flags. Only common, eligible official
qualifying alliance/RP labels are scored.

The comparison calls both models' read-only `forecast()` methods, as for upcoming
matches in the live API and candidate preview. The played-match `predict()` path has
a different covariance calculation in the current model; it is used only for the
separate historical snapshot reproduction.

Updates in this online comparison follow publication order. Production rebuilds can
reorder available history by actual playing time. Snapshot reproduction verifies the
played-history recipe, not historical upcoming probabilities; prospective captures
are still needed to validate the served path end to end.

Reports include per-RP and pooled Brier/log loss, paired 90% event-bootstrap intervals,
first 7/21 playing-time days, newest fifth of official events, first matches at an
event and championship events. Comparators are the calibrated live threshold recipe,
the uncalibrated threshold recipe and the as-of season rate. Source, settings,
prior-strength and forecast hashes identify the run. It writes nothing itself and
makes no network calls; redirect reports outside git.

These are examined-season development results. The production baseline retains its
2026-derived starting means and calibration coefficients; it is not an out-of-sample
fit. Saved historical API values use event-wide thresholds and are reproduction
checks, not prospective evidence. Score correction publication times are unavailable
in the cache. Neither reproduction nor a favorable development comparison permits
activation; prospective capture and the remaining release checks still apply.

## Fit an older-season candidate

```sh
python scripts/fit_flag_rp.py --cache /private/tba --out /private/rp-fits
```

Training opens only 2022 and 2023. A deterministic search of at most 13 parameter
sets fits shared prior/match/event variances against publication-aware, read-only
forecasts. The objective weights seasons and their RP types equally. Bounds are
prior variance 0.1–4, match variance 0.0001–0.1 and event variance 0.001–1. The two
coordinate passes are a finite search, not a claim of a global likelihood optimum.

A shared two-parameter correction then fits log loss with a small penalty toward
the identity:

```text
logit(calibrated) = logit(as-of season rate)
                 + intercept + slope × (logit(raw) − logit(as-of season rate))
```

This learns how strongly to depart from the as-of rate without requiring matching
RP names or a known point threshold in the new game. Intercept is bounded to ±2 and
slope to 0.25–4. It uses only the training forecasts/labels, with no 2026 coefficients
or evaluation-year fitting. Result publication must be strictly before the predicted
start; unknown flags and DQ sides never supply observations.

The candidate is saved before opening 2024. That check requires pooled log-loss
non-regression at the upper 90% event-bootstrap bound, and per-RP mean regressions
no larger than 0.0005 Brier or 0.002 log loss. These are development-screen rules,
not release approval. The output includes candidate, validation and frozen-manifest
files named by their content hashes. Parameters, policy, source/spec hashes and an
implementation hash identify the fit. Files must be outside the repository and an
existing artifact cannot be overwritten with different contents.

Only a successful frozen check can open the later examined seasons:

```sh
python scripts/evaluate_rp_fit.py --frozen /private/rp-fits/HASH.json \
  --season 2025 --cache /private/tba
python scripts/evaluate_rp_fit.py --frozen /private/rp-fits/HASH.json \
  --season 2026 --cache /private/tba-all \
  --priors-2025 /private/prod/team_prior_strengths_2025.csv --snapshots /private/prod
```

This compares the fitted state and calibrated probabilities with the original flag
recipe, and on 2026 with the reproduced live threshold recipe. Hash checks and the
2024 verdict run before evaluation data are read. The candidate stays fixed, including
when a later RP or slice loses. These seasons have already been examined and remain
development evidence; 2027 is still the clean test. The artifact is not imported into
active settings or the private preview. Weekly RP fitting, prospective capture,
serving integration and release validation remain separate work.

### Isolate probability calibration

```sh
python scripts/fit_flag_rp.py --calibration-only --cache /private/tba \
  --out /private/rp-calibration-only
```

This mode fits the same shared correction on 2022–2023 while fixing every state
variance to the original flag model's defaults. It performs no state search. Its
separate `flag-rp-calibration-v1` artifact type rejects changed state settings.
The 2024 screen, limits, frozen-file checks and later-season evaluation command are
the same as above. This isolates the correction from the rejected joint state fit;
it does not imply a successful result. Since 2024 has already informed this follow-up,
it is a reused development screen. Later examined seasons are also development
evidence, and serving remains disabled regardless of those results.

Adding `--slope-only` to this command also fixes the correction intercept at zero.
That simpler `flag-rp-slope-calibration-v1` recipe learns just the departure from
the as-of base rate. Its artifact rejects either changed state settings or a nonzero
intercept. It uses the same training data, slope bounds, regularization and development
limits. Follow-up comparisons on 2024 are development choices, not new blind tests.

### Prospective RP evidence and weekly fits

`RATING_RP_ENABLED` defaults to false. With it and `RATING_PREDICTION_LOG_ENABLED`
enabled, the live ingest job captures a separate RP candidate after refreshing the
served prediction log. This does not change served probabilities or the private
preview. Only declared seasons and official event types 0–5 qualify; 2027 still
requires a component/flag declaration when its rules are known.

`rating_rp_predictions` keeps the first forecast for each calibration version and
match. The database forbids updates and deletes. Capture requires an upcoming,
unscored qualification with six distinct teams and a served log no older than
60 seconds, matching its lineup and scheduled start. It saves the raw and corrected
RP probabilities, served comparator, teams, official event type, timestamps and
settings/data/source hashes. One identity bootstrap and up to two recent weekly
candidates are captured. Changed implementation or component declarations start a
new evidence cohort rather than reuse older raw forecasts.

The external `scripts/fit_rating_calibrations.py --season YEAR` job can also fit
`flag-rp-weekly-v1` candidates. Each RP has its own intercept and slope, starting at
identity every season with the original flag-state settings. Its fit uses saved
bootstrap forecasts whose actual match starts and result storage timestamps are
strictly before Monday 00:00 UTC. Forecasts must predate the actual start, both final
lineups must match, DQ sides are excluded, and labels must be explicit booleans.
Missing or corrected results stored at/after the cutoff wait for a later fit.

A role needs 150 alliance labels, including five of each outcome. Otherwise it
stays at identity; a return to identity that exceeds movement bounds rejects the
fit. Sum Bernoulli log loss uses identity-centred Gaussian prior precision `[4,16]`.
Intercept stays within ±2 and moves at most 0.25 per weekly fit; slope stays within
0.25–4 and changes by at most a factor of 1.5. Independent per-RP fits avoid forcing
rare and common bonus RPs to share a correction. Cutoffs must advance; repeating a
completed cutoff returns its existing version. Frozen seasons cannot capture or fit.

`rating_rp_fit_evidence` freezes the exact labels, their result timestamps, and a
training hash. Combined with saved forecasts, this reconstructs each fit's inputs
even after source results change. It has the same database immutability protection.
Prospective metrics use saved probabilities, never a retrospective rerun. A new fit
has no prospective evidence until it is captured at future matches. Comparison with
the served snapshot may include cached production state; raw versus corrected uses
the same candidate capture. Neither comparison automatically grants release approval,
and the activation API explicitly rejects RP candidates.

For historical development checks, run:

```sh
python scripts/evaluate_rp_weekly.py --season 2024 --cache /private/tba
python scripts/evaluate_rp_weekly.py --season 2026 --cache /private/tba-all \
  --priors-2025 /private/prod/team_prior_strengths_2025.csv --snapshots /private/prod
```

This fixed policy learns only labels published strictly before each Monday, without
parameter search. It reports whole-season, early-season, newest-event and championship
comparisons by RP, with event-bootstrap intervals. Final cached corrections still
lack historical publication times. Already examined 2022–2026 seasons are development
evidence; 2027 remains the clean test. No environment switch, cron or active setting
is enabled by adding this code. Serving integration and full release gates remain open.

## Component representation screen

`scripts/evaluate_component_tiers.py` compares total-only Tier A with an auto/teleop
Tier B and a declared numeric alliance split for Tier C. These research declarations
are separate from the serving registry. The 2026 generic split uses `totalAutoPoints`
and `totalTeleopPoints`; older seasons use `autoPoints` and `teleopPoints`. A signed
residual closes every complete split. Missing values, booleans in numeric fields and
DQ sides are excluded from observations, rather than filled with zero.

This is a fixed-recipe representation screen. All tiers share the same earlier-season
total-state bootstrap, previous-season strength priors and as-of total scoring unit.
Their initial prior, drift and constant-noise variance budgets sum to the total model's
budget. Counting noise applies separately to each component's predicted mean. Starting
component shares use earlier complete observations, with equal shares before any result.
With constant noise and complete alliance observations, the split reduces to the total
filter; tests verify this identity as well as single-component parity with the existing
filter. Upcoming forecasts do not mutate team/event pointers or running statistics.

The screen does not independently fit component hyperparameters, model categorical
robot outcomes, or apply the production win correction. Tier C uses numeric alliance
point fields. The raw win link is fixed for every model. These limits matter: a poor
result rejects this recipe and does not establish that finer components cannot help.

Freeze the protocol before evaluating, with private output directories:

```sh
python scripts/evaluate_component_tiers.py --step freeze --out /private/component-protocols
python scripts/evaluate_component_tiers.py --step evaluate --season 2024 \
  --protocol /private/component-protocols/HASH.json --cache /private/tba \
  --reports /private/phase2 --out /private/component-runs
```

Protocol and implementation hashes are checked before cache reads. Bootstrap fits are
read only from earlier-year files. The 2022 reference uses the pre-existing stand-in
ratios derived from 2026 and cannot count as an earlier-only bootstrap test. All five
seasons were already examined; this work does not restore blind status. The command
rejects 2027 and refuses report output inside the public repository.

Replay updates only when publication time is strictly before the forecast. Monday
checks use only saved forecasts whose outcomes were published strictly before that
cutoff. The likelihood comparison scores the same **alliance totals**, not component
likelihoods of different dimensions. The screen requires 1,500 common matches over
two calendar weeks, lower total-score negative log likelihood with a 90% event-bootstrap
interval excluding zero, and the plan's closure, log-loss, points-error, context-Brier,
coverage and rating-impact guards. Both models use the same as-of Tier A median/p99
rating anchors, preventing candidate re-anchoring from hiding rating changes.

Reports include weekly checks and the first screen-eligible Monday for B vs A, C vs A
and C vs B, plus early, newest-event, championship, playoff and first-match slices.
Those dates are counterfactual eligibility checks, not simulated promotions. Missing
context or impact evidence blocks eligibility. Full per-component MAP, prospective
capture, persisted scale anchors and the phase 7 late-event/live-model checks are still
required before any tier can serve. Historical result-correction timestamps remain
unavailable. This script writes no database or active settings.
