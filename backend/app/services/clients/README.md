# Clients Service

This service wraps the two main external APIs the system depends on: The Blue Alliance (TBA) and Statbotics. Rather than scattering raw HTTP calls across the codebase, all outbound API communication goes through here.

Both clients are built to be resilient — they handle failures gracefully using circuit breakers and stale-cache fallbacks, so a temporary API outage doesn't cascade into broken analysis.

## What It Does

- Provides a unified, async interface for fetching team, match, and event data from TBA and Statbotics.
- Protects the system from flaky upstream APIs with circuit breakers and stale cache serving.
- Reduces redundant network calls with a local LRU cache layer.

## Files

**`statbotics.py`**
An async HTTP client for the Statbotics API. Supports fetching team EPA data by team, event, and year.

Key endpoints wrapped:
- `get_team()` — team profile and career EPA
- `get_team_event()` — team performance at a specific event
- `get_event()` — event-level summary
- `get_team_year()` — team EPA for a given season
- `get_team_events()` — filtered EPA data across events
- `get_all_teams()` — paginated full team list

**`tba.py`**
A thin shim that re-exports TBA client functions from `app.tba.client`. TBA access is handled in a separate module and surfaced here for consistency — anything that needs TBA data imports from this file.

## Resilience Patterns

### Circuit Breaker

The Statbotics client tracks 5xx failures per endpoint. After a configurable number of consecutive failures (default: 4), the circuit opens and further calls are short-circuited — the cached value is returned instead of making a network request. After a 90-second cooldown, the circuit closes and requests resume normally.

4xx errors (client errors) do not trip the circuit breaker — they fail immediately and aren't retried, since these indicate a problem with the request itself rather than the server.

### Caching

Responses are cached in memory with a two-tier TTL:

| Tier | TTL | When Used |
|---|---|---|
| Fresh | 5 min (default) | Normal successful responses |
| Stale | 15 min (default) | Fallback when the circuit is open or the request fails |

The cache holds up to 3,000 entries and uses LRU eviction when full. Redis is also used to persist circuit breaker state across worker restarts.

## Dependencies

- `httpx` — async HTTP client
- `Redis` — circuit breaker state persistence and cache layer
- `app.tba.client` — underlying TBA implementation
