# Scouting Rooms Service

This service powers the real-time collaborative scouting experience. It manages WebSocket connections, broadcasts live analysis updates to connected clients, and handles role classification — identifying which role each robot is likely playing during a match.

## What It Does

During a live event, multiple people may be scouting simultaneously. This service keeps everyone in sync by broadcasting findings and analysis updates over WebSockets as they become available. It also runs the `RoleClassifier`, which categorizes teams into strategic roles based on their match data.

## Files

**`bus.py`**
The message bus. Manages WebSocket connections and handles broadcasting updates to all clients in a room. When analysis produces new findings or a match state changes, the bus distributes those updates to everyone currently connected to that room.

**`elite_detector.py`**
Contains the `RoleClassifier`, which assigns a team to one of four roles based on their performance signals:

- **Scorer** — 60% throughput-weighted, 40% cycle quality
- **Defender** — high defensive engagement relative to scoring
- **Feeder** — moderate scoring with strong efficiency signals
- **Endgame Specialist** — high climb success rates

Each team gets four role signal scores (0–100), one per role. Versatility vs. specialization is also tracked — a team with one very high role score is a specialist, while a team with moderate scores across multiple roles is versatile.

**`realtime.py`**
Orchestrates real-time sync between analysis workers and connected clients. Listens for analysis events, formats them into WebSocket payloads, and routes them through the bus.

**`scope.py`**
Manages room scoping — rooms can be event-level (all matches at an event) or match-level (a specific match). Scope determines which updates a given client receives and which teams are tracked in the room.

**`helpers.py`**
Utility functions for the service — message formatting, room lookups, and data preparation.

**`maintenance.py`**
Cleanup jobs for stale rooms. If a room has had no active connections for a period of time, maintenance closes it and releases the associated resources.

## How It Runs

**Joining a Room:**
1. A client connects via WebSocket and specifies an event key and optionally a match key.
2. The service creates or joins the appropriate scoped room.
3. The client receives the current state snapshot for that room.

**Live Updates:**
1. An analysis worker completes processing and emits a findings update.
2. `realtime.py` picks up the event and formats it as a WebSocket message.
3. The bus broadcasts to all clients in the relevant room.
4. Clients update their UI with the new data.

**Role Classification:**
1. `RoleClassifier.classify()` receives the latest findings for a team.
2. Four role signal scores are computed from throughput, cycle quality, defensive engagement, and climb data.
3. The classification result is broadcast to connected clients as part of the findings update.

## Dependencies

- WebSockets — connection management
- `analysis.elite_robot` — used by the elite detector for deep analysis
- `EventTeamRating`, `TeamMatchFinding` — data sources for role classification
- Redis — room state persistence across worker restarts
