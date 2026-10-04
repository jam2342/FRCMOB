# Analysis Service

This service is responsible for deep, structured evaluation of a team's match performance. It takes raw scouting data and produces multi-dimensional scores that capture how well a robot actually played — not just how many points it scored.

## What It Does

The core idea is that a simple point total doesn't tell the whole story. A team might score a lot but crumble under defensive pressure, or have an outstanding autonomous period but a weak endgame. This service breaks performance down into ten distinct dimensions and scores each one independently.

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

**`evidence.py`**
Decides whether a finding's evidence is strong enough to count; ratings and the quality gate call it.

**`runs.py`**
Run-kind constants (`official_truth`, `on_device`, and `video` for legacy broadcast rows) and the lookups that pick which run a consumer reads — including `best_on_device_run()`, the highest-quality operator-accepted phone recording of a match.

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
