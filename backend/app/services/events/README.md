# Events Service

This service manages the lifecycle of FRC events and matches — ingesting data from TBA and FIRST, tracking match states, queuing analysis jobs, and automating discovery of active events. It's the entry point for getting match data into the system.

## What It Does

When a new event or match is discovered, this service creates the corresponding database records, kicks off any necessary analysis, and keeps everything in sync as match states change (scheduled → live → completed). It also handles automated monitoring of regional events across the USA and Canada, which have their own discovery and tracking workflow.

## Files

**`ingest.py`**
The main ingestion layer. Pulls event and match data from TBA and writes `Event`, `Match`, and `EventTeam` records to the database. Handles deduplication and updates existing records when upstream data changes.

**`pipeline.py`**
Orchestrates analysis job queueing. When a match completes, this file is responsible for enqueuing the appropriate analysis jobs into the RQ (Redis Queue) worker pool. It acts as the bridge between "match is done" and "analysis starts."

**`classifier.py`**
Classifies transition events — for example, detecting zone changes during a match. Uses a neural network to tag events based on tracking and sensor data. The output feeds into downstream analysis.

**`first_events_client.py`**
An HTTP client for the FIRST Robotics events API (separate from TBA). Used to discover and validate event schedules directly from FIRST's own systems.

**`regional_automation.py`**
Automates discovery and live monitoring of regional events across the configured countries (USA and Canada by default). Scans for active in-region events, starts live analysis monitors for them, and handles the event management cycle specific to those competitions.

## How It Runs

**Standard Event Ingestion:**
1. Poll TBA for events matching the configured season and filters.
2. Create or update `Event`, `Match`, and `EventTeam` records.
3. Track match state transitions (scheduled → live → completed).
4. On match completion, call `pipeline.py` to enqueue analysis jobs.

**Analysis Pipeline:**
1. Receive a completed match trigger.
2. Build the job payload (event key, match key, video source).
3. Enqueue into RQ for processing by analysis workers.
4. Workers pick up the job and run the full analysis stack (vision → findings → ratings).

**Regional Automation:**
1. Scan for active regional events (USA + Canada by default) using the FIRST API and TBA.
2. Start live monitors for discovered events.
3. Continuously update match states and re-queue analysis as matches complete.

## Dependencies

- TBA client (`clients.tba`) — primary data source for events and matches
- FIRST events API — supplementary event discovery
- RQ (Redis Queue) — analysis job queuing
- Redis — distributed state for live monitoring
- `Event`, `Match`, `EventTeam` ORM models
