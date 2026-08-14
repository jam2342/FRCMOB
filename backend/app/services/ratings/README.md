# Ratings Service

This is the main team strength rating engine. It aggregates scouting data, video analysis findings, and Statbotics EPA into a comprehensive per-event rating for every team. The output — an `EventTeamRating` record — is what the rest of the system uses when it needs to know how good a team is.

## What It Does

Raw match data doesn't directly tell you how strong a team is. This service processes all available signals — autonomous performance, teleop scoring, climb results, penalties, driver skill, and more — weighs them, normalizes them against the rest of the event field, and produces a 0–100 overall rating with supporting subscores and confidence values.

It also blends in Statbotics EPA as a prior, which helps when a team has limited video data.

## Files

The service is split across several files, each handling a distinct responsibility:

**`model.py`**
The main entry point. `recompute_event_ratings()` orchestrates the full pipeline for a given event — loading data, computing ratings, and writing updated `EventTeamRating` records.

**`data_loader.py`**
Loads all the raw inputs: `TeamMatchFinding` records, throughput metrics, quality signals, and any official backfilled data. Handles deduplication and normalization of the raw inputs before they reach the scoring layer.

**`feature_extraction.py`**
Builds feature vectors from the loaded data. Each team at an event becomes a vector of numeric signals — scoring rates, cycle consistency, climb success rates, penalty counts, and so on.

**`scoring.py`**
The core computation. Takes feature vectors and produces subscores across all rating dimensions. Handles outlier handling (top 10% and bottom 5% drops), percentile normalization, and signal blending.

**`signal_generation.py`**
Generates confidence signals that indicate how much to trust each subscore. Confidence is a function of match count and data consistency.

**`signals.py`**
Defines signal thresholds — the minimum match counts and confidence levels required for signals to be considered weak or strong.

**`snapshots.py`**
Rating time-series helpers. Records an append-only `RatingSnapshot` row per team on each recompute, and computes the trend/momentum data the live rating board renders (sparkline points, delta, direction). Best-effort — a snapshot failure never aborts the recompute.

**`statbotics.py`**
Fetches and integrates EPA data from Statbotics. EPA acts as a prior that anchors ratings for teams with limited video data, and is blended out as more match data accumulates.

**`anti_defense.py`**
Computes anti-defense capability — how well a robot performs under defensive pressure. A separate subscore that feeds into the overall rating.

**`game_context.py`**
Season-specific game rules, scoring logic, and parameters. Updated each season to reflect the current game's scoring structure.

**`output_builder.py`**
Formats the final rating object into the JSON structure expected by `EventTeamRating` and downstream consumers.

**`helpers.py`**
Shared utility functions: percentile computation, safe division, deduplication, data range clamping.

**`constants.py`**
Rating weights, signal thresholds, and model version identifiers. Centralizes the constants used across the rating computation.

## Rating Dimensions

| Dimension | Description |
|---|---|
| Auto | Autonomous period contribution |
| Throughput | Scoring rate consistency across matches |
| Climb | Endgame ladder/cage success probability |
| Anti-Defense | Performance under defensive pressure |
| Consistency | Match-to-match variance |
| Penalties | Foul and tech foul impact |
| Trends | Recent performance trajectory |

## How It Runs

1. `recompute_event_ratings()` is called (either on a schedule or triggered by a new match).
2. `data_loader.py` pulls all relevant findings, throughput, and quality records.
3. `feature_extraction.py` builds per-team feature vectors.
4. `scoring.py` computes subscores and applies outlier handling and normalization.
5. `statbotics.py` fetches EPA and blends it in, weighted by data confidence.
6. `signal_generation.py` produces confidence values for each subscore.
7. `output_builder.py` formats the final object.
8. Updated `EventTeamRating` records are written to the database.

## Signal Thresholds

| Signal Level | Min Matches | Min Confidence |
|---|---|---|
| Weak | 3 | 0.50 |
| Strong | 5 | 0.75 |

Trend coverage requires at least 40% of recent matches to have valid data. Outlier handling drops the top 10% (elite) and bottom 5% (poor) matches when computing averages, to reduce noise from exceptional outlier matches.

## Dependencies

- `clients.statbotics` — EPA data
- `TeamMatchFinding`, `EventTeamRating` ORM models
- `game_context` — season-specific scoring rules
