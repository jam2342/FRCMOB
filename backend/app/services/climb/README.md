# Climb Service

Climb truth comes from the TBA score breakdown, not inference. Event ingest (`services/events/ingest.py`) writes each robot's official climb result into its `TeamMatchFinding` as `climb_success_prob` (full success `1.0`, partial `0.35`, none `0.0`).

## Files

**`coverage.py`**
`climb_signal_coverage()` reports, for the most recent events, how many teams have any climb signal and how many of those are official. The admin ops dashboard (`GET /maintenance/ops/dashboard`) shows it.

## History

- The video-vs-official climb audit was removed on 2026-10-05: the broadcast pipeline that produced video climb predictions was retired on 2026-09-28.
- The scheduled official-climb backfill was removed on 2026-10-14. It predated ingest writing climbs itself, and by then it re-downloaded the same six events every three hours and rewrote 1,662 unchanged rows each time. Rows it inserted earlier keep the source `tba_score_breakdown_backfill`, which the official-data readers still accept.
