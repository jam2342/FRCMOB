# Scoring Service

This service parses official TBA scorebreakdowns and extracts structured, per-team scoring data. It's the layer that turns raw JSON from TBA into usable truth data that the rest of the system can validate against.

## What It Does

TBA provides detailed scorebreakdowns for every match — exactly how many auto points each alliance scored, which teams climbed, how many fouls were called, and so on. The structure of that JSON changes every season as the game changes. This service handles that parsing in a season-aware way and produces clean, structured output.

The output from this service feeds into the climb backfill (in the climb service), integrity checks, and any place where official scores are needed as ground truth.

## Files

**`breakdown.py`**
Parses TBA scorebreakdown JSON per season and game. Handles the structural differences between game years — field names, scoring categories, and team attribution all vary. Returns a structured dict with per-team contributions: auto points, teleop points, climb points, and penalty counts.

**`truth.py`**
Uses the parsed breakdown to extract ground truth for specific signals. Where `breakdown.py` is a general parser, `truth.py` is opinionated — it knows what signals the system cares about (climb success, autonomous scoring, etc.) and extracts exactly those in the format expected downstream.

## How It Runs

1. A caller provides a TBA scorebreakdown JSON blob and the game year.
2. `breakdown.py` selects the correct parsing logic for that season.
3. The breakdown is parsed into per-team contribution dicts.
4. `truth.py` extracts the specific signals needed (climb, auto, teleop, penalties).
5. The structured result is returned for use in backfill, validation, or analysis.

## Dependencies

- TBA scorebreakdown JSON — the raw input (fetched via `clients.tba`)
- Game-year-specific parsing logic — updated each season
