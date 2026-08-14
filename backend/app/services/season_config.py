# Centralized 2026 REBUILT season constants and helpers.
#
# This module is the single source of truth for REBUILT game-specific timing
# constants that were previously duplicated across score_breakdown.py,
# rating_constants.py, rating_game_context.py, score_truth.py, and
# routes_events.py.
from __future__ import annotations

# ── Current season ──────────────────────────────────────────────────────────────
# Single source for "what season are we in" — derived from the active game_config
# so a season swap is one number (season_template.json: season_year). Used as the
# default for year-scoped routes/queries and the current/previous fallback pair.
# Lazy import + try/except keeps this leaf module free of import-time cycles and
# safe if config can't load.

def _resolve_current_season_year() -> int:
    try:
        from app.services.game_config import load_game_config

        return int(load_game_config().season_year)
    except Exception:
        return REBUILT_SEASON_YEAR


# ── REBUILT 2026 timing constants ──────────────────────────────────────────────
REBUILT_SEASON_YEAR: int = 2026
REBUILT_TRANSITION_SHIFT_SEC: float = 10.0
REBUILT_ALLIANCE_SHIFT_SEC: float = 25.0
REBUILT_ALLIANCE_SHIFT_COUNT: int = 4
REBUILT_ENDGAME_WINDOW_SEC: float = 30.0
REBUILT_HUB_SCORE_GRACE_SEC: float = 3.0

# Resolved once at import; concrete int so it can serve as a default arg value.
CURRENT_SEASON_YEAR: int = _resolve_current_season_year()
PREVIOUS_SEASON_YEAR: int = CURRENT_SEASON_YEAR - 1

# ── Alliance shift helpers ─────────────────────────────────────────────────────

def shift_active_alliance(
    shift_index: int,
    shift1_active_alliance: str | None,
) -> str | None:
    # Return the active alliance for a given 1-based *shift_index*.
    #
    # Shifts alternate: if shift 1 belongs to ``shift1_active_alliance``, then
    # shift 2 belongs to the other alliance, shift 3 back to the first, etc.
    #
    # Returns ``None`` when *shift1_active_alliance* is unknown (both alliances
    # are treated as active in that case by callers).
    if shift1_active_alliance not in {"red", "blue"}:
        return None
    other = "blue" if shift1_active_alliance == "red" else "red"
    return shift1_active_alliance if shift_index % 2 == 1 else other

# ── Hub duration calculation ───────────────────────────────────────────────────

def rebuilt_active_hub_duration_sec(
    *,
    auto_sec: float,
    teleop_sec: float,
    endgame_sec: float,
    include_post_deactivate_grace: bool = True,
) -> float:
    # Compute the total seconds an alliance hub is actively scorable.
    #
    # This accounts for the transition period, half-active shift band (each
    # alliance is active 50 % of the shift time), endgame, and optional 3 s
    # grace windows after each deactivation boundary (2026 Manual 6.5).
    auto_end = max(0.0, float(auto_sec))
    teleop = max(0.0, float(teleop_sec))
    total_sec = max(auto_end, auto_end + teleop)
    endgame_start = max(auto_end, total_sec - max(0.0, float(endgame_sec)))

    transition_end = min(endgame_start, auto_end + REBUILT_TRANSITION_SHIFT_SEC)
    shift_band = max(0.0, endgame_start - transition_end)
    active_shift_duration = max(0.0, shift_band * 0.5)
    endgame_duration = max(0.0, total_sec - endgame_start)

    duration = max(1.0, (transition_end - auto_end) + active_shift_duration + endgame_duration)
    if include_post_deactivate_grace:
        # 2026 Manual 6.5: per alliance, 3 HUB deactivation boundaries add grace each.
        duration += 3.0 * REBUILT_HUB_SCORE_GRACE_SEC
    return max(1.0, duration)

def rebuilt_active_hub_duration_from_phases(
    phases: dict[str, float],
    *,
    include_post_deactivate_grace: bool = True,
) -> float:
    # Convenience wrapper accepting a *phases* dict (as used by score_truth).
    #
    # The dict may contain either ``teleop_sec`` directly or ``total_sec``
    # (from which ``teleop_sec`` is derived as ``total_sec - auto_sec``).
    auto_sec = float(phases.get("auto_sec") or 20.0)
    if "teleop_sec" in phases:
        teleop_sec = float(phases["teleop_sec"] or 140.0)
    else:
        total_sec = float(phases.get("total_sec") or 160.0)
        teleop_sec = max(0.0, total_sec - auto_sec)
    return rebuilt_active_hub_duration_sec(
        auto_sec=auto_sec,
        teleop_sec=teleop_sec,
        endgame_sec=float(phases.get("endgame_sec") or 30.0),
        include_post_deactivate_grace=include_post_deactivate_grace,
    )
