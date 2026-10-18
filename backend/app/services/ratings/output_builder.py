# Rating rows as every rating API shows them.
from __future__ import annotations

from typing import Any


def rating_subscores(row: Any) -> dict[str, Any]:
    # Measured from official results: teleop (throughput), auto, endgame, consistency.
    # The rest stay null: official data can't tell them apart (kept so older clients
    # that read the keys don't break).
    stored = (row.details_json or {}).get("subscores") or {}
    return {
        "throughput": row.throughput,
        "auto_contribution": stored.get("auto_contribution"),
        "endgame": row.endgame,
        "consistency": row.consistency,
        "results_anchor": None,
        "shift_productivity": None,
        "capacity_utilization": None,
        "anti_defense": None,
        "manual_points_impact": None,
        "rp_contribution": None,
        "defense_presence": None,
        "penalty_discipline": None,
    }


def rating_points(row: Any) -> dict[str, Any]:
    details = row.details_json or {}
    rating_range = details.get("rating_range_80")
    return {
        "expected_points": details.get("expected_points"),
        "basis": details.get("basis"),
        "matches_here": details.get("matches_here"),
        "matches_before": details.get("matches_before"),
        # Where the rating likely is (80%): narrow with many matches, wide for a team
        # we've barely seen.
        "rating_range_80": rating_range if isinstance(rating_range, list) and len(rating_range) == 2 else None,
        "dud_rate": details.get("dud_rate"),
        "recent_changes": details.get("recent_changes") or [],
    }


def build_response_payload(persisted_rows: list[Any]) -> list[dict[str, Any]]:
    payload = []
    for row, team in persisted_rows:
        payload.append(
            {
                "event_key": row.event_key,
                "team_key": row.team_key,
                "team_number": team.team_number if team else None,
                "nickname": team.nickname if team else None,
                "rating_0_100": row.rating_0_100,
                "confidence_0_1": row.confidence_0_1,
                "robot_level_0_100": row.robot_level_0_100,
                "driver_skill_0_100": row.driver_skill_0_100,
                "subscores": rating_subscores(row),
                **rating_points(row),
                "pros": row.pros_json or [],
                "cons": row.cons_json or [],
                "evidence": row.evidence_json or [],
                "model_version": row.model_version,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return payload
