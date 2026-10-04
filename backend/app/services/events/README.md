# Events Service

Gets official event and match data into the system and keeps it fresh.

## Files

**`ingest.py`**
Pulls an event from TBA (teams, schedule, results, score breakdowns, video links) and writes `Event`, `Match`, `MatchTeam`, `EventTeam` and official-truth `TeamMatchFinding` rows. Idempotent: re-running it updates rows in place.

**`pipeline.py`**
`refresh_event()` re-ingests one event, then rebuilds what depends on it: synergy, ratings and, when enabled, the ML shadow models. `train_shadow_models_after_refresh()` is the training step on its own, so batch callers can train once instead of per event.

**`first_events_client.py`**
HTTP client for the FIRST events API, used alongside TBA for event discovery.

**`regional_automation.py`**
The scheduled refresh. Every tick (12 h by default) it picks the season's completed events (in-region only, or all of them with `AUTOMATION_REGIONAL_INCLUDE_ALL_EVENTS`), refreshes each one, then trains the ML shadow models once. Interval gating and a Redis lock keep ticks from overlapping across uvicorn workers.

## Dependencies

- TBA client (`app/tba/client.py`) — primary data source
- Redis — automation lock and last-run bookkeeping
- `ratings`, `ml.synergy`, `ml.shadow` — post-ingest recompute
