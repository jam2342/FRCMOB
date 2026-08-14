# Shared team/event utility helpers used by multiple route modules.
#
# Extracted from routes_teams.py and routes_scouting.py to eliminate duplication.
from __future__ import annotations

def _region_label(state_prov: str | None, country: str | None) -> str:
    normalized_country = (country or "").strip()
    if normalized_country in {"USA", "Canada"}:
        if state_prov and state_prov.strip():
            return state_prov.strip()
        return normalized_country
    if normalized_country:
        return normalized_country
    return "Unknown"

def _model_uses_calibrated_public_scale(model_version: object) -> bool:
    if not isinstance(model_version, str):
        return False
    normalized = model_version.strip().lower()
    calibrated_prefixes = (
        "rating_v11",
        "primary_video_model_v",
        "fallback_external_intel_v",
        "rating_sparse_external_fallback_v",
    )
    return "calibrated_scale" in normalized or any(
        normalized.startswith(prefix) for prefix in calibrated_prefixes
    )

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

# Public aliases — these functions are used across module boundaries.
region_label = _region_label
team_number_from_team_key = _team_number_from_team_key
model_uses_calibrated_public_scale = _model_uses_calibrated_public_scale
