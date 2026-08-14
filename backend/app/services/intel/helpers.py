# Intel-snapshot cache helpers — extracted from routes_teams to avoid layer violations.

from __future__ import annotations

from app.core.config import settings

TEAM_INTEL_CACHE_VERSION = "v1"
TEAM_INTEL_CACHE_TTL_SEC = max(20, int(getattr(settings, "team_intel_cache_ttl_sec", 300)))
EVENT_INTEL_CACHE_TTL_SEC = max(20, int(getattr(settings, "event_intel_cache_ttl_sec", 180)))

def _normalize_team_key(raw_team_key: str) -> str:
    value = raw_team_key.strip().lower()
    if value.startswith("frc"):
        suffix = value[3:]
    else:
        suffix = value
    if suffix.isdigit():
        return f"frc{int(suffix)}"
    return value

def _cache_key(scope: str, token: str) -> str:
    normalized_scope = scope.strip().lower()
    normalized_token = token.strip().lower()
    return f"intel:{TEAM_INTEL_CACHE_VERSION}:{normalized_scope}:{normalized_token}"

def _team_intel_cache_token(
    *,
    team_key: str,
    event_key: str | None,
    preferred_year: int,
    fallback_year: int,
    include_tba: bool,
    include_statbotics: bool,
    allow_season_fallback: bool,
    auto_heal_ratings: bool,
) -> str:
    normalized_team_key = _normalize_team_key(team_key)
    normalized_event_key = (
        event_key.strip().lower()
        if isinstance(event_key, str) and event_key.strip()
        else "-"
    )
    return (
        f"{normalized_team_key}|{normalized_event_key}|{preferred_year}|{fallback_year}|"
        f"tba={int(include_tba)}|sb={int(include_statbotics)}|"
        f"fallback={int(allow_season_fallback)}|heal={int(auto_heal_ratings)}"
    )

def _event_intel_cache_token(
    *,
    event_key: str,
    include_tba: bool,
    include_statbotics: bool,
    auto_heal_ratings: bool,
    include_season_fallback: bool,
    include_rating_details: bool,
    include_rating_signals: bool,
) -> str:
    normalized_event_key = event_key.strip().lower()
    return (
        f"{normalized_event_key}|tba={int(include_tba)}|sb={int(include_statbotics)}|"
        f"heal={int(auto_heal_ratings)}|fallback={int(include_season_fallback)}|"
        f"rd={int(include_rating_details)}|rs={int(include_rating_signals)}"
    )
