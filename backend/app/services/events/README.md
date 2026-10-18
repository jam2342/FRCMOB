# Events Service

Gets official event and match data into the system and keeps it fresh.

## Files

**`ingest.py`**
Pulls an event from TBA (teams, schedule, results, score breakdowns, video links) and writes `Event`, `Match`, `MatchTeam`, `EventTeam` and official-truth `TeamMatchFinding` rows. Idempotent: re-running it updates rows in place.

**`pipeline.py`**
`refresh_event()` re-ingests one event, then rebuilds its ratings (`post_compute_event()`).

**`first_events_client.py`**
HTTP client for the FIRST events API, used alongside TBA for event discovery.

**`regional_automation.py`**
The scheduled refresh. Every tick (12 h by default) it picks the season's completed events (in-region only, or all of them with `AUTOMATION_REGIONAL_INCLUDE_ALL_EVENTS`), refreshes each one, then rebuilds each one's ratings. Interval gating and a Redis lock keep ticks from overlapping across uvicorn workers.

## Dependencies

- TBA client (`app/tba/client.py`) — primary data source
- Redis — automation lock and last-run bookkeeping
- `ratings` — post-ingest recompute
