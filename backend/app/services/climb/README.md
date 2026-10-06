# Climb Service

Backfills official climb results into the database when they become available. Climb truth comes from the TBA score breakdown, not inference.

The video-vs-official climb audit that used to live here was removed on 2026-10-05: the broadcast pipeline that produced video climb predictions was retired on 2026-09-28, so the audit had nothing new to compare.

## Files

**`official_backfill.py`**
Fetches match scorebreakdowns from TBA and writes official climb results into the database.

Climb results are converted to a `climb_success_prob` value:
- Full success → `1.0`
- Partial → `0.35`
- Failed → `0.0`

These are either upserted into existing `TeamMatchFinding` records or created as new ones. The last backfill result is cached in Redis with a 14-day TTL.

## How It Runs

1. Fetch match scorebreakdowns from TBA for the target event.
2. Extract climb outcome per team from the breakdown JSON.
3. Convert to `climb_success_prob` using the success/partial/fail mapping.
4. Upsert into `TeamMatchFinding` records, preserving existing fields.
5. Cache the result for 14 days.

## Dependencies

- TBA client — source of official scorebreakdowns
- `TeamMatchFinding` — the target table for backfill writes
- `EventTeamRating` — used for coverage reporting
- Redis — result caching for backfill
