# Rating model constants and configuration knobs.
#
# All tuning parameters, defaults, and threshold constants used across
# the rating sub-modules live here to avoid circular imports.

from __future__ import annotations

from app.core.config import settings

# ── Identity ─────────────────────────────────────────────────────────
MODEL_VERSION = "rating_v14_rebuilt_hub_terrain"
TBA_SCOREBREAKDOWN_SOURCE = "tba_score_breakdown"
FALLBACK_MODEL_LABEL = "fallback_external_intel_v1"

# ── Penalty values (REBUILT 2026 manual) ─────────────────────────────
MINOR_FOUL_POINTS = 5.0
MAJOR_FOUL_POINTS = 15.0

PENALTY_EVENT_POINT_WEIGHTS: dict[str, float] = {
    "minor_foul": MINOR_FOUL_POINTS,
    "foul_minor": MINOR_FOUL_POINTS,
    "illegal_contact": MINOR_FOUL_POINTS,
    "protected_zone_interference": MINOR_FOUL_POINTS,
    "major_foul": MAJOR_FOUL_POINTS,
    "foul_major": MAJOR_FOUL_POINTS,
    "tech_foul": MAJOR_FOUL_POINTS,
    "robot_rule_violation": MAJOR_FOUL_POINTS,
    "safety_violation": MAJOR_FOUL_POINTS,
    "yellow_card": MAJOR_FOUL_POINTS,
    "red_card": MAJOR_FOUL_POINTS * 2.0,
}

# ── Relevant match event types ───────────────────────────────────────
RELEVANT_MATCH_EVENT_TYPES: list[str] = sorted(
    {
        "auto_points_scored",
        "teleop_fuel_score_attempt",
        "teleop_fuel_score_success",
        "climb_success",
        "climb_attempt",
        "robot_disabled",
        "zone_entry",
        "zone_dwell",
        *PENALTY_EVENT_POINT_WEIGHTS.keys(),
    }
)

# ── Phase / scoring / RP defaults ────────────────────────────────────
DEFAULT_PHASES = {
    "auto_sec": 20.0,
    "teleop_sec": 140.0,
    "endgame_sec": 30.0,
}
DEFAULT_SCORING_POINTS = {
    "auto_fuel_score": 1.0,
    "teleop_fuel_score": 1.0,
    "auto_level1_climb": 15.0,
    "low_climb": 10.0,
    "mid_climb": 20.0,
    "high_climb": 30.0,
}
DEFAULT_RP_THRESHOLDS = {
    "energized": 100.0,
    "supercharged": 360.0,
    "traversal": 50.0,
}

# ── Recency configuration ────────────────────────────────────────────
RECENT_MATCH_WINDOW = max(4, int(settings.rating_recent_match_window))
RECENT_PRIORITY_WINDOW = max(1, min(RECENT_MATCH_WINDOW, int(settings.rating_recent_priority_window)))
RECENT_PRIORITY_WEIGHT = max(0.1, float(settings.rating_recent_priority_weight))
RECENT_BASE_WEIGHT = max(0.1, float(settings.rating_recent_base_weight))

# ── Penalty impact knobs ─────────────────────────────────────────────
PENALTY_IMPACT_NET_POINTS = max(0.0, float(settings.rating_penalty_impact_net_points))
PENALTY_IMPACT_SUBSCORES = max(0.0, float(settings.rating_penalty_impact_subscores))
PENALTY_IMPACT_DRIVER = max(0.0, float(settings.rating_penalty_impact_driver))
PENALTY_IMPACT_BASE_RATING = max(0.0, float(settings.rating_penalty_impact_base_rating))

# ── EPA / performance blend weights ──────────────────────────────────
STATBOTICS_EPA_ENABLED = bool(settings.rating_statbotics_epa_enabled)
RESULTS_ANCHOR_EPA_WEIGHT = max(0.0, min(0.5, float(settings.rating_results_anchor_epa_weight)))
PERFORMANCE_EPA_BLEND = max(0.0, min(0.35, float(settings.rating_performance_epa_blend)))
PERFORMANCE_AUTO_WEIGHT = max(0.10, min(0.35, float(settings.rating_performance_auto_weight)))
BASE_AUTO_WEIGHT = max(0.0, min(0.25, float(settings.rating_base_auto_weight)))
PERFORMANCE_ANTIDEFENSE_WEIGHT = max(
    0.0,
    min(0.22, float(settings.rating_performance_antidefense_weight)),
)
BASE_ANTIDEFENSE_WEIGHT = max(0.0, min(0.18, float(settings.rating_base_antidefense_weight)))

# ── Anti-defense configuration ────────────────────────────────────────
ANTIDEFENSE_STAGE_EARLY_QUALS_MULTIPLIER = max(
    0.35,
    min(1.0, float(settings.rating_antidefense_stage_early_quals_multiplier)),
)
ANTIDEFENSE_STAGE_LATE_QUALS_MULTIPLIER = max(
    0.7,
    min(1.3, float(settings.rating_antidefense_stage_late_quals_multiplier)),
)
ANTIDEFENSE_STAGE_ELIMS_MULTIPLIER = max(
    0.8,
    min(1.45, float(settings.rating_antidefense_stage_elims_multiplier)),
)
ANTIDEFENSE_STAGE_SUPPORT_MATCHES = max(
    2,
    min(12, int(settings.rating_antidefense_stage_support_matches)),
)

ANTI_DEFENSE_ELITE_DROP_PCT = 0.15
ANTI_DEFENSE_GOOD_DROP_PCT = 0.35

# ── Trend thresholds ─────────────────────────────────────────────────
THROUGHPUT_TREND_DELTA_THRESHOLD = max(0.0, float(settings.rating_trend_throughput_delta))
RELIABILITY_TREND_DELTA_THRESHOLD = max(0.0, float(settings.rating_trend_reliability_delta))
CYCLE_TREND_DELTA_THRESHOLD = max(0.0, float(settings.rating_trend_cycle_delta))
PENALTY_TREND_DELTA_THRESHOLD = max(0.0, float(settings.rating_trend_penalty_delta))

# ── Signal thresholds ────────────────────────────────────────────────
SIGNAL_MIN_CONFIDENCE = 0.22
SIGNAL_STRONG_CONFIDENCE = 0.30
SIGNAL_MIN_MATCHES = 2
SIGNAL_STRONG_MATCHES = 3
SIGNAL_TREND_MATCHES = 4
SIGNAL_TREND_COVERAGE = 0.50

# ── Public-facing rating scale calibration ────────────────────────────
_PUBLIC_RATING_CENTER = 61.5
_PUBLIC_RATING_SLOPE = 12.5
_PUBLIC_RATING_FLOOR = 24.0
_PUBLIC_RATING_CEILING = 98.0

# ── Sparse rating guard ──────────────────────────────────────────────
SPARSE_RATING_MIN_RAW = 30.0
SPARSE_RATING_MAX_RAW_NO_VIDEO = 80.0
SPARSE_RATING_MAX_RAW_LOW_MATCH = 83.0
SPARSE_RATING_MAX_RAW_MEDIUM_MATCH = 89.0
SPARSE_RATING_MAX_RAW_LOW_CONFIDENCE = 86.0
