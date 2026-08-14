# Team breakdown service.
#
# Provides a service-layer interface for loading team breakdown payloads.
# This breaks the routes_teams → routes_scouting cross-route import.

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

def load_team_breakdown_payload(
    db: Session,
    team_key: str,
    event_key: str | None,
    *,
    match_limit: int = 20,
    event_limit: int = 200,
    track_limit: int = 300,
) -> dict[str, Any] | None:
    # Load a team breakdown payload.
    #
    # Returns the breakdown dict, or None if the team was not found.
    # This avoids raising HTTP exceptions at the service layer.
    # Lazy import to avoid circular dependencies at module load time.
    from app.api.routes_scouting import get_team_breakdown

    try:
        return get_team_breakdown(
            team_key=team_key,
            event_key=event_key,
            match_limit=match_limit,
            event_limit=event_limit,
            track_limit=track_limit,
            db=db,
        )
    except Exception as exc:
        # Only suppress 404s (team not found); re-raise everything else.
        status_code = getattr(exc, "status_code", None)
        if status_code == 404:
            return None
        raise
