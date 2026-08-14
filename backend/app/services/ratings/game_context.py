# Game-context loading and penalty-evidence helpers for the rating model.

from __future__ import annotations

from typing import Any

from app.db import models
from app.services.game_config import load_game_config

from app.services.ratings.constants import (
    DEFAULT_PHASES,
    DEFAULT_RP_THRESHOLDS,
    DEFAULT_SCORING_POINTS,
    MINOR_FOUL_POINTS,
    PENALTY_EVENT_POINT_WEIGHTS,
)
from app.services.ratings.helpers import _event_meta_number, _extract_first_number
from app.services.season_config import (
    CURRENT_SEASON_YEAR,
    rebuilt_active_hub_duration_sec as _rebuilt_active_hub_duration_sec,
)

def _manual_game_context() -> dict[str, Any]:
    context = {
        "phases": dict(DEFAULT_PHASES),
        "scoring_points": dict(DEFAULT_SCORING_POINTS),
        "rp_thresholds": dict(DEFAULT_RP_THRESHOLDS),
        "season_year": CURRENT_SEASON_YEAR,
        "config_version": "fallback",
    }
    try:
        config = load_game_config()
    except Exception:
        return context

    context["season_year"] = int(getattr(config, "season_year", CURRENT_SEASON_YEAR))
    context["config_version"] = str(getattr(config, "version", "unknown"))
    context["phases"] = {
        "auto_sec": float(getattr(config.phases, "auto_sec", DEFAULT_PHASES["auto_sec"])),
        "teleop_sec": float(getattr(config.phases, "teleop_sec", DEFAULT_PHASES["teleop_sec"])),
        "endgame_sec": float(getattr(config.phases, "endgame_sec", DEFAULT_PHASES["endgame_sec"])),
    }
    phase_auto_sec = float(context["phases"]["auto_sec"])
    phase_teleop_sec = float(context["phases"]["teleop_sec"])
    phase_endgame_sec = float(context["phases"]["endgame_sec"])
    context["phases"]["teleop_active_hub_sec"] = (
        _rebuilt_active_hub_duration_sec(
            auto_sec=phase_auto_sec,
            teleop_sec=phase_teleop_sec,
            endgame_sec=phase_endgame_sec,
            include_post_deactivate_grace=True,
        )
        if int(context["season_year"]) >= 2026
        else phase_teleop_sec
    )

    scoring_points = dict(DEFAULT_SCORING_POINTS)
    for action in getattr(config, "scoring_actions", []):
        key = str(getattr(action, "key", "") or "")
        if not key:
            continue
        points = getattr(action, "points", None)
        if points is None:
            continue
        try:
            scoring_points[key] = float(points)
        except (TypeError, ValueError):
            continue
    context["scoring_points"] = scoring_points

    rp_thresholds = dict(DEFAULT_RP_THRESHOLDS)
    for rp in getattr(config, "ranking_point_conditions", []):
        label = str(getattr(rp, "label", "") or "")
        description = str(getattr(rp, "description", "") or "")
        combined = f"{label} {description}".lower()
        value = _extract_first_number(combined)
        if value is None:
            continue
        if "energized" in combined:
            rp_thresholds["energized"] = value
        elif "supercharged" in combined:
            rp_thresholds["supercharged"] = value
        elif "traversal" in combined:
            rp_thresholds["traversal"] = value
    context["rp_thresholds"] = rp_thresholds

    return context

def _penalty_event_evidence(
    events_by_match: dict[str, list[models.MatchEvent]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    scored_matches: list[tuple[float, str, str | None]] = []
    for match_key, match_events in events_by_match.items():
        total_points = 0.0
        dominant_event_type: str | None = None
        dominant_weight = 0.0
        for event in match_events:
            event_type = str(event.event_type or "").strip().lower()
            weight = PENALTY_EVENT_POINT_WEIGHTS.get(event_type, 0.0)
            count_estimate = _event_meta_number(event, "count_estimate")
            count = max(0.0, count_estimate) if count_estimate is not None else 1.0
            if event_type == "zone_dwell":
                meta = event.meta or {}
                zone_key = str(meta.get("zone_key") or "").lower()
                if "protected" in zone_key:
                    # Soft proxy: protected-zone lingering is a frequent foul trigger.
                    weight += MINOR_FOUL_POINTS * 0.5
            if weight <= 0.0:
                continue
            weighted_value = weight * count
            total_points += weighted_value
            if weighted_value > dominant_weight:
                dominant_weight = weighted_value
                dominant_event_type = event_type

        if total_points <= 0.0:
            continue
        scored_matches.append((total_points, match_key, dominant_event_type))

    scored_matches.sort(key=lambda row: row[0], reverse=True)
    evidence: list[dict[str, Any]] = []
    for points, match_key, event_type in scored_matches[:limit]:
        evidence.append(
            {
                "match_key": match_key,
                "metric": "penalty_points",
                "value": round(points, 4),
                "event_type": event_type,
                "clip_url": None,
            }
        )
    return evidence
