# Analysis Service

This service is responsible for deep, structured evaluation of a team's match performance. It takes raw scouting data and produces multi-dimensional scores that capture how well a robot actually played — not just how many points it scored.

## What It Does

The core idea is that a simple point total doesn't tell the whole story. A team might score a lot but crumble under defensive pressure, or have an outstanding autonomous period but a weak endgame. This service breaks performance down into ten distinct dimensions and scores each one independently.

It also handles live match monitoring, allowing analysis sessions to stream in real time during an event.

## Files

**`elite_robot.py`**
The main analysis engine. The `EliteRobotAnalyzer` class accepts a team and event, loads their `TeamMatchFinding` records, and scores them across ten performance dimensions:

- **Game Performance** — scoring rates, cycle times, consistency under pressure
- **Autonomous Performance** — reliability and multi-piece capability in auto
- **Endgame Capability** — climb success probability and consistency
- **Game Piece Handling** — intake quality and release reliability
- **Reliability** — match-to-match consistency and penalty discipline
- **Driver Skill** — cycle efficiency, field positioning, decision-making
- **Strategic Versatility** — role classification (scorer, defender, feeder, endgame)
- **Engineering Quality** — mechanical health signals
- **Championship Consistency** — variance across matches
- **Match Composure** — performance under pressure in endgame scenarios

Each dimension is scored using percentile normalization and clamping, and confidence signals are attached based on how much data is available for that team.

**`hash.py`**
Generates deterministic hashes for analysis parameter sets. This ensures that the same input always maps to the same cached result, which is how the service avoids recomputing analysis that hasn't changed.

**`live_monitor.py`**
Manages live-streaming analysis sessions during active matches. It opens and tracks a monitoring session so analysis can be updated in near real-time as a match plays out.

**`pipeline_types.py`**
Shared dataclasses and type definitions for the analysis pipeline — the `_QualityResult` and related structures that carry per-stage results and quality signals through the pipeline.

**`pipeline_quality.py`**
Computes pipeline health/quality signals for an analysis run — the checks behind the run-level `model_degraded` flag and the `with_signal / findings` ratio that surface whether the real FRC model ran and the pipeline produced usable findings.

## How It Runs

1. `TeamMatchFinding` records are loaded for the given event and team.
2. Key metrics are extracted: scoring rates, cycle times, climb results, penalty counts, etc.
3. Each dimension is scored using normalization against the data range — higher relative performance = higher score.
4. Confidence is assessed based on how many matches are available and how consistent the data is.
5. The result is returned as a structured JSON payload with per-dimension scores and confidence values.

## Dependencies

- `scouting_rooms.elite_detector` — for role classification within the strategic versatility dimension
- `game_config` — for season-specific match parameters and scoring rules
- `TeamMatchFinding` ORM model — the primary data source
