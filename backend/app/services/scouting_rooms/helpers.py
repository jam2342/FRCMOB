# Scouting metric and climb computation helpers.
#
# Extracted from routes_scouting.py to reduce file size and enable
# reuse in other service modules.  All functions are pure logic
# (no DB session needed) unless documented otherwise.

from __future__ import annotations

import math
from typing import Any, Callable

from app.core.config import settings
from app.db import models
from app.services.quality_gate import evaluate_summary_quality_gate
from app.services.ratings.model import calibrate_public_rating_scale
from app.services.team_utils import model_uses_calibrated_public_scale as _model_uses_calibrated_public_scale

# ── Constants ──────────────────────────────────────────────────

RECENT_STATS_MATCH_WINDOW = 20
RECENT_STATS_PRIORITY_WINDOW = 10
RECENT_STATS_PRIORITY_WEIGHT = 2.0
RECENT_STATS_BASE_WEIGHT = 1.0

# ── Tiny math / type helpers ──────────────────────────────────

def weight_for_recent_index(index: int) -> float:
    return RECENT_STATS_PRIORITY_WEIGHT if index < RECENT_STATS_PRIORITY_WINDOW else RECENT_STATS_BASE_WEIGHT

def rating_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None

def rating_display_value(value: object, model_version: object) -> float | None:
    parsed = rating_float(value)
    if parsed is None:
        return None
    if _model_uses_calibrated_public_scale(model_version):
        return round(parsed, 3)
    return round(calibrate_public_rating_scale(parsed), 3)

def delta_or_none(after_value: float | None, before_value: float | None) -> float | None:
    if after_value is None or before_value is None:
        return None
    return after_value - before_value

def clamp_metric(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))

def mean_metric(values: list[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))

def safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None

def sigmoid(value: float) -> float:
    clamped = max(-60.0, min(60.0, float(value)))
    return 1.0 / (1.0 + math.exp(-clamped))

def safe_div(numerator: float, denominator: float) -> float | None:
    if abs(denominator) < 1e-12:
        return None
    return numerator / denominator

def pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    x_vals, y_vals = xs[:n], ys[:n]
    x_mean = sum(x_vals) / n
    y_mean = sum(y_vals) / n
    cov = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))
    sx = math.sqrt(max(0.0, sum((x - x_mean) ** 2 for x in x_vals)))
    sy = math.sqrt(max(0.0, sum((y - y_mean) ** 2 for y in y_vals)))
    if sx < 1e-12 or sy < 1e-12:
        return None
    return max(-1.0, min(1.0, cov / (sx * sy)))

# ── Finding source helpers ────────────────────────────────────

def is_score_breakdown_source(
    source: object,
    *,
    finding: models.TeamMatchFinding | None = None,
) -> bool:
    token = str(source or "").strip().lower()
    if "score_breakdown" in token:
        return True
    if finding is not None and isinstance(finding.summary, dict):
        summary = finding.summary
        if bool(summary.get("official_score_breakdown")):
            return True
        official_source = str(summary.get("official_climb_source") or "").strip().lower()
        if "score_breakdown" in official_source:
            return True
    return False

_VIDEO_TRACKING_BACKENDS = frozenset({"motion_v1", "yolo_bytetrack", "auto"})

def is_video_finding_source(finding: models.TeamMatchFinding) -> bool:
    source_token = str(finding.source or "").strip().lower()
    if not source_token:
        return False
    if is_score_breakdown_source(source_token, finding=finding):
        return False
    if source_token.startswith("video_"):
        return True
    if source_token in {"motion_v1", "yolo_bytetrack"}:
        return True
    if "yolo" in source_token or "bytetrack" in source_token:
        return True

    summary = finding.summary if isinstance(finding.summary, dict) else {}
    analysis_context = summary.get("analysis_context") if isinstance(summary.get("analysis_context"), dict) else {}
    tracking_backend = str(analysis_context.get("tracking_backend") or "").strip().lower()
    return tracking_backend in _VIDEO_TRACKING_BACKENDS

def finding_source_priority(source: object) -> int:
    token = str(source or "").strip().lower()
    if not token:
        return 0
    if token.startswith("video_") or token in {"motion_v1", "yolo_bytetrack"}:
        return 3
    if "yolo" in token or "bytetrack" in token:
        return 3
    if is_score_breakdown_source(token):
        return 1
    return 2

def finding_row_match_time(row: tuple) -> int:
    if len(row) < 2:
        return -1
    value = row[1]
    if isinstance(value, bool):
        return -1
    if isinstance(value, (int, float)):
        return int(value)
    return -1

def dedupe_finding_rows_by_match(rows: list[tuple]) -> list[tuple]:
    selected: dict[str, tuple] = {}
    for row in rows:
        if not row:
            continue
        finding = row[0]
        if not isinstance(finding, models.TeamMatchFinding):
            continue
        match_key = str(finding.match_key or "")
        if not match_key:
            continue
        current = selected.get(match_key)
        if current is None:
            selected[match_key] = row
            continue
        current_finding = current[0]
        candidate_priority = finding_source_priority(getattr(finding, "source", None))
        current_priority = finding_source_priority(getattr(current_finding, "source", None))
        if candidate_priority > current_priority:
            selected[match_key] = row
            continue
        if candidate_priority < current_priority:
            continue
        if int(getattr(finding, "id", 0) or 0) > int(getattr(current_finding, "id", 0) or 0):
            selected[match_key] = row

    deduped = list(selected.values())
    deduped.sort(
        key=lambda row: (
            finding_row_match_time(row),
            int(getattr(row[0], "id", 0) or 0),
        ),
        reverse=True,
    )
    return deduped

# ── Fuel / metric computation ─────────────────────────────────

def effective_fuel_scoring_rate(finding: models.TeamMatchFinding) -> float | None:
    raw_rate = finding.fuel_scoring_rate
    rate = float(raw_rate) if isinstance(raw_rate, (int, float)) and raw_rate >= 0.0 else None

    raw_cycle_time = finding.cycle_time_sec
    cycle_implied_rate = (
        (60.0 / max(1e-6, float(raw_cycle_time)))
        if isinstance(raw_cycle_time, (int, float)) and raw_cycle_time > 0.0
        else None
    )

    if rate is None:
        return round(float(cycle_implied_rate), 3) if cycle_implied_rate is not None else None
    if cycle_implied_rate is None:
        return round(float(rate), 3)

    legacy_cap = max(1.0, float(getattr(settings, "fuel_scoring_rate_max_per_min", 16.0) or 16.0))
    if rate >= (legacy_cap - 1e-6) and cycle_implied_rate > (rate * 1.01):
        return round(float(cycle_implied_rate), 3)
    return round(float(rate), 3)

def weighted_recent_mean(
    findings: list[models.TeamMatchFinding],
    value_getter: Callable[[models.TeamMatchFinding], float | None],
) -> float | None:
    weighted_sum = 0.0
    total_weight = 0.0
    for index, finding in enumerate(findings[:RECENT_STATS_MATCH_WINDOW]):
        value = value_getter(finding)
        if value is None:
            continue
        weight = weight_for_recent_index(index)
        weighted_sum += weight * float(value)
        total_weight += weight
    if total_weight <= 0.0:
        return None
    return round(weighted_sum / total_weight, 3)

def compute_averages(
    findings: list[models.TeamMatchFinding],
    climb_fallback_by_match: dict[str, float] | None = None,
) -> dict | None:
    if not findings:
        return None
    recent_findings = findings[:RECENT_STATS_MATCH_WINDOW]
    fuel_avg_raw = weighted_recent_mean(recent_findings, effective_fuel_scoring_rate)
    fuel_avg = round(float(fuel_avg_raw), 3) if isinstance(fuel_avg_raw, (int, float)) else None
    cycle_avg = weighted_recent_mean(recent_findings, lambda f: f.cycle_time_sec)
    auto_avg = weighted_recent_mean(recent_findings, lambda f: f.auto_contribution)
    defense_avg = weighted_recent_mean(recent_findings, lambda f: f.defensive_engagement_sec)
    reliability_avg = weighted_recent_mean(recent_findings, lambda f: f.reliability_score)

    climb_weighted_sum = 0.0
    climb_weight_total = 0.0
    for index, finding in enumerate(recent_findings):
        climb_value = (
            float(finding.climb_success_prob)
            if finding.climb_success_prob is not None
            else None
        )
        fallback = (
            climb_fallback_by_match.get(finding.match_key)
            if climb_fallback_by_match is not None
            else None
        )
        if fallback is not None and (climb_value is None or fallback > climb_value):
            climb_value = float(fallback)
        if climb_value is None:
            continue
        weight = weight_for_recent_index(index)
        climb_weighted_sum += weight * climb_value
        climb_weight_total += weight

    climb_avg = round(climb_weighted_sum / climb_weight_total, 3) if climb_weight_total > 0.0 else None

    return {
        "fuel_scoring_rate": fuel_avg,
        "cycle_time_sec": cycle_avg,
        "auto_contribution": auto_avg,
        "climb_success_prob": climb_avg,
        "defensive_engagement_sec": defense_avg,
        "reliability_score": reliability_avg,
    }

def metric_coverage_payload(
    findings: list[models.TeamMatchFinding],
    climb_fallback_by_match: dict[str, float] | None = None,
    quality_gate_fallback_used: bool = False,
) -> dict[str, dict[str, float | int | str | None]]:
    recent = findings[:RECENT_STATS_MATCH_WINDOW]
    total_matches = len(recent)
    fallback = climb_fallback_by_match or {}

    def _observed_count(metric_key: str) -> int:
        observed = 0
        for finding in recent:
            if metric_key == "fuel_scoring_rate":
                value = effective_fuel_scoring_rate(finding)
            else:
                value = getattr(finding, metric_key, None)
            if metric_key == "climb_success_prob":
                fallback_value = fallback.get(finding.match_key)
                if isinstance(fallback_value, (int, float)) and (value is None or fallback_value > float(value)):
                    value = float(fallback_value)
            if isinstance(value, (int, float)):
                observed += 1
        return observed

    def _missing_reason(metric_key: str, observed: int) -> str | None:
        if observed > 0:
            return None
        if total_matches <= 0:
            return "No analyzed matches available in the current season scope."
        if quality_gate_fallback_used:
            return (
                "Metric is still missing even after low-confidence fallback coverage; "
                "run fresh analysis for stronger signal."
            )
        if metric_key == "fuel_scoring_rate":
            return "No scoring detections were accepted by quality gate for recent matches."
        if metric_key == "cycle_time_sec":
            return "No valid cycle windows were detected in recent analyzed matches."
        if metric_key == "auto_contribution":
            return "No autonomous scoring/mobility contribution was detected yet."
        if metric_key == "climb_success_prob":
            return "No climb attempts/success signals were detected for recent matches."
        if metric_key == "defensive_engagement_sec":
            return "No defensive engagement intervals were detected for recent matches."
        if metric_key == "reliability_score":
            return "Tracking quality evidence is insufficient to estimate reliability."
        return "Insufficient signal coverage in recent matches."

    keys = [
        "fuel_scoring_rate",
        "cycle_time_sec",
        "auto_contribution",
        "climb_success_prob",
        "defensive_engagement_sec",
        "reliability_score",
    ]
    payload: dict[str, dict[str, float | int | str | None]] = {}
    for key in keys:
        observed = _observed_count(key)
        payload[key] = {
            "observed_matches": observed,
            "total_matches": total_matches,
            "coverage_0_1": round((observed / total_matches), 3) if total_matches > 0 else 0.0,
            "missing_reason": _missing_reason(key, observed),
        }
    return payload

# ── Climb helpers ─────────────────────────────────────────────

def infer_climb_from_match_events(
    match_events: list[models.MatchEvent],
) -> dict[str, float]:
    per_match_flags: dict[str, dict[str, bool]] = {}
    for event in match_events:
        event_type = (event.event_type or "").strip().lower()
        if "climb" not in event_type:
            continue
        flags = per_match_flags.setdefault(
            event.match_key,
            {"attempt": False, "success": False},
        )

        if any(token in event_type for token in ("success", "high", "mid", "low", "level", "travers")):
            flags["success"] = True
            continue

        meta = event.meta if isinstance(event.meta, dict) else {}
        if float(meta.get("points") or 0.0) > 0.0:
            flags["success"] = True
            continue

        flags["attempt"] = True

    inferred: dict[str, float] = {}
    for match_key, flags in per_match_flags.items():
        if flags["success"]:
            inferred[match_key] = 1.0
        elif flags["attempt"]:
            inferred[match_key] = 0.35
    return inferred

def normalize_climb_level_token(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric >= 2.75:
            return "level3"
        if numeric >= 1.75:
            return "level2"
        if numeric > 0.0:
            return "level1"
        return None

    token = str(value or "").strip().lower()
    if not token:
        return None
    compact = "".join(ch for ch in token if ch.isalnum())
    if compact in {"none", "no", "unknown", "null", "park", "parked"}:
        return None
    if (
        "level3" in compact
        or compact == "l3"
        or "deepcage" in compact
        or "travers" in compact
        or compact == "high"
    ):
        return "level3"
    if (
        "level2" in compact
        or compact == "l2"
        or "mid" in compact
        or "centercage" in compact
        or compact == "medium"
    ):
        return "level2"
    if (
        "level1" in compact
        or compact == "l1"
        or "low" in compact
        or "shallowcage" in compact
    ):
        return "level1"
    return None

def extract_climb_status_token(summary: dict[str, Any]) -> object:
    status_payload = summary.get("status") if isinstance(summary.get("status"), dict) else {}
    if isinstance(status_payload, dict):
        if "endgame_tower" in status_payload:
            return status_payload.get("endgame_tower")
        if "endgameTower" in status_payload:
            return status_payload.get("endgameTower")
        if "endgame" in status_payload:
            return status_payload.get("endgame")

    meta_payload = summary.get("meta") if isinstance(summary.get("meta"), dict) else {}
    meta_status_payload = meta_payload.get("status") if isinstance(meta_payload.get("status"), dict) else {}
    if isinstance(meta_status_payload, dict):
        if "endgame_tower" in meta_status_payload:
            return meta_status_payload.get("endgame_tower")
        if "endgameTower" in meta_status_payload:
            return meta_status_payload.get("endgameTower")
        if "endgame" in meta_status_payload:
            return meta_status_payload.get("endgame")

    for key in ("endgame_tower", "endgameTower", "endgame"):
        if key in summary:
            return summary.get(key)
    return None

def weighted_climb_source_summary(
    indexed_findings: list[tuple[int, models.TeamMatchFinding]],
    source_label: str,
    *,
    climb_fallback_by_match: dict[str, float] | None = None,
) -> dict[str, object]:
    weighted_sum = 0.0
    total_weight = 0.0
    matches_with_signal = 0
    matches_with_fallback_signal = 0
    for index, finding in indexed_findings:
        climb_value = (
            float(finding.climb_success_prob)
            if finding.climb_success_prob is not None
            else None
        )
        fallback_value = (
            climb_fallback_by_match.get(finding.match_key)
            if climb_fallback_by_match is not None
            else None
        )
        if (
            isinstance(fallback_value, (int, float))
            and (climb_value is None or float(fallback_value) > float(climb_value))
        ):
            climb_value = float(fallback_value)
            matches_with_fallback_signal += 1
        if climb_value is None:
            continue
        matches_with_signal += 1
        weight = weight_for_recent_index(index)
        weighted_sum += weight * climb_value
        total_weight += weight

    climb_avg = round(weighted_sum / total_weight, 3) if total_weight > 0.0 else None
    return {
        "source": source_label,
        "climb_success_prob": climb_avg,
        "matches_considered": len(indexed_findings),
        "matches_with_signal": matches_with_signal,
        "matches_with_fallback_signal": matches_with_fallback_signal,
    }

def compute_climb_level_capability(
    findings: list[models.TeamMatchFinding],
    *,
    climb_fallback_by_match: dict[str, float] | None = None,
) -> dict[str, object]:
    recent_findings = findings[:RECENT_STATS_MATCH_WINDOW]
    counts = {"level1": 0, "level2": 0, "level3": 0}
    matches_with_level = 0
    matches_with_success_no_level = 0

    for finding in recent_findings:
        summary = finding.summary if isinstance(finding.summary, dict) else {}
        token = extract_climb_status_token(summary)
        level = normalize_climb_level_token(token)
        if level in counts:
            counts[level] = int(counts[level]) + 1
            matches_with_level += 1
            continue
        climb_value = float(finding.climb_success_prob) if finding.climb_success_prob is not None else None
        fallback = (
            climb_fallback_by_match.get(finding.match_key)
            if climb_fallback_by_match is not None
            else None
        )
        if isinstance(fallback, (int, float)) and (climb_value is None or float(fallback) > float(climb_value)):
            climb_value = float(fallback)
        if climb_value is not None and climb_value >= 0.8:
            matches_with_success_no_level += 1

    best_level: str | None = None
    best_level_label = "No Level Data"
    best_level_score: float | None = None
    if int(counts["level3"]) > 0:
        best_level = "level3"
        best_level_label = "Level 3"
        best_level_score = 100.0
    elif int(counts["level2"]) > 0:
        best_level = "level2"
        best_level_label = "Level 2"
        best_level_score = 68.0
    elif int(counts["level1"]) > 0:
        best_level = "level1"
        best_level_label = "Level 1"
        best_level_score = 38.0
    elif matches_with_success_no_level > 0:
        best_level_label = "Success (Level Unknown)"
        best_level_score = 34.0

    return {
        "best_level": best_level,
        "best_level_label": best_level_label,
        "best_level_score_0_100": best_level_score,
        "level_counts": counts,
        "matches_with_level": int(matches_with_level),
        "matches_with_success_no_level": int(matches_with_success_no_level),
        "matches_considered": len(recent_findings),
    }

def compute_climb_sources(
    findings: list[models.TeamMatchFinding],
    *,
    climb_fallback_by_match: dict[str, float] | None = None,
) -> dict[str, object]:
    if not findings:
        empty = weighted_climb_source_summary([], "video_analyzed")
        return {
            "overall_climb_success_prob": None,
            "video_only": empty,
            "official_score_breakdown": {**empty, "source": "official_score_breakdown"},
            "level_capability": compute_climb_level_capability([]),
        }

    recent_findings = findings[:RECENT_STATS_MATCH_WINDOW]
    indexed_recent = list(enumerate(recent_findings))
    overall = weighted_climb_source_summary(
        indexed_recent,
        "all_sources",
        climb_fallback_by_match=climb_fallback_by_match,
    )
    video_only = weighted_climb_source_summary(
        [(index, finding) for index, finding in indexed_recent if is_video_finding_source(finding)],
        "video_analyzed",
    )
    official_only = weighted_climb_source_summary(
        [
            (index, finding)
            for index, finding in indexed_recent
            if is_score_breakdown_source(finding.source, finding=finding)
        ],
        "official_score_breakdown",
    )
    return {
        "overall_climb_success_prob": overall.get("climb_success_prob"),
        "video_only": video_only,
        "official_score_breakdown": official_only,
        "level_capability": compute_climb_level_capability(
            recent_findings,
            climb_fallback_by_match=climb_fallback_by_match,
        ),
    }

# ── Finding serialization & filtering ────────────────────────

def serialize_finding(finding: models.TeamMatchFinding, match_time: int | None) -> dict:
    return {
        "match_key": finding.match_key,
        "event_key": finding.event_key,
        "match_time": match_time,
        "alliance": finding.alliance,
        "station": finding.station,
        "source": finding.source,
        "fuel_scoring_rate": effective_fuel_scoring_rate(finding),
        "cycle_time_sec": finding.cycle_time_sec,
        "auto_contribution": finding.auto_contribution,
        "climb_success_prob": finding.climb_success_prob,
        "defensive_engagement_sec": finding.defensive_engagement_sec,
        "reliability_score": finding.reliability_score,
        "summary": finding.summary,
    }

def extract_perimeter_context(finding: models.TeamMatchFinding) -> tuple[str | None, str | None]:
    summary = finding.summary if isinstance(finding.summary, dict) else {}
    context = summary.get("analysis_context")
    if not isinstance(context, dict):
        return None, None

    perimeter_type = str(context.get("perimeter_type") or "").strip().lower()
    if perimeter_type not in {"welded", "andymark"}:
        perimeter_type = ""
    perimeter_source = str(context.get("perimeter_source") or "").strip() or None
    return (perimeter_type or None), perimeter_source

def apply_quality_gate_to_finding_rows(rows: list[tuple]) -> tuple[list[tuple], dict]:
    gate_config = quality_gate_config_payload()
    if not gate_config["enabled"]:
        return (
            rows,
            {
                **gate_config,
                "raw_count": len(rows),
                "accepted_count": len(rows),
                "excluded_count": 0,
                "excluded_examples": [],
            },
        )

    accepted: list[tuple] = []
    excluded_examples: list[dict] = []
    for row in rows:
        finding = row[0]
        is_valid, reason, coverage_score, detections = evaluate_summary_quality_gate(
            finding.summary if isinstance(finding, models.TeamMatchFinding) else None
        )
        if is_valid:
            accepted.append(row)
            continue
        if len(excluded_examples) < 12 and isinstance(finding, models.TeamMatchFinding):
            excluded_examples.append(
                {
                    "match_key": finding.match_key,
                    "analysis_run_id": finding.analysis_run_id,
                    "reason": reason,
                    "coverage_score": coverage_score,
                    "detections": detections,
                }
            )

    return (
        accepted,
        {
            **gate_config,
            "raw_count": len(rows),
            "accepted_count": len(accepted),
            "excluded_count": max(0, len(rows) - len(accepted)),
            "excluded_examples": excluded_examples,
        },
    )

def rows_for_metric_coverage(
    raw_rows: list[tuple],
    accepted_rows: list[tuple],
    quality_gate: dict,
) -> tuple[list[tuple], dict]:
    fallback_used = bool(quality_gate.get("enabled") and not accepted_rows and raw_rows)
    selected_rows = accepted_rows if accepted_rows else raw_rows
    source = "quality_gate_accepted"
    if not quality_gate.get("enabled"):
        source = "quality_gate_disabled"
    elif fallback_used:
        source = "quality_gate_fallback_raw"
    elif not selected_rows:
        source = "none"

    raw_count = int(quality_gate.get("raw_count") or 0)
    accepted_count = int(quality_gate.get("accepted_count") or 0)
    accepted_ratio = (float(accepted_count) / float(raw_count)) if raw_count > 0 else None
    return (
        selected_rows,
        {
            "source": source,
            "fallback_used": fallback_used,
            "raw_count": raw_count,
            "accepted_count": accepted_count,
            "accepted_ratio_0_1": (
                round(max(0.0, min(1.0, accepted_ratio)), 3)
                if isinstance(accepted_ratio, float)
                else None
            ),
        },
    )

# ── Rating row serialization ─────────────────────────────────

def serialize_rating_row(row: models.EventTeamRating, team: models.Team | None) -> dict:
    rd = rating_display_value(row.rating_0_100, row.model_version)
    robot_display = rating_display_value(row.robot_level_0_100, row.model_version)
    driver_display = rating_display_value(row.driver_skill_0_100, row.model_version)
    return {
        "event_key": row.event_key,
        "team_key": row.team_key,
        "team_number": team.team_number if team else None,
        "nickname": team.nickname if team else None,
        "rating_0_100": rd,
        "confidence_0_1": row.confidence_0_1,
        "robot_level_0_100": robot_display,
        "driver_skill_0_100": driver_display,
        "subscores": {
            "results_anchor": row.results_anchor,
            "throughput": row.throughput,
            "shift_productivity": row.shift_productivity,
            "capacity_utilization": row.capacity_utilization,
            "endgame": row.endgame,
            "auto_contribution": ((row.details_json or {}).get("subscores") or {}).get(
                "auto_contribution"
            ),
            "anti_defense": ((row.details_json or {}).get("subscores") or {}).get(
                "anti_defense"
            ),
            "manual_points_impact": ((row.details_json or {}).get("subscores") or {}).get(
                "manual_points_impact"
            ),
            "rp_contribution": ((row.details_json or {}).get("subscores") or {}).get(
                "rp_contribution"
            ),
            "defense_presence": ((row.details_json or {}).get("subscores") or {}).get(
                "defense_presence"
            ),
            "consistency": row.consistency,
            "penalty_discipline": ((row.details_json or {}).get("subscores") or {}).get(
                "penalty_discipline"
            ),
        },
        "pros": row.pros_json or [],
        "cons": row.cons_json or [],
        "evidence": row.evidence_json or [],
        "details": row.details_json or {},
        "model_version": row.model_version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }

def signal_labels(signals: object) -> set[str]:
    labels: set[str] = set()
    if not isinstance(signals, list):
        return labels
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        label = signal.get("label")
        if isinstance(label, str) and label.strip():
            labels.add(label.strip())
    return labels

# ── Quality-gate helpers for upstream callers ─────────────────

def quality_gate_config_payload() -> dict:
    from app.services.quality_gate import quality_gate_config_payload as _gate_cfg
    return _gate_cfg()
