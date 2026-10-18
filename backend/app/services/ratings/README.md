# Ratings Service

Rates every team at an event on one 0-100 scale for the whole season, from official
results only (TBA score breakdowns). `EventTeamRating` rows are what Team Center,
Compare, Picklist, Alliance Advisor (pick values and the alliance outlook in
`pick_value.py`) and the live board read.

## What the number means (rating_v16)

A team's **expected points per match**: teleop hub + auto hub + its own tower (climb)
points, mapped by

    rating = 100 × (1 − e^(−(points / scale)^shape))

with the season's median robot at 50 and its 99th percentile at 95 (2026: median 39.7
points, p99 254; scale 63.2, shape 0.789). The same output reads the same at every
event. Each rating also stores an 80% range (`details.rating_range_80`).

## How the points are estimated: the team model

TBA counts hub fuel per alliance, so every match says "red's three robots scored this
many between them". `team_model.py` keeps, per team and component, a belief (mean and
variance). Each alliance total moves its three robots in proportion to how unsure we are
about each, a Kalman filter on a sum of three. Within an event the beliefs keep their
full covariance, so this is exactly a ridge regression on the event's alliance totals
pulled toward each team's prior. Robots change: variance grows a little every match and
more at each new event. Tower levels are recorded per robot, so climbs update one robot
at a time. A team with no official matches starts at the season's mean robot.

`team_model_service.season_run()` replays the season's alliance results
(`match_alliance_results`) in playing order once, caches it per process (keyed on the
results), and keeps each played match's prediction from just before it.
`recompute_event_ratings()` reads a team's belief at the event (or as it enters it, from
its earlier events), and `events/win_probability.py` reads the same run, so the rating
and the prediction never disagree.

Per-robot findings (`team_match_findings`) are still written by ingest, which splits
alliance fuel with end-of-event COPR/OPR. The rating no longer uses that split for
points: it compressed differences between robots and, for auto, leaked later matches
into earlier ones.

## Evidence

`scripts/evaluate_rating_replay.py` (read-only, ~2.5 min on the VM) replays the season;
models see a match card without scores before each result (`replay.py`). On the 56
newest 2026 events:

| | v16 team model | v15, leak-free | v15 as replayed |
|---|---|---|---|
| Match winner, Brier | 78.0%, 0.152 | 77.6%, 0.152 | 78.0%, 0.147 (auto leak) |
| Playoffs, Brier | **0.147** | 0.155 | 0.161 |
| Alliance points error | **65.4** | 70.0 | 73.7 |
| Next event, Spearman | **0.79** | — | 0.78 |

Mid-season validation: v16 Brier 0.149 vs v15 0.1525. Details, rejected variants and
next steps: `docs/RATING_ROADMAP.md`.

After a model change, rebuild without re-ingesting:
`PYTHONPATH=. python scripts/rebuild_season_ratings.py --season 2026` (ratings for every
event, about a minute for a season).

## Beyond the rating

The same season run feeds:
- **Win probability** (`events/win_probability.py`): margin / predicted spread through a
  logistic per "matches seen here" bucket and a playoff scale, then the learned
  correction (`win_correction.py`, Phase 7; held-out Brier 0.1503 → 0.1464).
- **Bonus RP chances** (`ranking_points.py`): per alliance, from predicted hub fuel and
  tower points against each event's own thresholds, calibrated.
- **Pick value** (`pick_value.py`, `/events/{key}/pick-values`): each candidate's effect on
  an alliance's chance to beat a strong alliance at the event.
- **Event forecast** (`services/simulation/`, `/events/{key}/forecast`): the rest of the
  event simulated 2,000 times.
- **Early-season priors**: last season's strength (`team_prior_strengths`,
  `scripts/build_prior_strengths.py`) and rookie years set each team's first belief.

Every one was gated in the replay harness; the numbers and what was tried and rejected
are in `docs/RATING_ROADMAP.md`.

## Files

- `team_model.py`: the Kalman/ridge team model (pure numpy), with duds, priors and the
  (off by default) fouls / defense / event-offset terms.
- `team_model_service.py`: the cached season run shared with win probability.
- `model.py`: `recompute_event_ratings()`: beliefs → rating, range, subscores, signals.
- `strength.py`: the rating curve; v15's estimate (kept as the replay baseline).
- `official_points.py`: one official finding → teleop / auto / climb (signals, trends).
- `replay.py`: the replay harness and its v15 / fuel-only baselines.
- `signals.py`: plain-language strengths and risks with the matches behind them.
- `output_builder.py`: the API shape (`expected_points`, `basis`, `subscores`).
- `snapshots.py`: rating history for the live board's sparkline.
- `constants.py`: model version and the per-season baseline (priors, settings, curve).

## Subscores

`throughput` (teleop) and `auto_contribution` sit on their own curves (the season's
median robot = 50); `endgame` is tower points / 30; `consistency` is 100 × (1 − spread /
mean) of the team's official per-robot findings here once it has 3 matches.
`robot_level_0_100` is the scoring rating (teleop + auto). Driver skill, results anchor,
shift productivity, capacity use, defense and penalties can't be told apart from
official results, so they are null.
