# Statbotics EPA integration helpers for the rating model.

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

from app.core.security import sanitize_external_error
from app.db import models
from app.services.clients import statbotics as statbotics_client
from app.services.utils import _as_float

from app.services.ratings.constants import STATBOTICS_EPA_ENABLED

def _extract_statbotics_epa_value(team_payload: dict[str, Any] | None) -> float | None:
    if not isinstance(team_payload, dict):
        return None
    norm_epa = team_payload.get("norm_epa")
    if isinstance(norm_epa, dict):
        for key in ("current", "recent", "mean", "max"):
            value = _as_float(norm_epa.get(key))
            if value is not None:
                return value
    epa = team_payload.get("epa")
    if isinstance(epa, dict):
        for key in ("norm", "total_points", "total"):
            value = _as_float(epa.get(key))
            if value is not None:
                return value
    return None

def _load_statbotics_epa_by_team(
    team_rows: list[tuple[models.EventTeam, models.Team | None]],
) -> dict[str, dict[str, Any]]:
    # Load EPA values from Statbotics (synchronous version for backwards compatibility).
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    # Most call sites are synchronous; asyncio.run avoids deprecated event-loop access.
    if running_loop is None or not running_loop.is_running():
        return statbotics_client.run_sync(_load_statbotics_epa_by_team_async(team_rows))

    # When called from within an active loop, execute in a worker thread with its own loop.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: statbotics_client.run_sync(_load_statbotics_epa_by_team_async(team_rows)))
        return future.result()

async def _load_statbotics_epa_by_team_async(
    team_rows: list[tuple[models.EventTeam, models.Team | None]],
) -> dict[str, dict[str, Any]]:
    # Load EPA values from Statbotics asynchronously.
    epa_context_by_team: dict[str, dict[str, Any]] = {}
    if not STATBOTICS_EPA_ENABLED:
        return epa_context_by_team

    # Bounded concurrency: one team at a time made a 60-team recompute wait on 60
    # sequential round trips.
    semaphore = asyncio.Semaphore(8)

    async def _one(event_team: models.EventTeam, team: models.Team | None) -> tuple[str, dict[str, Any]]:
        team_key = event_team.team_key
        team_number = int(team.team_number) if team and isinstance(team.team_number, int) else None
        if not team_number or team_number <= 0:
            return team_key, {"available": False, "source": "missing_team_number", "raw_value": None}
        try:
            async with semaphore:
                team_payload = await statbotics_client.get_team(team_number)
            raw_value = _extract_statbotics_epa_value(team_payload)
            return team_key, {"available": raw_value is not None, "source": "statbotics_team", "raw_value": raw_value}
        except Exception as exc:
            return team_key, {
                "available": False,
                "source": "statbotics_error",
                "raw_value": None,
                "detail": sanitize_external_error(exc, default="statbotics_request_failed"),
            }

    for team_key, context in await asyncio.gather(*(_one(event_team, team) for event_team, team in team_rows)):
        epa_context_by_team[team_key] = context
    return epa_context_by_team
