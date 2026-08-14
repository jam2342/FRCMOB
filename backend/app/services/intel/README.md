# Intel Service

This service builds unified intelligence payloads for teams and events. Rather than making callers stitch together data from ratings, Statbotics, and TBA themselves, this service assembles it all into a single structured response.

Think of it as the aggregation layer — anything that needs a complete picture of a team or event for display or decision-making goes through here.

## What It Does

The intel service defines a set of async builders, each responsible for fetching and shaping a specific piece of data (ratings, Statbotics EPA, TBA records, alliance data, etc.). When a payload is requested, all relevant builders run and their outputs are merged into a single response object.

## Files

**`builders.py`**
The core of the service. Defines a registry of async builder functions and the logic to run them together. Each builder fetches a specific data type — ratings, EPA, TBA event data — and returns a structured dict. The registry pattern makes it straightforward to add new data sources without changing the rest of the service.

Two main entry points:
- `build_event_teams_intel_payload()` — returns intel for all teams at a given event, including ratings, EPA, and alliance context.
- `build_team_intel_payload()` — returns historical intel for a single team across events.

**`helpers.py`**
Utility functions used during aggregation — things like normalizing team keys, handling missing data, merging partial results, and formatting alliance groupings.

**`snapshots.py`**
Handles historical intel snapshots — saved point-in-time views of team intel that can be compared across events or referenced later without recomputing.

## How It Runs

1. A caller requests an intel payload (event-level or team-level).
2. The relevant set of async builders is selected.
3. All builders run concurrently.
4. Results are merged into a unified JSON payload.
5. The payload is returned to the caller (API handler, scouting UI, etc.).

The builders are registered at API startup, and the sync wrapper (`asyncio.run`) allows non-async callers to use the service without changes.

## Dependencies

- `ratings.model` — event team strength ratings
- `clients.statbotics` — EPA data from Statbotics
- TBA client — team and event data
- `EventTeamRating` ORM model
