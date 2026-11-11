# Shared team/event utility helpers used by multiple route modules.
#
# Extracted from routes_teams.py and routes_scouting.py to eliminate duplication.
from __future__ import annotations

import re

def _region_label(state_prov: str | None, country: str | None) -> str:
    normalized_country = (country or "").strip()
    if normalized_country in {"USA", "Canada"}:
        if state_prov and state_prov.strip():
            return state_prov.strip()
        return normalized_country
    if normalized_country:
        return normalized_country
    return "Unknown"

def _team_number_from_team_key(team_key: str) -> int:
    # Parse the numeric team number from a key like 'frc254'.
    #
    # Handles both normalised and raw input.  Returns 0 on parse failure.
    normalized = (team_key or "").strip().lower()
    if normalized.startswith("frc"):
        normalized = normalized[3:]
    try:
        return int(normalized)
    except ValueError:
        return 0

_STORED_TEAM_KEY_RE = re.compile(r"^frc(\d{1,5})([A-Za-z]?)$")


def stored_team_key(raw: str | None) -> str | None:
    # TBA lowercases B-team keys (frc100b); our teams table keeps frc100B. None if not a team key.
    match = _STORED_TEAM_KEY_RE.match(str(raw or "").strip().lower())
    return f"frc{match.group(1)}{match.group(2).upper()}" if match else None


def team_key_sort_key(team_key: str) -> tuple[int, str]:
    # frc100 < frc100B < frc101
    match = _STORED_TEAM_KEY_RE.match(team_key.lower())
    return (int(match.group(1)), match.group(2).upper()) if match else (0, team_key)


# Public aliases — these functions are used across module boundaries.
region_label = _region_label
team_number_from_team_key = _team_number_from_team_key
