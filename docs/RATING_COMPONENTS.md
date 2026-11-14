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
including cached nonofficial events. It requires actual playing times, forecasts all
same-time matches before observing their outcomes, and derives event thresholds from
earlier results only. Current-match, later-match and retrospective thresholds cannot
enter a forecast. The live recipe keeps its existing DQ observation behavior; flag
updates exclude DQ sides and nonboolean flags. Only common, eligible official
qualifying alliance/RP labels are scored.

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
