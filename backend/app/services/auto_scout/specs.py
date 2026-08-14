from __future__ import annotations

from typing import Any


AUTO_SCOUT_FORM_FIELDS: tuple[str, ...] = (
    "auto_mobility",
    "auto_scored",
    "auto_missed",
    "auto_pickups",
    "auto_cycles",
    "auto_path_quality_1_5",
    "teleop_scored",
    "teleop_missed",
    "teleop_under_defense_scored",
    "teleop_under_defense_attempts",
    "teleop_cycles",
    "teleop_drops",
    "intake_failures",
    "foul_count",
    "offense_level_1_5",
    "defense_level_1_5",
    "field_awareness_1_5",
    "decision_quality_1_5",
    "communication_1_5",
    "anti_defense_level_1_5",
    "escape_level_1_5",
    "reroute_level_1_5",
    "hit_recovery_level_1_5",
    "bump_crosses",
    "trench_crosses",
    "climbed_under_pressure",
    "protected_zone_risk",
    "endgame_mode",
)

AUTO_SCOUT_V1_FORM_FIELDS_BY_SEASON: dict[int, tuple[str, ...]] = {
    2025: ("auto_mobility",),
    2026: (
        "auto_mobility",
        "auto_scored",
        "auto_missed",
        "teleop_scored",
        "teleop_under_defense_scored",
        "teleop_cycles",
        "offense_level_1_5",
        "defense_level_1_5",
        "field_awareness_1_5",
        "decision_quality_1_5",
        "intake_failures",
        "foul_count",
        "endgame_mode",
    ),
}

AUTO_SCOUT_DERIVED_INSIGHT_FIELDS_BY_SEASON: dict[int, tuple[str, ...]] = {
    2025: (
        "disabled_period",
        "defensive_engagement_presence",
        "cycle_pace_summary",
        "zone_dwell_breakdown",
    ),
    2026: (
        "disabled_period",
        "defensive_engagement_presence",
        "cycle_pace_summary",
        "zone_dwell_breakdown",
    ),
}

AUTO_SCOUT_FIELD_PRIORS_BY_SEASON: dict[int, dict[str, float]] = {
    2025: {
        "auto_mobility": 0.92,
        "disabled_period": 0.82,
        "defensive_engagement_presence": 0.8,
        "cycle_pace_summary": 0.78,
        "zone_dwell_breakdown": 0.98,
    },
    2026: {
        "auto_mobility": 0.92,
        "auto_scored": 0.84,
        "auto_missed": 0.82,
        "teleop_scored": 0.88,
        "teleop_under_defense_scored": 0.8,
        "teleop_cycles": 0.86,
        "offense_level_1_5": 0.78,
        "defense_level_1_5": 0.78,
        "field_awareness_1_5": 0.76,
        "decision_quality_1_5": 0.76,
        "intake_failures": 0.78,
        "foul_count": 0.8,
        "endgame_mode": 0.84,
        "disabled_period": 0.82,
        "defensive_engagement_presence": 0.8,
        "cycle_pace_summary": 0.78,
        "zone_dwell_breakdown": 0.98,
    }
}

AUTO_SCOUT_MIN_TRACK_COVERAGE_BY_SEASON: dict[int, float] = {
    2025: 0.35,
    2026: 0.35,
}


def mapper_version_for_season(season_year: int) -> str:
    year = int(season_year)
    if year == 2026:
        return "2026_v2"
    return f"{year}_v1"


def season_support_payload(season_year: int) -> dict[str, Any]:
    year = int(season_year)
    return {
        "supported": year in AUTO_SCOUT_V1_FORM_FIELDS_BY_SEASON,
        "form_fields": list(AUTO_SCOUT_V1_FORM_FIELDS_BY_SEASON.get(year, ())),
        "derived_insights": list(AUTO_SCOUT_DERIVED_INSIGHT_FIELDS_BY_SEASON.get(year, ())),
        "mapper_version": mapper_version_for_season(year),
        "min_track_coverage": float(AUTO_SCOUT_MIN_TRACK_COVERAGE_BY_SEASON.get(year, 0.35)),
    }
