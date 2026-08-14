# Intel payload builder registry.
#
# Provides a clean service-layer interface for building intel payloads.
# The actual implementations are registered by the API layer at startup,
# breaking the services → routes import cycle.

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Builder function type aliases ────────────────────────────────────────

EventTeamsIntelBuilder = Callable[..., Coroutine[Any, Any, dict[str, Any]]]
TeamIntelBuilder = Callable[..., Coroutine[Any, Any, dict[str, Any]]]

# ── Registry ─────────────────────────────────────────────────────────────

_event_teams_intel_builder: EventTeamsIntelBuilder | None = None
_team_intel_builder: TeamIntelBuilder | None = None

def register_event_teams_intel_builder(builder: EventTeamsIntelBuilder) -> None:
    # Called by routes_teams at import time to register its builder.
    global _event_teams_intel_builder
    _event_teams_intel_builder = builder

def register_team_intel_builder(builder: TeamIntelBuilder) -> None:
    # Called by routes_teams at import time to register its builder.
    global _team_intel_builder
    _team_intel_builder = builder

# ── Public API for services ──────────────────────────────────────────────

def build_event_teams_intel_payload(
    *,
    db: Session,
    event_key: str,
    include_tba: bool = True,
    include_statbotics: bool = True,
    auto_heal_ratings: bool = True,
    include_season_fallback: bool = True,
    include_rating_details: bool = False,
    include_rating_signals: bool = False,
) -> dict[str, Any]:
    # Build event-scoped team intel payload.
    #
    # Runs the registered async builder synchronously via asyncio.run().
    if _event_teams_intel_builder is None:
        logger.warning("intel_builders: event_teams_intel_builder not registered")
        return {}
    return asyncio.run(
        _event_teams_intel_builder(
            db=db,
            event_key=event_key,
            include_tba=include_tba,
            include_statbotics=include_statbotics,
            auto_heal_ratings=auto_heal_ratings,
            include_season_fallback=include_season_fallback,
            include_rating_details=include_rating_details,
            include_rating_signals=include_rating_signals,
        )
    )

def build_team_intel_payload(
    *,
    db: Session,
    team_key: str,
    event_key: str | None,
    preferred_year: int,
    fallback_year: int,
    include_tba: bool = True,
    include_statbotics: bool = True,
    allow_season_fallback: bool = True,
    auto_heal_ratings: bool = True,
) -> dict[str, Any]:
    # Build team-scoped intel payload.
    #
    # Runs the registered async builder synchronously via asyncio.run().
    if _team_intel_builder is None:
        logger.warning("intel_builders: team_intel_builder not registered")
        return {}
    return asyncio.run(
        _team_intel_builder(
            db=db,
            team_key=team_key,
            event_key=event_key,
            preferred_year=preferred_year,
            fallback_year=fallback_year,
            include_tba=include_tba,
            include_statbotics=include_statbotics,
            allow_season_fallback=allow_season_fallback,
            auto_heal_ratings=auto_heal_ratings,
        )
    )
