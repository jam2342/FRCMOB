# Climb Service

This service handles everything related to endgame climb data — validating that the system's video-derived climb predictions line up with official TBA results, and backfilling official climb scores into the database when they become available.

Climb is a high-stakes scoring element in most FRC games, so accuracy here matters a lot. This service is what keeps the predictions honest.

## What It Does

There are two separate jobs this service handles:

1. **Integrity auditing** — periodically comparing video-analyzed climb predictions against official TBA scorebreakdowns to detect systematic drift.
2. **Official backfill** — pulling official climb results from TBA and writing them into `TeamMatchFinding` records so they can be used as ground truth.

## Files

**`integrity.py`**
Runs the audit. For a given lookback window and sample size, it loads `TeamMatchFinding` records, groups them by event/match/team, and compares the average video-derived `climb_success_prob` against the official value.

Mismatches are flagged by severity:
- **ok** — within normal variance
- **warning** — mismatch ≥ 12%
- **critical** — mismatch ≥ 25%

The audit results help identify when the video analysis model has drifted or when a specific event's data is unreliable.

**`official_backfill.py`**
Fetches match scorebreakdowns from TBA and writes official climb results into the database.

Climb results are converted to a `climb_success_prob` value:
- Full success → `1.0`
- Partial → `0.35`
- Failed → `0.0`

These are either upserted into existing `TeamMatchFinding` records or created as new ones. The last backfill result is cached in Redis with a 14-day TTL.

## How It Runs

**Integrity Audit:**
1. Query recent `TeamMatchFinding` records (within the lookback window, up to the sample limit).
2. Group by `(event, match, team)`.
3. For each group, compare the average official vs. video `climb_success_prob`.
4. Compute the absolute delta and assign a severity level.
5. Return a report of all flagged mismatches above the threshold (default delta: 0.05).

**Official Backfill:**
1. Fetch match scorebreakdowns from TBA for the target event.
2. Extract climb outcome per team from the breakdown JSON.
3. Convert to `climb_success_prob` using the success/partial/fail mapping.
4. Upsert into `TeamMatchFinding` records, preserving existing video-analyzed fields.
5. Cache the result for 14 days.

## Dependencies

- TBA client (`clients.tba`) — source of official scorebreakdowns
- `TeamMatchFinding` — the target table for backfill writes
- `EventTeamRating` — used for coverage reporting
- Redis — result caching for backfill
