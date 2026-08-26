# Team routes — hub module.
#
# Helper functions live here so that existing unittest.mock.patch targets
# (e.g. ``app.api.routes_teams._load_team_breakdown_payload``) keep working.
# Endpoint handlers have been moved to focused sub-modules:
#
# * ``app.api.teams.intel``  — search, team intel, event teams intel
# * ``app.api.teams.media``  — robot image, team logo
# * ``app.api.teams.stats``  — competitions, capability, event status, awards
#
# No business logic was changed during the split.

from datetime import datetime, timezone
import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.security import sanitize_external_error
from app.db import models
from app.services.auto_scout.scouting import summarize_team_auto_scout_profile
from app.services.calibration import fuel_rate as fuel_rate_calibration
from app.services.clients import statbotics as statbotics_client
from app.services.cache import get_async_cache
from app.services.intel.helpers import (
    _cache_key as _intel_cache_key,
    _event_intel_cache_token as _intel_event_cache_token,
    _normalize_team_key,
    _team_intel_cache_token as _intel_team_cache_token,
)
from app.services.ratings.model import calibrate_public_rating_scale, recompute_event_ratings
from app.services.scouting_rooms.scope import (
    active_game_season_year as _active_game_season_year,
    build_data_freshness_payload as _build_data_freshness_payload,
)
from app.services.team_utils import (
    model_uses_calibrated_public_scale as _model_uses_calibrated_public_scale,
    region_label as _region_label,
    team_number_from_team_key as _team_number_from_team_key,
)
from app.services.utils import _as_float, _clamp
from app.services.intel.builders import (
    register_event_teams_intel_builder,
    register_team_intel_builder,
)
from app.tba.client import TBAClient

router = APIRouter(prefix="/teams", tags=["teams"])
logger = logging.getLogger(__name__)

# Backward-compatible aliases for tests and legacy patch targets.
_cache_key = _intel_cache_key
_event_intel_cache_token = _intel_event_cache_token
_team_intel_cache_token = _intel_team_cache_token

class TeamCapabilityUpsertRequest(BaseModel):
    ball_capacity: int | None = Field(default=None, ge=0, le=30)
    notes: str | None = None

def _matches_team_query(query: str, team_key: str, team_number: int, nickname: str | None) -> bool:
    lower_nickname = (nickname or "").lower()
    number_str = str(team_number)
    team_key_lower = team_key.lower()
    # Exact or prefix match on team key
    if query == team_key_lower or team_key_lower.startswith(query):
        return True
    # For numeric queries, use prefix match on team number ("118" matches 118, 1180 but not 10118)
    if query.isdigit():
        return number_str.startswith(query)
    # Non-numeric: substring match on team key and nickname
    return query in team_key_lower or query in lower_nickname

def _event_year_from_key(event_key: str | None) -> int | None:
    if not event_key:
        return None
    prefix = event_key.strip()[:4]
    if prefix.isdigit():
        return int(prefix)
    return None

def _serialize_registered_event(event_payload: dict[str, Any], default_year: int) -> dict[str, Any] | None:
    event_key = event_payload.get("key")
    if not isinstance(event_key, str) or not event_key:
        return None

    raw_year = event_payload.get("year")
    year: int
    if isinstance(raw_year, int):
        year = raw_year
    else:
        year = default_year

    name = event_payload.get("name")
    if not isinstance(name, str) or not name.strip():
        name = event_key

    return {
        "event_key": event_key,
        "name": name,
        "year": year,
        "start_date": event_payload.get("start_date"),
        "end_date": event_payload.get("end_date"),
        "city": event_payload.get("city"),
        "state_prov": event_payload.get("state_prov"),
        "country": event_payload.get("country"),
    }

def _local_team_events(db: Session, team_key: str, year: int) -> list[dict[str, Any]]:
    rows = (
        db.query(models.Event)
        .join(models.EventTeam, models.EventTeam.event_key == models.Event.event_key)
        .filter(models.EventTeam.team_key == team_key, models.Event.year == year)
        .order_by(models.Event.event_key.asc())
        .all()
    )
    return [
        {
            "event_key": row.event_key,
            "name": row.name,
            "year": row.year,
            "start_date": None,
            "end_date": None,
            "city": None,
            "state_prov": None,
            "country": None,
        }
        for row in rows
    ]

def _team_registered_events(
    db: Session,
    team_key: str,
    preferred_year: int,
    fallback_year: int,
    tba: TBAClient | None,
    primary_year: int | None = None,
) -> tuple[int | None, list[dict[str, Any]], str]:
    years: list[int] = []
    for year in (primary_year, preferred_year, fallback_year):
        if isinstance(year, int) and year > 0 and year not in years:
            years.append(year)

    for year in years:
        if tba is not None:
            try:
                remote_payload = tba.team_events(team_key, year)
            except Exception:
                remote_payload = []
            if isinstance(remote_payload, list):
                remote_events = [
                    serialized
                    for serialized in (
                        _serialize_registered_event(event_payload, year)
                        for event_payload in remote_payload
                        if isinstance(event_payload, dict)
                    )
                    if serialized is not None
                ]
                if remote_events:
                    remote_events.sort(key=lambda item: (str(item.get("start_date") or ""), str(item["event_key"])))
                    return year, remote_events, "tba"

        local_events = _local_team_events(db, team_key, year)
        if local_events:
            return year, local_events, "local"

    latest_local_year = (
        db.query(func.max(models.Event.year))
        .join(models.EventTeam, models.EventTeam.event_key == models.Event.event_key)
        .filter(models.EventTeam.team_key == team_key)
        .scalar()
    )
    if latest_local_year is not None:
        local_latest = _local_team_events(db, team_key, int(latest_local_year))
        if local_latest:
            return int(latest_local_year), local_latest, "local_latest"

    return None, [], "none"

def _get_tba_client_or_503() -> TBAClient:
    if not settings.tba_auth_key.strip():
        raise HTTPException(status_code=503, detail="TBA auth key is not configured.")
    return TBAClient()

def _candidate_years(
    event_key: str | None,
    preferred_year: int,
    fallback_year: int,
    latest_local_year: int | None,
) -> list[int]:
    years: list[int] = []
    for year in (
        _event_year_from_key(event_key),
        preferred_year,
        fallback_year,
        latest_local_year,
    ):
        if isinstance(year, int) and year > 0 and year not in years:
            years.append(year)
    return years

def _media_candidate_years(base_years: list[int], lookback_years: int = 2) -> list[int]:
    years = [year for year in base_years if isinstance(year, int) and year > 0]
    if not years:
        return []
    newest_year = max(years)
    for delta in range(1, max(0, int(lookback_years)) + 1):
        candidate = newest_year - delta
        if candidate > 0 and candidate not in years:
            years.append(candidate)
    return years

def _serialize_team_capability(capability: models.TeamStaticCapability | None) -> dict:
    if capability is None:
        return {
            "ball_capacity": None,
            "notes": None,
            "updated_at": None,
        }
    return {
        "ball_capacity": capability.ball_capacity,
        "notes": capability.notes,
        "updated_at": capability.updated_at.isoformat() if capability.updated_at else None,
    }

def _now_unix() -> int:
    return int(time.time())

async def _aread_cached_payload(cache_key: str) -> tuple[dict[str, Any] | None, int | None]:
    raw = await get_async_cache().get(cache_key)
    if not raw:
        return None, None
    try:
        envelope = json.loads(raw)
    except ValueError:
        return None, None
    if not isinstance(envelope, dict):
        return None, None
    payload = envelope.get("payload")
    generated_unix = envelope.get("generated_unix")
    if not isinstance(payload, dict):
        return None, None
    age_sec = None
    if isinstance(generated_unix, int):
        age_sec = max(0, _now_unix() - generated_unix)
    return payload, age_sec

async def _awrite_cached_payload(cache_key: str, payload: dict[str, Any], ttl_sec: int) -> None:
    envelope = {
        "generated_unix": _now_unix(),
        "payload": payload,
    }
    await get_async_cache().set(cache_key, json.dumps(envelope, default=str), ttl=ttl_sec)

def _with_cache_metadata(
    payload: dict[str, Any],
    *,
    cache_key: str,
    cache_hit: bool,
    age_sec: int | None,
    ttl_sec: int,
) -> dict[str, Any]:
    with_meta = dict(payload)
    with_meta["cache"] = {
        "key": cache_key,
        "hit": bool(cache_hit),
        "age_sec": int(age_sec) if isinstance(age_sec, int) else 0,
        "ttl_sec": int(ttl_sec),
    }
    return with_meta

def _rating_display_value(value: object, model_version: object) -> float | None:
    parsed = _as_float(value)
    if parsed is None:
        return None
    if _model_uses_calibrated_public_scale(model_version):
        return round(parsed, 4)
    return round(calibrate_public_rating_scale(parsed), 4)

def _dedupe_warnings(messages: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for message in messages:
        normalized = str(message or "").strip()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped

def _normalize_missing_reasons(values: list[object]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized

def _coverage_tier(score_0_1: float) -> str:
    if score_0_1 >= 0.75:
        return "high"
    if score_0_1 >= 0.45:
        return "medium"
    return "low"

def _analysis_metric_coverage_summary(
    analysis_payload: dict[str, Any],
) -> tuple[float, list[str], dict[str, Any]]:
    averages = analysis_payload.get("averages") if isinstance(analysis_payload.get("averages"), dict) else {}
    metric_coverage = (
        analysis_payload.get("metric_coverage")
        if isinstance(analysis_payload.get("metric_coverage"), dict)
        else {}
    )
    keys = sorted(
        set(metric_coverage.keys())
        | set(averages.keys())
        | {
            "fuel_scoring_rate",
            "cycle_time_sec",
            "auto_contribution",
            "climb_success_prob",
            "defensive_engagement_sec",
            "reliability_score",
        }
    )
    if not keys:
        return 0.0, [], {"metrics_total": 0, "metrics_available": 0, "coverage_0_1": 0.0}

    reasons: list[str] = []
    available_count = 0
    metrics_total = len(keys)
    for key in keys:
        entry = metric_coverage.get(key) if isinstance(metric_coverage.get(key), dict) else {}
        observed_matches = int(_as_float(entry.get("observed_matches")) or 0)
        coverage_value = _as_float(entry.get("coverage_0_1"))
        estimated = bool(entry.get("estimated_from_model"))
        average_value = _as_float(averages.get(key))
        available = bool(
            observed_matches > 0
            or (coverage_value is not None and coverage_value > 0.0)
            or estimated
            or average_value is not None
        )
        if available:
            available_count += 1
        else:
            reasons.append(f"{key}_missing")
        missing_reason = entry.get("missing_reason")
        if isinstance(missing_reason, str) and missing_reason.strip():
            reasons.append(f"{key}:{missing_reason.strip()}")

    ratio = _clamp(float(available_count) / max(1.0, float(metrics_total)), 0.0, 1.0)
    return ratio, _normalize_missing_reasons(reasons), {
        "metrics_total": int(metrics_total),
        "metrics_available": int(available_count),
        "coverage_0_1": round(ratio, 4),
    }

def _build_team_data_coverage_payload(
    *,
    analysis_payload: dict[str, Any],
    rating_payload: dict[str, Any],
    tba_context: dict[str, Any],
    statbotics_context: dict[str, Any],
    fallbacks: list[dict[str, Any]],
) -> dict[str, Any]:
    reasons: list[str] = []
    analysis_matches = int(analysis_payload.get("matches_analyzed") or 0)
    analysis_factor = _clamp(float(analysis_matches) / 8.0, 0.0, 1.0)
    if analysis_matches <= 0:
        reasons.append("no_analyzed_matches")

    metric_ratio, metric_reasons, metric_summary = _analysis_metric_coverage_summary(analysis_payload)
    reasons.extend(metric_reasons)

    freshness = (
        analysis_payload.get("data_freshness")
        if isinstance(analysis_payload.get("data_freshness"), dict)
        else {}
    )
    is_outdated = bool(freshness.get("is_outdated"))
    if is_outdated:
        reasons.append("data_outdated")

    coverage_meta = (
        analysis_payload.get("analysis_coverage")
        if isinstance(analysis_payload.get("analysis_coverage"), dict)
        else {}
    )
    if bool(coverage_meta.get("fallback_used")):
        reasons.append("season_fallback_in_use")

    rating_available = bool(rating_payload.get("available"))
    if not rating_available:
        reasons.append("rating_unavailable")

    tba_enabled = bool(tba_context.get("enabled"))
    tba_available = bool(
        isinstance(tba_context.get("event_status_summary"), dict)
        and _as_float((tba_context.get("event_status_summary") or {}).get("rank")) is not None
    )
    if tba_enabled and not tba_available:
        reasons.append("tba_rank_unavailable")

    statbotics_enabled = bool(statbotics_context.get("enabled"))
    statbotics_available = bool(
        isinstance(statbotics_context.get("team_event"), dict)
        or isinstance(statbotics_context.get("team_year"), dict)
        or isinstance(statbotics_context.get("team"), dict)
    )
    if statbotics_enabled and not statbotics_available:
        reasons.append("statbotics_unavailable")

    fallback_fields = sum(
        len(item.get("fields") or [])
        for item in fallbacks
        if isinstance(item, dict) and item.get("kind") == "analysis_metric_estimates"
    )
    if fallback_fields > 0:
        reasons.append("model_estimate_backfill_in_use")

    external_present = [tba_available, statbotics_available]
    external_factor = _clamp(
        float(sum(1 for present in external_present if present)) / max(1.0, float(len(external_present))),
        0.0,
        1.0,
    )
    score_0_1 = _clamp(
        (0.42 * analysis_factor)
        + (0.30 * metric_ratio)
        + (0.16 * (1.0 if rating_available else 0.0))
        + (0.12 * external_factor)
        - (0.12 if is_outdated else 0.0)
        - (0.06 if bool(coverage_meta.get("fallback_used")) else 0.0),
        0.0,
        1.0,
    )
    normalized_reasons = _normalize_missing_reasons(reasons)
    return {
        "score_0_1": round(score_0_1, 4),
        "score_100": round(score_0_1 * 100.0, 1),
        "tier": _coverage_tier(score_0_1),
        "missing_reasons": normalized_reasons,
        "details": {
            "analysis_matches": int(analysis_matches),
            "analysis_factor_0_1": round(analysis_factor, 4),
            "metrics": metric_summary,
            "rating_available": bool(rating_available),
            "tba_rank_available": bool(tba_available),
            "statbotics_available": bool(statbotics_available),
            "is_outdated": bool(is_outdated),
            "season_fallback": bool(coverage_meta.get("fallback_used")),
            "backfilled_fields_count": int(fallback_fields),
        },
    }

def _build_event_team_data_coverage_payload(
    *,
    event_analysis_count: int,
    season_analysis_count: int,
    uses_season_fallback: bool,
    data_freshness: dict[str, Any],
    rating_payload: dict[str, Any],
    tba_status_summary: dict[str, Any],
    statbotics_available: bool,
) -> dict[str, Any]:
    reasons: list[str] = []
    event_factor = _clamp(float(event_analysis_count) / 6.0, 0.0, 1.0)
    season_factor = _clamp(float(season_analysis_count) / 10.0, 0.0, 1.0)
    if event_analysis_count <= 0:
        reasons.append("no_event_analysis_rows")
    if uses_season_fallback:
        reasons.append("season_fallback_in_use")

    is_outdated = bool(data_freshness.get("is_outdated"))
    if is_outdated:
        reasons.append("data_outdated")

    rating_available = bool(rating_payload.get("available"))
    if not rating_available:
        reasons.append("rating_unavailable")

    tba_rank_available = _as_float(tba_status_summary.get("rank")) is not None
    if not tba_rank_available:
        reasons.append("tba_rank_unavailable")
    if not statbotics_available:
        reasons.append("statbotics_unavailable")

    score_0_1 = _clamp(
        (0.52 * event_factor)
        + (0.12 * season_factor)
        + (0.16 * (1.0 if rating_available else 0.0))
        + (0.10 * (1.0 if tba_rank_available else 0.0))
        + (0.10 * (1.0 if statbotics_available else 0.0))
        - (0.12 if is_outdated else 0.0)
        - (0.08 if uses_season_fallback else 0.0),
        0.0,
        1.0,
    )
    return {
        "score_0_1": round(score_0_1, 4),
        "score_100": round(score_0_1 * 100.0, 1),
        "tier": _coverage_tier(score_0_1),
        "missing_reasons": _normalize_missing_reasons(reasons),
        "details": {
            "event_matches_analyzed": int(event_analysis_count),
            "season_matches_analyzed": int(season_analysis_count),
            "uses_season_fallback": bool(uses_season_fallback),
            "is_outdated": bool(is_outdated),
            "rating_available": bool(rating_available),
            "tba_rank_available": bool(tba_rank_available),
            "statbotics_available": bool(statbotics_available),
        },
    }

def _model_derived_averages_from_rating(rating_payload: dict[str, Any]) -> dict[str, float]:
    if not isinstance(rating_payload, dict) or not bool(rating_payload.get("available")):
        return {}

    details = rating_payload.get("details") if isinstance(rating_payload.get("details"), dict) else {}
    raw_features = (
        details.get("raw_features")
        if isinstance(details, dict) and isinstance(details.get("raw_features"), dict)
        else {}
    )
    fallback_model = (
        details.get("fallback_model")
        if isinstance(details, dict) and isinstance(details.get("fallback_model"), dict)
        else {}
    )
    subscores = rating_payload.get("subscores") if isinstance(rating_payload.get("subscores"), dict) else {}
    source_token = str(rating_payload.get("source") or "").strip().lower()
    model_version = str(rating_payload.get("model_version") or "").strip().lower()
    fallback_label = str(fallback_model.get("label") or "").strip().lower()
    sparse_external_fallback = (
        source_token == "sparse_external_fallback"  # nosec B105
        or model_version.startswith("rating_sparse_external_fallback")
        or fallback_label.startswith("sparse_external")
    )

    throughput = _as_float(subscores.get("throughput"))
    auto_subscore = _as_float(subscores.get("auto_contribution"))
    endgame_subscore = _as_float(subscores.get("endgame"))
    defense_subscore = _as_float(subscores.get("defense_presence"))
    consistency_subscore = _as_float(subscores.get("consistency"))

    fuel_rate_bps = _as_float(raw_features.get("bps_median"))
    fuel_rate_per_min = (
        _clamp(float(fuel_rate_bps) * 60.0, 0.0, 180.0)
        if isinstance(fuel_rate_bps, (int, float))
        else None
    )
    if (not sparse_external_fallback) and fuel_rate_per_min is None and throughput is not None:
        phase_estimate = fuel_rate_calibration.estimate_fuel_rate_per_min(
            throughput_score_0_100=throughput,
            auto_score_0_100=auto_subscore,
        )
        fuel_rate_per_min = _as_float(phase_estimate.get("fuel_rate_per_min"))
    if (not sparse_external_fallback) and fuel_rate_per_min is None and throughput is not None:
        # Defensive fallback if calibration output is unavailable.
        fuel_rate_bps = _clamp(0.1 + (throughput * 0.018), 0.05, 2.4)
        fuel_rate_per_min = _clamp(float(fuel_rate_bps) * 60.0, 0.0, 180.0)

    cycle_time = _as_float(raw_features.get("cycle_time_sec"))
    if (not sparse_external_fallback) and cycle_time is None and throughput is not None:
        cycle_time = _clamp(55.0 - (throughput * 0.42), 12.0, 65.0)

    auto_contribution = _as_float(raw_features.get("auto_points_est"))
    if (not sparse_external_fallback) and auto_contribution is None and auto_subscore is not None:
        auto_contribution = _clamp((auto_subscore / 100.0) * 12.0, 0.0, 18.0)

    climb_success = _as_float(raw_features.get("climb_success"))
    if (not sparse_external_fallback) and climb_success is None and endgame_subscore is not None:
        climb_success = _clamp(endgame_subscore / 100.0, 0.0, 1.0)
    elif climb_success is not None:
        climb_success = _clamp(climb_success, 0.0, 1.0)

    defense_time = _as_float(raw_features.get("defense_presence"))
    if (not sparse_external_fallback) and defense_time is None and defense_subscore is not None:
        defense_time = _clamp(defense_subscore * 0.55, 0.0, 65.0)

    reliability = _as_float(raw_features.get("uptime"))
    if (not sparse_external_fallback) and reliability is None and consistency_subscore is not None:
        reliability = _clamp(consistency_subscore / 100.0, 0.0, 1.0)
    elif reliability is not None:
        reliability = _clamp(reliability, 0.0, 1.0)

    return {
        "fuel_scoring_rate": (
            round(fuel_rate_per_min, 3)
            if isinstance(fuel_rate_per_min, (int, float))
            else None
        ),
        "cycle_time_sec": round(cycle_time, 3) if isinstance(cycle_time, (int, float)) else None,
        "auto_contribution": round(auto_contribution, 3) if isinstance(auto_contribution, (int, float)) else None,
        "climb_success_prob": round(climb_success, 3) if isinstance(climb_success, (int, float)) else None,
        "defensive_engagement_sec": round(defense_time, 3) if isinstance(defense_time, (int, float)) else None,
        "reliability_score": round(reliability, 3) if isinstance(reliability, (int, float)) else None,
    }

def _enrich_analysis_with_rating_estimates(
    analysis_payload: dict[str, Any],
    rating_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    merged = dict(analysis_payload)
    averages = merged.get("averages")
    metric_coverage = merged.get("metric_coverage")
    normalized_averages = dict(averages) if isinstance(averages, dict) else {}
    normalized_coverage = dict(metric_coverage) if isinstance(metric_coverage, dict) else {}

    has_observed_matches = any(
        int(_as_float(entry.get("observed_matches")) or 0) > 0
        for entry in normalized_coverage.values()
        if isinstance(entry, dict)
    )
    rating_details = rating_payload.get("details") if isinstance(rating_payload.get("details"), dict) else {}
    raw_features = (
        rating_details.get("raw_features")
        if isinstance(rating_details, dict) and isinstance(rating_details.get("raw_features"), dict)
        else {}
    )
    has_raw_metric_signal = any(
        _as_float(raw_features.get(key)) is not None
        for key in (
            "bps_median",
            "cycle_time_sec",
            "auto_points_est",
            "climb_success",
            "defense_presence",
            "uptime",
        )
    )
    if not has_observed_matches and not has_raw_metric_signal:
        merged["estimated_averages"] = {
            "applied": False,
            "source": "rating_model_fallback",
            "fields": [],
        }
        return merged, []

    model_estimates = _model_derived_averages_from_rating(rating_payload)
    applied_fields: list[str] = []
    for metric_key, metric_value in model_estimates.items():
        if metric_value is None:
            continue
        existing_value = _as_float(normalized_averages.get(metric_key))
        if existing_value is not None:
            continue
        normalized_averages[metric_key] = metric_value
        applied_fields.append(metric_key)
        coverage_entry = (
            dict(normalized_coverage.get(metric_key))
            if isinstance(normalized_coverage.get(metric_key), dict)
            else {}
        )
        coverage_entry["estimated_from_model"] = True
        if int(_as_float(coverage_entry.get("observed_matches")) or 0) <= 0:
            coverage_entry["missing_reason"] = (
                "Using model-derived estimate until analyzed clips are available."
            )
        normalized_coverage[metric_key] = coverage_entry

    if applied_fields:
        merged["averages"] = normalized_averages
        if normalized_coverage:
            merged["metric_coverage"] = normalized_coverage
        merged["estimated_averages"] = {
            "applied": True,
            "source": "rating_model_fallback",
            "fields": applied_fields,
        }
    else:
        merged["estimated_averages"] = {
            "applied": False,
            "source": "rating_model_fallback",
            "fields": [],
        }
    return merged, applied_fields

def _freshness_warnings_from_analysis_payload(payload: dict[str, Any]) -> list[str]:
    data_freshness = payload.get("data_freshness") if isinstance(payload.get("data_freshness"), dict) else {}
    warnings = data_freshness.get("warnings") if isinstance(data_freshness, dict) else []
    if not isinstance(warnings, list):
        return []
    return [str(item).strip() for item in warnings if isinstance(item, str) and item.strip()]

def _statbotics_norm_epa_from_context(statbotics_context: dict[str, Any]) -> float | None:
    team_payload = (
        statbotics_context.get("team")
        if isinstance(statbotics_context, dict) and isinstance(statbotics_context.get("team"), dict)
        else {}
    )
    norm_epa = team_payload.get("norm_epa") if isinstance(team_payload.get("norm_epa"), dict) else {}
    for key in ("current", "recent", "mean", "max"):
        value = _as_float(norm_epa.get(key))
        if value is not None:
            return value
    return None

def _synthesize_rating_from_sparse_signals(
    *,
    event_key: str | None,
    analysis_payload: dict[str, Any],
    statbotics_context: dict[str, Any],
) -> dict[str, Any] | None:
    averages = analysis_payload.get("averages") if isinstance(analysis_payload.get("averages"), dict) else {}
    fuel = _as_float(averages.get("fuel_scoring_rate"))
    auto = _as_float(averages.get("auto_contribution"))
    climb = _as_float(averages.get("climb_success_prob"))
    reliability = _as_float(averages.get("reliability_score"))
    defense = _as_float(averages.get("defensive_engagement_sec"))
    epa = _statbotics_norm_epa_from_context(statbotics_context)

    score = 50.0
    signal_count = 0
    if epa is not None:
        score += _clamp((epa - 1600.0) / 18.0, -20.0, 30.0)
        signal_count += 1
    if fuel is not None:
        score += _clamp((fuel - 0.6) * 22.0, -12.0, 12.0)
        signal_count += 1
    if auto is not None:
        score += _clamp((auto - 4.0) * 2.4, -10.0, 10.0)
        signal_count += 1
    if climb is not None:
        score += _clamp((climb - 0.50) * 20.0, -10.0, 10.0)
        signal_count += 1
    if reliability is not None:
        score += _clamp((reliability - 0.60) * 25.0, -10.0, 10.0)
        signal_count += 1

    if signal_count <= 0:
        return None

    raw_rating_value = _clamp(score, 30.0, 92.0)
    throughput = _clamp(42.0 + ((fuel or 0.7) * 24.0) + ((auto or 4.0) * 1.6), 20.0, 95.0)
    endgame = _clamp(35.0 + ((climb or 0.45) * 60.0), 20.0, 95.0)
    defense_presence = _clamp(32.0 + ((defense or 20.0) * 1.2), 20.0, 95.0)
    consistency = _clamp(32.0 + ((reliability or 0.55) * 62.0), 20.0, 95.0)
    raw_robot_level = _clamp((0.58 * raw_rating_value) + (0.42 * throughput), 20.0, 95.0)
    raw_driver_skill = _clamp((0.56 * consistency) + (0.44 * defense_presence), 20.0, 95.0)
    rating_value = calibrate_public_rating_scale(raw_rating_value)
    robot_level = calibrate_public_rating_scale(raw_robot_level)
    driver_skill = calibrate_public_rating_scale(raw_driver_skill)
    confidence = _clamp(0.12 + (0.07 * float(signal_count)), 0.12, 0.44)

    return {
        "available": True,
        "source": "sparse_external_fallback",
        "context_event_key": event_key,
        "rating_0_100": round(rating_value, 3),
        "confidence_0_1": round(confidence, 4),
        "robot_level_0_100": round(robot_level, 3),
        "driver_skill_0_100": round(driver_skill, 3),
        "subscores": {
            "results_anchor": round(_clamp((0.60 * rating_value) + 18.0, 20.0, 95.0), 4),
            "throughput": round(throughput, 4),
            "shift_productivity": round(_clamp((0.65 * throughput) + 12.0, 20.0, 95.0), 4),
            "capacity_utilization": round(_clamp((0.55 * throughput) + 18.0, 20.0, 95.0), 4),
            "endgame": round(endgame, 4),
            "auto_contribution": round(_clamp((auto or 4.0) * 8.0, 20.0, 95.0), 4),
            "anti_defense": round(_clamp((0.55 * defense_presence) + (0.45 * consistency), 20.0, 95.0), 4),
            "manual_points_impact": round(_clamp((0.62 * throughput) + (0.38 * rating_value), 20.0, 95.0), 4),
            "rp_contribution": round(_clamp((0.35 * endgame) + (0.65 * throughput), 20.0, 95.0), 4),
            "defense_presence": round(defense_presence, 4),
            "consistency": round(consistency, 4),
            "penalty_discipline": 50.0,
        },
        "pros": [],
        "cons": [
            {
                "label": "Sparse analysis coverage",
                "metric_value": float(signal_count),
                "percentile": 50.0,
                "confidence_0_1": round(confidence, 4),
            }
        ],
        "details": {
            "fallback_model": {
                "active": True,
                "label": "sparse_external_fallback",
                "signal_count": int(signal_count),
                "raw_rating_0_100": round(raw_rating_value, 4),
                "public_scale": "calibrated_v1",
            },
            "raw_features": {
                "bps_median": fuel,
                "auto_points_est": auto,
                "climb_success": climb,
                "defense_presence": defense,
                "uptime": reliability,
                "statbotics_norm_epa": epa,
            },
        },
        "model_version": "rating_sparse_external_fallback_v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

def _analysis_snapshot_from_breakdown(payload: dict[str, Any]) -> dict[str, Any]:
    team_data = payload.get("team") if isinstance(payload.get("team"), dict) else {}
    return {
        "team": {
            "team_key": team_data.get("team_key"),
            "team_number": team_data.get("team_number"),
            "nickname": team_data.get("nickname"),
        },
        "event_key": payload.get("event_key"),
        "season_scope": payload.get("season_scope"),
        "data_freshness": payload.get("data_freshness"),
        "analysis_coverage": payload.get("analysis_coverage"),
        "ml_projection": payload.get("ml_projection"),
        "matches_analyzed": int(payload.get("matches_analyzed") or 0),
        "matches_before_quality_gate": int(payload.get("matches_before_quality_gate") or 0),
        "matches_excluded_by_quality_gate": int(payload.get("matches_excluded_by_quality_gate") or 0),
        "averages": payload.get("averages"),
        "metric_units": payload.get("metric_units"),
        "climb_sources": payload.get("climb_sources"),
        "estimated_averages": payload.get("estimated_averages"),
        "metric_coverage": payload.get("metric_coverage"),
        "active_perimeter_type": payload.get("active_perimeter_type"),
        "perimeter_types": payload.get("perimeter_types") or [],
        "analysis_versions": payload.get("analysis_versions") or [],
        "event_type_counts": payload.get("event_type_counts") or [],
        "zone_time_sec": payload.get("zone_time_sec") or {},
        "run_ids": payload.get("run_ids") or [],
        "recent_matches": payload.get("recent_matches") or [],
    }

def _rating_preview(
    row: models.EventTeamRating,
    *,
    source: str,
    context_event_key: str | None,
    include_details: bool = True,
    include_signals: bool = True,
) -> dict[str, Any]:
    display_rating = _rating_display_value(row.rating_0_100, row.model_version)
    display_robot_level = _rating_display_value(row.robot_level_0_100, row.model_version)
    display_driver_skill = _rating_display_value(row.driver_skill_0_100, row.model_version)
    details = row.details_json if isinstance(row.details_json, dict) else {}
    subscores = details.get("subscores") if isinstance(details.get("subscores"), dict) else {}
    default_anchor = float(row.results_anchor)
    default_performance = float(row.throughput)
    default_consistency = float(row.consistency)
    default_driver = (
        float(display_driver_skill)
        if isinstance(display_driver_skill, (int, float))
        else float(row.driver_skill_0_100)
    )
    default_endgame = float(row.endgame)

    def _subscore(key: str, fallback: float) -> float:
        parsed = _as_float(subscores.get(key))
        return round(parsed, 4) if parsed is not None else round(fallback, 4)

    subscore_payload = {
        "results_anchor": round(default_anchor, 4),
        "throughput": round(default_performance, 4),
        "shift_productivity": round(float(row.shift_productivity), 4),
        "capacity_utilization": round(float(row.capacity_utilization), 4),
        "endgame": round(default_endgame, 4),
        "auto_contribution": _subscore("auto_contribution", default_anchor),
        "anti_defense": _subscore("anti_defense", default_driver),
        "manual_points_impact": _subscore("manual_points_impact", default_performance),
        "rp_contribution": _subscore("rp_contribution", default_anchor),
        "defense_presence": _subscore("defense_presence", default_consistency),
        "consistency": round(default_consistency, 4),
        "penalty_discipline": _subscore("penalty_discipline", 50.0),
    }

    return {
        "available": True,
        "source": source,
        "context_event_key": context_event_key,
        "rating_0_100": float(display_rating if display_rating is not None else row.rating_0_100),
        "confidence_0_1": float(row.confidence_0_1),
        "robot_level_0_100": float(
            display_robot_level if display_robot_level is not None else row.robot_level_0_100
        ),
        "driver_skill_0_100": float(
            display_driver_skill if display_driver_skill is not None else row.driver_skill_0_100
        ),
        "subscores": subscore_payload,
        "pros": (row.pros_json if isinstance(row.pros_json, list) else []) if include_signals else [],
        "cons": (row.cons_json if isinstance(row.cons_json, list) else []) if include_signals else [],
        "details": details if include_details else {},
        "model_version": row.model_version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }

def _latest_team_rating_row(
    db: Session,
    team_key: str,
) -> tuple[models.EventTeamRating | None, str | None]:
    row = (
        db.query(models.EventTeamRating, models.Event)
        .join(models.Event, models.Event.event_key == models.EventTeamRating.event_key)
        .filter(models.EventTeamRating.team_key == team_key)
        .order_by(models.Event.year.desc(), models.EventTeamRating.updated_at.desc())
        .first()
    )
    if row is None:
        return None, None
    rating_row, event_row = row
    return rating_row, (event_row.event_key if event_row is not None else None)

def _event_team_rating_row(
    db: Session,
    event_key: str,
    team_key: str,
) -> models.EventTeamRating | None:
    return (
        db.query(models.EventTeamRating)
        .filter(
            models.EventTeamRating.event_key == event_key,
            models.EventTeamRating.team_key == team_key,
        )
        .first()
    )

def _event_rating_rank_context(
    db: Session,
    *,
    event_key: str,
    team_key: str,
) -> dict[str, int] | None:
    row = _event_team_rating_row(db, event_key, team_key)
    if row is None:
        return None
    rank = _event_rating_rank(db, event_key=event_key, team_key=team_key)
    if rank is None:
        return None
    field_size = (
        db.query(func.count(models.EventTeamRating.team_key))
        .filter(models.EventTeamRating.event_key == event_key)
        .scalar()
    )
    # "4th" says nothing without "of 75". A figure with no comparison is
    # decoration, so the two travel together or not at all.
    return {"rank": int(rank), "field_size": int(field_size or 0)}

def _event_rating_rank(
    db: Session,
    *,
    event_key: str,
    team_key: str,
) -> int | None:
    row = _event_team_rating_row(db, event_key, team_key)
    if row is None:
        return None
    higher_count = (
        db.query(func.count(models.EventTeamRating.team_key))
        .filter(
            models.EventTeamRating.event_key == event_key,
            models.EventTeamRating.rating_0_100 > row.rating_0_100,
        )
        .scalar()
    )
    tie_break_count = (
        db.query(func.count(models.EventTeamRating.team_key))
        .filter(
            models.EventTeamRating.event_key == event_key,
            models.EventTeamRating.rating_0_100 == row.rating_0_100,
            models.EventTeamRating.team_key < team_key,
        )
        .scalar()
    )
    return int(higher_count or 0) + int(tie_break_count or 0) + 1

def _extract_tba_rank_record(status_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = status_payload if isinstance(status_payload, dict) else {}
    rank_block = payload.get("qual")
    if not isinstance(rank_block, dict):
        rank_block = payload.get("rank")
    rank_payload = rank_block if isinstance(rank_block, dict) else {}
    ranking = rank_payload.get("ranking") if isinstance(rank_payload.get("ranking"), dict) else {}
    sort_orders = ranking.get("sort_orders") if isinstance(ranking.get("sort_orders"), list) else []
    record_payload = rank_payload.get("record") if isinstance(rank_payload.get("record"), dict) else {}
    playoff_block = payload.get("playoff")
    return {
        "rank": rank_payload.get("rank"),
        "sort_orders": sort_orders,
        "record": {
            "wins": record_payload.get("wins"),
            "losses": record_payload.get("losses"),
            "ties": record_payload.get("ties"),
        },
        "playoff": playoff_block if isinstance(playoff_block, dict) else None,
    }

def _has_breakdown_signal(breakdown_payload: dict[str, Any] | None) -> bool:
    if not isinstance(breakdown_payload, dict):
        return False
    matches_analyzed = int(breakdown_payload.get("matches_analyzed") or 0)
    averages = breakdown_payload.get("averages")
    return matches_analyzed > 0 or isinstance(averages, dict)

def _load_team_breakdown_payload(
    db: Session,
    team_key: str,
    event_key: str | None,
) -> dict[str, Any]:
    from app.services.team_breakdown import load_team_breakdown_payload

    result = load_team_breakdown_payload(db, team_key, event_key)
    if result is not None:
        return result

    team_number = _team_number_from_team_key(team_key)
    normalized_team_key = _normalize_team_key(team_key)
    season_year = _event_year_from_key(event_key) or _active_game_season_year()
    active_year = _active_game_season_year()
    season_scope = {
        "mode": "event" if event_key else "season",
        "season_year": season_year,
        "active_season_year": active_year,
        "source": "event_key" if isinstance(_event_year_from_key(event_key), int) else "active_game_config",
        "event_key": event_key,
    }
    data_freshness = _build_data_freshness_payload(
        season_scope=season_scope,
        match_times=[],
        analyzed_matches=0,
        raw_analyzed_matches=0,
        quality_gate_enabled=False,
        quality_gate_fallback_used=False,
    )
    return {
        "ok": True,
        "team": {
            "team_key": normalized_team_key,
            "team_number": team_number,
            "nickname": None,
        },
        "event_key": event_key,
        "season_scope": season_scope,
        "data_freshness": data_freshness,
        "quality_gate": {
            "enabled": False,
            "raw_count": 0,
            "accepted_count": 0,
            "excluded_count": 0,
            "fallback_used": False,
        },
        "analysis_coverage": {
            "raw_match_rows": 0,
            "accepted_match_rows": 0,
            "fallback_used": False,
            "reason": "team_missing_local_ingest",
        },
        "matches_analyzed": 0,
        "matches_used_for_metrics": 0,
        "matches_before_quality_gate": 0,
        "matches_excluded_by_quality_gate": 0,
        "averages": None,
        "metric_coverage": {},
        "active_perimeter_type": None,
        "perimeter_types": [],
        "perimeter_sources": [],
        "analysis_versions": [],
        "event_type_counts": [],
        "zone_time_sec": {},
        "recent_matches": [],
        "recent_events": [],
        "recent_track_points": [],
        "run_ids": [],
    }

def _load_team_breakdown_with_fallback(
    db: Session,
    team_key: str,
    event_key: str | None,
    *,
    allow_season_fallback: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    fallbacks: list[dict[str, Any]] = []
    primary = _load_team_breakdown_payload(db, team_key, event_key)
    selected = primary
    if event_key and allow_season_fallback and not _has_breakdown_signal(primary):
        fallback = _load_team_breakdown_payload(db, team_key, None)
        if _has_breakdown_signal(fallback):
            selected = fallback
            fallback_scope = fallback.get("season_scope")
            fallback_year = (
                int(fallback_scope.get("season_year"))
                if isinstance(fallback_scope, dict) and isinstance(fallback_scope.get("season_year"), int)
                else None
            )
            warnings.append(
                "No analyzed clips available in the selected event context; temporarily using season-wide data."
            )
            if isinstance(fallback_year, int):
                warnings.append(
                    f"Season fallback is currently using {fallback_year} data until event-specific analysis is available."
                )
            fallbacks.append(
                {
                    "kind": "analysis_scope",
                    "from": f"event:{event_key}",
                    "to": f"season:{fallback_year}" if isinstance(fallback_year, int) else "season:latest",
                    "reason": "event_context_empty",
                }
            )
    return selected, fallbacks, warnings

def _resolve_team_rating_context(
    db: Session,
    team_key: str,
    event_key: str | None,
    *,
    auto_heal_ratings: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    fallbacks: list[dict[str, Any]] = []
    if event_key:
        rating_row = _event_team_rating_row(db, event_key, team_key)
        if rating_row is None and auto_heal_ratings and not settings.public_readonly_mode:
            event_exists = db.get(models.Event, event_key) is not None
            if event_exists:
                try:
                    recompute_event_ratings(db, event_key)
                except Exception as exc:
                    warnings.append(
                        f"Auto-heal ratings refresh failed for {event_key}: {sanitize_external_error(exc, default='refresh_failed')}"
                    )
                    logger.warning(
                        "teams.intel rating recompute failed event=%s team=%s error=%s",
                        event_key,
                        team_key,
                        sanitize_external_error(exc, default="refresh_failed"),
                    )
                rating_row = _event_team_rating_row(db, event_key, team_key)
                if rating_row is not None:
                    fallbacks.append(
                        {
                            "kind": "event_rating",
                            "from": "missing",
                            "to": "recomputed",
                            "event_key": event_key,
                            "reason": "auto_heal_ratings",
                        }
                    )
        if rating_row is not None:
            return _rating_preview(rating_row, source="event_rating", context_event_key=event_key), fallbacks, warnings

    latest_rating, latest_event_key = _latest_team_rating_row(db, team_key)
    if latest_rating is not None:
        warnings.append("No event-specific rating found; using latest available team rating.")
        fallbacks.append(
            {
                "kind": "event_rating",
                "from": "event_context",
                "to": latest_event_key,
                "reason": "latest_available",
            }
        )
        return (
            _rating_preview(latest_rating, source="latest_event_fallback", context_event_key=latest_event_key),
            fallbacks,
            warnings,
        )
    return (
        {
            "available": False,
            "source": "none",
            "context_event_key": event_key,
        },
        fallbacks,
        warnings,
    )

async def _load_statbotics_team_context(
    *,
    team_number: int,
    event_key: str | None,
    preferred_year: int,
    fallback_year: int,
) -> dict[str, Any]:
    warnings: list[str] = []
    team_payload = None
    team_event_payload = None
    team_year_payload = None
    resolved_year: int | None = None

    # Build year candidates list
    year_candidates: list[int] = []
    for year in (_event_year_from_key(event_key), preferred_year, fallback_year, preferred_year - 1):
        if isinstance(year, int) and year >= 1992 and year not in year_candidates:
            year_candidates.append(year)

    # Fire all Statbotics requests concurrently
    async def _fetch_team():
        try:
            return await statbotics_client.get_team(team_number)
        except Exception as exc:
            return exc

    async def _fetch_team_event():
        if not event_key:
            return None
        try:
            return await statbotics_client.get_team_event(team_number, event_key)
        except Exception as exc:
            return exc

    async def _fetch_team_year(yr: int):
        try:
            return (yr, await statbotics_client.get_team_year(team_number, yr))
        except Exception as exc:
            return (yr, exc)

    tasks: list = [_fetch_team(), _fetch_team_event()]
    tasks.extend(_fetch_team_year(c) for c in year_candidates[:3])
    results = await asyncio.gather(*tasks)

    # Unpack team result
    team_result = results[0]
    if isinstance(team_result, Exception):
        warnings.append(
            f"Statbotics team profile unavailable: {sanitize_external_error(team_result, default='request_failed')}"
        )
        return {
            "enabled": True,
            "available": False,
            "team": None,
            "team_event": None,
            "team_year": None,
            "resolved_year": None,
            "warnings": warnings,
        }
    team_payload = team_result

    # Unpack team_event result
    te_result = results[1]
    if te_result is None:
        pass  # no event_key provided
    elif isinstance(te_result, Exception):
        warnings.append(
            f"team_event unavailable: {sanitize_external_error(te_result, default='request_failed')}"
        )
    else:
        team_event_payload = te_result

    # Unpack team_year results — pick first successful by candidate order
    year_results = results[2:]
    for yr_result in year_results:
        yr, payload = yr_result
        if isinstance(payload, Exception):
            warnings.append(
                f"team_year {yr} unavailable: {sanitize_external_error(payload, default='request_failed')}"
            )
        else:
            if team_year_payload is None:
                team_year_payload = payload
                resolved_year = yr

    if team_year_payload is None and year_candidates:
        warnings.append("No Statbotics year bundle was available; EPA context may be sparse.")

    return {
        "enabled": True,
        "available": True,
        "team": team_payload if isinstance(team_payload, dict) else None,
        "team_event": team_event_payload if isinstance(team_event_payload, dict) else None,
        "team_year": team_year_payload if isinstance(team_year_payload, dict) else None,
        "resolved_year": resolved_year,
        "warnings": warnings,
    }

def _load_tba_team_context(
    *,
    team_key: str,
    event_key: str | None,
    preferred_year: int,
    fallback_year: int,
) -> dict[str, Any]:
    warnings: list[str] = []
    if not settings.tba_auth_key.strip():
        return {
            "enabled": False,
            "available": False,
            "event_status": None,
            "event_status_summary": None,
            "awards": [],
            "awards_year": None,
            "warnings": warnings,
        }

    tba = TBAClient()
    event_status_payload = None
    if event_key:
        try:
            status_payload = tba.team_event_status(team_key, event_key)
            event_status_payload = status_payload if isinstance(status_payload, dict) else None
        except Exception as exc:
            warnings.append(
                f"team_event_status unavailable: {sanitize_external_error(exc, default='request_failed')}"
            )

    awards_payload: list[dict[str, Any]] = []
    awards_year: int | None = None
    year_candidates: list[int] = []
    for year in (_event_year_from_key(event_key), preferred_year, fallback_year):
        if isinstance(year, int) and year > 0 and year not in year_candidates:
            year_candidates.append(year)
    for year in year_candidates:
        try:
            raw_awards = tba.team_awards_year(team_key, year)
        except Exception as exc:
            warnings.append(
                f"team_awards_year {year} unavailable: {sanitize_external_error(exc, default='request_failed')}"
            )
            continue
        if isinstance(raw_awards, list):
            awards_payload = [item for item in raw_awards if isinstance(item, dict)]
            awards_year = year
            if awards_payload:
                break

    return {
        "enabled": True,
        "available": True,
        "event_status": event_status_payload,
        "event_status_summary": _extract_tba_rank_record(event_status_payload),
        "awards": awards_payload,
        "awards_year": awards_year,
        "warnings": warnings,
    }

def _upsert_event_roster_if_missing(
    db: Session,
    event_key: str,
) -> tuple[models.Event | None, bool, str | None]:
    normalized_event_key = event_key.strip().lower()
    event = db.get(models.Event, normalized_event_key)
    existing_count = (
        db.query(models.EventTeam)
        .filter(models.EventTeam.event_key == normalized_event_key)
        .count()
    )
    if event is not None and existing_count > 0:
        return event, False, None
    if settings.public_readonly_mode:
        return event, False, "public_mode"
    if not settings.tba_auth_key.strip():
        return event, False, "tba_not_configured"

    try:
        tba = TBAClient()
        event_payload = tba.event(normalized_event_key)
        teams_payload = tba.event_teams(normalized_event_key)
    except Exception as exc:
        return event, False, sanitize_external_error(exc, default="TBA roster fetch failed.")

    if not isinstance(event_payload, dict) or not isinstance(teams_payload, list):
        return event, False, "invalid_tba_payload"

    year = (
        int(event_payload.get("year"))
        if isinstance(event_payload.get("year"), int)
        else (_event_year_from_key(normalized_event_key) or _active_game_season_year())
    )
    db.merge(
        models.Event(
            event_key=normalized_event_key,
            name=str(event_payload.get("name") or normalized_event_key),
            year=year,
        )
    )
    db.merge(
        models.EventProfile(
            event_key=normalized_event_key,
            city=event_payload.get("city"),
            state_prov=event_payload.get("state_prov"),
            country=event_payload.get("country"),
        )
    )
    for item in teams_payload:
        if not isinstance(item, dict):
            continue
        raw_team_key = item.get("key")
        if not isinstance(raw_team_key, str) or not raw_team_key.strip():
            continue
        team_key = _normalize_team_key(raw_team_key)
        team_number = (
            int(item.get("team_number"))
            if isinstance(item.get("team_number"), int)
            else _team_number_from_team_key(team_key)
        )
        db.merge(
            models.Team(
                team_key=team_key,
                team_number=team_number,
                nickname=item.get("nickname"),
            )
        )
        db.merge(
            models.TeamProfile(
                team_key=team_key,
                city=item.get("city"),
                state_prov=item.get("state_prov"),
                country=item.get("country"),
            )
        )
        db.merge(
            models.EventTeam(
                event_key=normalized_event_key,
                team_key=team_key,
            )
        )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        return event, False, sanitize_external_error(exc, default="Roster upsert failed.")
    refreshed_event = db.get(models.Event, normalized_event_key)
    return refreshed_event, True, None

async def _build_team_intel_payload(
    *,
    db: Session,
    team_key: str,
    event_key: str | None,
    preferred_year: int,
    fallback_year: int,
    include_tba: bool,
    include_statbotics: bool,
    allow_season_fallback: bool,
    auto_heal_ratings: bool,
) -> dict[str, Any]:
    normalized_team_key = _normalize_team_key(team_key)
    normalized_event_key = event_key.strip().lower() if isinstance(event_key, str) and event_key.strip() else None

    team = db.get(models.Team, normalized_team_key)
    resolved_team_key = team.team_key if team is not None else normalized_team_key
    resolved_team_number = int(team.team_number) if team is not None and team.team_number is not None else _team_number_from_team_key(normalized_team_key)
    resolved_nickname = team.nickname if team is not None else None
    local_team_missing_warning: str | None = None
    if team is None:
        # Keep intel endpoint resilient for teams that are valid on TBA/Statbotics but
        # have not been ingested into the local Team table yet.
        local_team_missing_warning = (
            f"Team {normalized_team_key} is not in local ingest yet; using external and fallback signals where available."
        )

    # ── Parallel phase: run DB queries + external API calls concurrently ──
    loop = asyncio.get_running_loop()
    tba_client = TBAClient() if settings.tba_auth_key.strip() else None
    bind = db.get_bind() if hasattr(db, "get_bind") else None
    thread_session_factory = (
        sessionmaker(
            bind=bind,
            autoflush=False,
            autocommit=False,
        )
        if bind is not None
        else None
    )

    def _thread_db():
        return thread_session_factory() if thread_session_factory is not None else db

    def _sync_registered_events():
        thread_db = _thread_db()
        try:
            return _team_registered_events(
                db=thread_db,
                team_key=normalized_team_key,
                preferred_year=preferred_year,
                fallback_year=fallback_year,
                tba=tba_client,
                primary_year=_event_year_from_key(normalized_event_key),
            )
        finally:
            if thread_db is not db and hasattr(thread_db, "close"):
                thread_db.close()

    def _sync_analysis():
        thread_db = _thread_db()
        try:
            return _load_team_breakdown_with_fallback(
                db=thread_db,
                team_key=normalized_team_key,
                event_key=normalized_event_key,
                allow_season_fallback=allow_season_fallback,
            )
        finally:
            if thread_db is not db and hasattr(thread_db, "close"):
                thread_db.close()

    def _sync_rating():
        thread_db = _thread_db()
        try:
            return _resolve_team_rating_context(
                db=thread_db,
                team_key=normalized_team_key,
                event_key=normalized_event_key,
                auto_heal_ratings=auto_heal_ratings,
            )
        finally:
            if thread_db is not db and hasattr(thread_db, "close"):
                thread_db.close()

    def _sync_auto_scout_profile():
        thread_db = _thread_db()
        try:
            return summarize_team_auto_scout_profile(
                db=thread_db,
                team_key=normalized_team_key,
                event_key=normalized_event_key,
                season_year=preferred_year,
            )
        except Exception:
            logger.exception("team_intel.auto_scout_profile_failed team_key=%s", normalized_team_key)
            return {"available": False, "team_key": normalized_team_key, "fields": {}, "sample_matches": 0}
        finally:
            if thread_db is not db and hasattr(thread_db, "close"):
                thread_db.close()

    def _sync_tba_context():
        if not include_tba:
            return {
                "enabled": False,
                "available": False,
                "event_status": None,
                "event_status_summary": None,
                "awards": [],
                "awards_year": None,
                "warnings": [],
            }
        return _load_tba_team_context(
            team_key=normalized_team_key,
            event_key=normalized_event_key,
            preferred_year=preferred_year,
            fallback_year=fallback_year,
        )

    async def _async_statbotics_context():
        if not include_statbotics:
            return {
                "enabled": False,
                "available": False,
                "team": None,
                "team_event": None,
                "team_year": None,
                "resolved_year": None,
                "warnings": [],
            }
        return await _load_statbotics_team_context(
            team_number=resolved_team_number,
            event_key=normalized_event_key,
            preferred_year=preferred_year,
            fallback_year=fallback_year,
        )

    # Run sync DB + TBA work in thread pool, Statbotics async — all concurrently
    (
        (selected_year, registered_events, registered_source),
        (analysis_payload, analysis_fallbacks, analysis_warnings),
        (rating_payload, rating_fallbacks, rating_warnings),
        auto_scout_profile,
        tba_context,
        statbotics_context,
    ) = await asyncio.gather(
        loop.run_in_executor(None, _sync_registered_events),
        loop.run_in_executor(None, _sync_analysis),
        loop.run_in_executor(None, _sync_rating),
        loop.run_in_executor(None, _sync_auto_scout_profile),
        loop.run_in_executor(None, _sync_tba_context),
        _async_statbotics_context(),
    )

    # ── Sequential post-processing (depends on parallel results) ──
    analysis_payload, estimated_average_fields = _enrich_analysis_with_rating_estimates(
        analysis_payload,
        rating_payload,
    )
    if estimated_average_fields:
        analysis_fallbacks.append(
            {
                "kind": "analysis_metric_estimates",
                "from": "missing_analysis_metrics",
                "to": "rating_model_fallback",
                "fields": estimated_average_fields,
                "reason": "sparse_analysis_signal",
            }
        )
        analysis_warnings.append(
            "Some scouting averages are temporarily model-derived because analyzed clip coverage is still sparse."
        )

    # Rank against the event the rating actually came from, not the event that
    # was asked for. A team with no rating at the selected event falls back to
    # their most recent one, and ranking that against a field they were never
    # in would be a fabricated baseline.
    #
    # Deliberately not gated on normalized_event_key: Team Center requests this
    # payload with no event at all, and a rating that arrives without its
    # position is the bare figure this work exists to remove.
    if bool(rating_payload.get("available")):
        rank_event_key = (
            rating_payload.get("context_event_key")
            or rating_payload.get("event_key")
            or normalized_event_key
        )
        if rank_event_key:
            rank_context = _event_rating_rank_context(
                db,
                event_key=str(rank_event_key),
                team_key=normalized_team_key,
            )
            if rank_context is not None:
                rating_payload["rank"] = rank_context["rank"]
                rating_payload["field_size"] = rank_context["field_size"]
                rating_payload["rank_event_key"] = str(rank_event_key)

    if normalized_event_key and bool(rating_payload.get("available")):
        rank_summary = (
            dict(tba_context.get("event_status_summary"))
            if isinstance(tba_context.get("event_status_summary"), dict)
            else {}
        )
        rank_value = _as_float(rank_summary.get("rank"))
        if rank_value is None:
            fallback_rank = _event_rating_rank(
                db,
                event_key=normalized_event_key,
                team_key=normalized_team_key,
            )
            if isinstance(fallback_rank, int):
                rank_summary["rank"] = fallback_rank
                rank_summary["source"] = "model_rating_fallback"
                tba_context["event_status_summary"] = rank_summary
                rating_fallbacks.append(
                    {
                        "kind": "tba_event_rank",
                        "from": "tba_missing",
                        "to": f"event_rating_rank:{fallback_rank}",
                        "event_key": normalized_event_key,
                        "reason": "tba_rank_unavailable",
                    }
                )
                analysis_warnings.append(
                    "TBA event rank was unavailable; using model rank fallback for now."
                )
    if not bool(rating_payload.get("available")):
        synthesized = _synthesize_rating_from_sparse_signals(
            event_key=normalized_event_key,
            analysis_payload=analysis_payload,
            statbotics_context=statbotics_context,
        )
        if synthesized is not None:
            rating_payload = synthesized
            rating_fallbacks.append(
                {
                    "kind": "event_rating",
                    "from": "missing",
                    "to": "sparse_external_fallback",
                    "event_key": normalized_event_key,
                    "reason": "no_precomputed_rating",
                }
            )
            rating_warnings.append(
                "No precomputed event rating was available; using sparse external fallback rating."
            )
            analysis_payload, synthesized_fields = _enrich_analysis_with_rating_estimates(
                analysis_payload,
                rating_payload,
            )
            if synthesized_fields:
                analysis_fallbacks.append(
                    {
                        "kind": "analysis_metric_estimates",
                        "from": "missing_analysis_metrics",
                        "to": "sparse_external_fallback",
                        "fields": synthesized_fields,
                        "reason": "no_precomputed_rating",
                    }
                )
    warnings = analysis_warnings + rating_warnings
    if local_team_missing_warning:
        warnings.append(local_team_missing_warning)
    warnings.extend(_freshness_warnings_from_analysis_payload(analysis_payload))
    warnings.extend([str(item) for item in tba_context.get("warnings", []) if isinstance(item, str)])
    warnings.extend([str(item) for item in statbotics_context.get("warnings", []) if isinstance(item, str)])
    warnings = _dedupe_warnings(warnings)
    data_coverage = _build_team_data_coverage_payload(
        analysis_payload=analysis_payload,
        rating_payload=rating_payload,
        tba_context=tba_context,
        statbotics_context=statbotics_context,
        fallbacks=(analysis_fallbacks + rating_fallbacks),
    )
    analysis_snapshot = _analysis_snapshot_from_breakdown(analysis_payload)
    analysis_snapshot["data_coverage"] = data_coverage
    analysis_snapshot["data_coverage_score_0_1"] = data_coverage.get("score_0_1")

    return {
        "ok": True,
        "team_key": normalized_team_key,
        "event_key": normalized_event_key,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "team": {
            "team_key": resolved_team_key,
            "team_number": resolved_team_number,
            "nickname": resolved_nickname,
        },
        "competitions": {
            "registration_year": selected_year,
            "registered_events_count": len(registered_events),
            "registered_events_source": registered_source,
            "registered_events": registered_events,
        },
        "analysis": analysis_snapshot,
        "rating": rating_payload,
        "auto_scout_profile": auto_scout_profile,
        "tba": tba_context,
        "statbotics": statbotics_context,
        "data_coverage": data_coverage,
        "fallbacks": analysis_fallbacks + rating_fallbacks,
        "warnings": warnings,
        "source_versions": {
            "scouting": "scouting.team_breakdown_v1",
            "tba": "tba.v3",
            "statbotics": "statbotics.v3",
        },
    }

async def _build_event_teams_intel_payload(
    *,
    db: Session,
    event_key: str,
    include_tba: bool,
    include_statbotics: bool,
    auto_heal_ratings: bool,
    include_season_fallback: bool,
    include_rating_details: bool,
    include_rating_signals: bool,
) -> dict[str, Any]:
    normalized_event_key = event_key.strip().lower()
    event = db.get(models.Event, normalized_event_key)
    source = "local"
    refresh_error: str | None = None
    event_refreshed, refreshed, refresh_error = _upsert_event_roster_if_missing(db, normalized_event_key)
    if refreshed:
        event = event_refreshed
        source = "remote_refreshed"
    elif refresh_error:
        source = "remote_refresh_failed"
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {normalized_event_key} not found")

    team_rows = (
        db.query(models.EventTeam, models.Team, models.TeamProfile)
        .join(models.Team, models.Team.team_key == models.EventTeam.team_key)
        .outerjoin(models.TeamProfile, models.TeamProfile.team_key == models.Team.team_key)
        .filter(models.EventTeam.event_key == normalized_event_key)
        .order_by(models.Team.team_number.asc())
        .all()
    )
    team_keys = [event_team.team_key for event_team, *_ in team_rows]

    rating_rows = (
        db.query(models.EventTeamRating)
        .filter(models.EventTeamRating.event_key == normalized_event_key)
        .all()
    )
    rating_by_team = {row.team_key: row for row in rating_rows}
    if auto_heal_ratings and team_keys and not settings.public_readonly_mode and not rating_by_team:
        try:
            recompute_event_ratings(db, normalized_event_key)
            rating_rows = (
                db.query(models.EventTeamRating)
                .filter(models.EventTeamRating.event_key == normalized_event_key)
                .all()
            )
            rating_by_team = {row.team_key: row for row in rating_rows}
            source = "local_auto_heal_ratings"
        except Exception as exc:
            logger.warning(
                "teams.event_intel rating recompute failed event=%s error=%s",
                normalized_event_key,
                sanitize_external_error(exc, default="refresh_failed"),
            )
    event_rating_rank_by_team: dict[str, int] = {}
    for idx, row in enumerate(
        sorted(
            rating_rows,
            key=lambda item: (-float(item.rating_0_100), str(item.team_key)),
        ),
        start=1,
    ):
        event_rating_rank_by_team[str(row.team_key)] = idx

    latest_rating_rows = (
        db.query(models.EventTeamRating, models.Event)
        .join(models.Event, models.Event.event_key == models.EventTeamRating.event_key)
        .filter(models.EventTeamRating.team_key.in_(team_keys) if team_keys else False)
        .order_by(models.Event.year.desc(), models.EventTeamRating.updated_at.desc())
        .all()
    )
    latest_rating_by_team: dict[str, tuple[models.EventTeamRating, str | None]] = {}
    for row, source_event in latest_rating_rows:
        if row.team_key not in latest_rating_by_team:
            latest_rating_by_team[row.team_key] = (row, source_event.event_key if source_event else None)
    latest_rating_rank_by_team: dict[str, int] = {}
    for idx, item in enumerate(
        sorted(
            latest_rating_by_team.items(),
            key=lambda pair: (-float(pair[1][0].rating_0_100), str(pair[0])),
        ),
        start=1,
    ):
        latest_rating_rank_by_team[str(item[0])] = idx

    findings_event_rows = (
        db.query(
            models.TeamMatchFinding.team_key,
            func.count(models.TeamMatchFinding.id).label("count"),
            func.max(models.Match.time).label("latest_match_time"),
        )
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .filter(models.TeamMatchFinding.event_key == normalized_event_key)
        .group_by(models.TeamMatchFinding.team_key)
        .all()
    )
    findings_event_by_team: dict[str, tuple[int, int | None]] = {
        str(team_key): (int(count or 0), int(latest_match_time) if isinstance(latest_match_time, int) else None)
        for team_key, count, latest_match_time in findings_event_rows
    }

    season_year = int(event.year)
    findings_season_rows = (
        db.query(
            models.TeamMatchFinding.team_key,
            func.count(models.TeamMatchFinding.id).label("count"),
            func.max(models.Match.time).label("latest_match_time"),
        )
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
        .filter(models.TeamMatchFinding.team_key.in_(team_keys) if team_keys else False)
        .filter(models.Event.year == season_year)
        .group_by(models.TeamMatchFinding.team_key)
        .all()
    )
    findings_season_by_team: dict[str, tuple[int, int | None]] = {
        str(team_key): (int(count or 0), int(latest_match_time) if isinstance(latest_match_time, int) else None)
        for team_key, count, latest_match_time in findings_season_rows
    }

    tba_statuses: dict[str, Any] = {}
    tba_warning: str | None = None
    if include_tba and settings.tba_auth_key.strip():
        try:
            raw_statuses = TBAClient().event_team_statuses(normalized_event_key)
            if isinstance(raw_statuses, dict):
                tba_statuses = {str(k).strip().lower(): v for k, v in raw_statuses.items()}
        except Exception as exc:
            tba_warning = sanitize_external_error(exc, default="team_statuses_request_failed")

    statbotics_by_team: dict[str, dict[str, Any] | None] = {}
    statbotics_errors: list[str] = []
    if include_statbotics:
        try:
            # One bulk request gives event EPA rows for all teams and is much cheaper
            # than N per-team requests.
            team_event_rows = await statbotics_client.get_team_events(
                event_key=normalized_event_key,
                limit=max(128, len(team_rows) * 2),
                metric="epa",
            )
            for item in team_event_rows:
                if not isinstance(item, dict):
                    continue
                team_number = _as_float(item.get("team"))
                if team_number is None:
                    continue
                team_key = _normalize_team_key(f"frc{int(team_number)}")
                statbotics_by_team[team_key] = item
        except Exception as exc:
            statbotics_errors.append(
                f"event:{normalized_event_key}: {sanitize_external_error(exc, default='request_failed')}"
            )

    teams_payload: list[dict[str, Any]] = []
    fallback_count = 0
    stale_data_count = 0
    low_coverage_count = 0
    coverage_scores: list[float] = []
    tba_rank_fallback_count = 0
    statbotics_rating_fallback_count = 0
    for _, team, profile in team_rows:
        event_analysis_count, event_latest_time = findings_event_by_team.get(team.team_key, (0, None))
        season_analysis_count, season_latest_time = findings_season_by_team.get(team.team_key, (0, None))
        using_season_fallback = (
            include_season_fallback and event_analysis_count <= 0 and season_analysis_count > 0
        )
        if using_season_fallback:
            fallback_count += 1
        match_time_for_freshness = season_latest_time if using_season_fallback else event_latest_time
        analyzed_matches_for_freshness = season_analysis_count if using_season_fallback else event_analysis_count
        season_scope = {
            "season_year": season_year,
            "active_season_year": _active_game_season_year(),
            "source": "event",
            "uses_active_season": season_year == _active_game_season_year(),
        }
        data_freshness = _build_data_freshness_payload(
            season_scope=season_scope,
            match_times=[match_time_for_freshness],
            analyzed_matches=int(analyzed_matches_for_freshness),
            raw_analyzed_matches=int(analyzed_matches_for_freshness),
            quality_gate_enabled=None,
            quality_gate_fallback_used=using_season_fallback,
        )
        if bool(data_freshness.get("is_outdated")):
            stale_data_count += 1

        rating_row = rating_by_team.get(team.team_key)
        rating_payload = None
        rating_details_source: dict[str, Any] = {}
        if rating_row is not None:
            rating_details_source = (
                rating_row.details_json if isinstance(rating_row.details_json, dict) else {}
            )
            rating_payload = _rating_preview(
                rating_row,
                source="event_rating",
                context_event_key=normalized_event_key,
                include_details=include_rating_details,
                include_signals=include_rating_signals,
            )
        else:
            latest_rating = latest_rating_by_team.get(team.team_key)
            if latest_rating is not None:
                latest_row, latest_event_key = latest_rating
                rating_details_source = (
                    latest_row.details_json if isinstance(latest_row.details_json, dict) else {}
                )
                rating_payload = _rating_preview(
                    latest_row,
                    source="latest_event_fallback",
                    context_event_key=latest_event_key,
                    include_details=include_rating_details,
                    include_signals=include_rating_signals,
                )
            else:
                rating_payload = {"available": False, "source": "none", "context_event_key": normalized_event_key}

        tba_status_summary = _extract_tba_rank_record(
            tba_statuses.get(team.team_key) if include_tba else None
        )
        if _as_float(tba_status_summary.get("rank")) is None:
            fallback_rank = event_rating_rank_by_team.get(team.team_key)
            fallback_source = "model_rating_fallback"
            if fallback_rank is None:
                fallback_rank = latest_rating_rank_by_team.get(team.team_key)
                fallback_source = "latest_rating_fallback"
            if isinstance(fallback_rank, int):
                tba_status_summary["rank"] = fallback_rank
                tba_status_summary["source"] = fallback_source
                tba_rank_fallback_count += 1
        statbotics_payload = statbotics_by_team.get(team.team_key)
        norm_epa = statbotics_payload.get("norm_epa") if isinstance(statbotics_payload, dict) else {}
        event_epa = statbotics_payload.get("epa") if isinstance(statbotics_payload, dict) else {}
        event_epa_total_points = (
            _as_float((event_epa.get("total_points") or {}).get("mean"))
            if isinstance(event_epa, dict)
            else None
        )
        if event_epa_total_points is None and isinstance(event_epa, dict):
            event_breakdown = event_epa.get("breakdown")
            if isinstance(event_breakdown, dict):
                event_epa_total_points = _as_float(event_breakdown.get("total_points"))
        event_epa_norm = _as_float(event_epa.get("norm")) if isinstance(event_epa, dict) else None
        event_breakdown = event_epa.get("breakdown") if isinstance(event_epa, dict) else {}
        event_epa_auto = (
            _as_float(event_breakdown.get("auto_points"))
            if isinstance(event_breakdown, dict)
            else None
        )
        event_epa_teleop = (
            _as_float(event_breakdown.get("teleop_points"))
            if isinstance(event_breakdown, dict)
            else None
        )
        event_epa_endgame = (
            _as_float(event_breakdown.get("endgame_points"))
            if isinstance(event_breakdown, dict)
            else None
        )
        rating_raw_features = (
            rating_details_source.get("raw_features")
            if isinstance(rating_details_source, dict)
            and isinstance(rating_details_source.get("raw_features"), dict)
            else {}
        )
        rating_epa_fallback = _as_float(rating_raw_features.get("statbotics_norm_epa"))
        if not isinstance(statbotics_payload, dict) and rating_epa_fallback is not None:
            statbotics_rating_fallback_count += 1
        norm_epa_current = (
            event_epa_norm
            if event_epa_norm is not None
            else (
                norm_epa.get("current")
                if isinstance(norm_epa, dict) and _as_float(norm_epa.get("current")) is not None
                else rating_epa_fallback
            )
        )
        norm_epa_recent = (
            norm_epa.get("recent")
            if isinstance(norm_epa, dict) and _as_float(norm_epa.get("recent")) is not None
            else norm_epa_current
        )
        team_data_coverage = _build_event_team_data_coverage_payload(
            event_analysis_count=int(event_analysis_count),
            season_analysis_count=int(season_analysis_count),
            uses_season_fallback=bool(using_season_fallback),
            data_freshness=data_freshness,
            rating_payload=rating_payload if isinstance(rating_payload, dict) else {},
            tba_status_summary=tba_status_summary if isinstance(tba_status_summary, dict) else {},
            statbotics_available=(isinstance(statbotics_payload, dict) or rating_epa_fallback is not None),
        )
        coverage_score = float(team_data_coverage.get("score_0_1") or 0.0)
        coverage_scores.append(coverage_score)
        if coverage_score < 0.45:
            low_coverage_count += 1
        teams_payload.append(
            {
                "team_key": team.team_key,
                "team_number": team.team_number,
                "nickname": team.nickname,
                "region": _region_label(profile.state_prov if profile else None, profile.country if profile else None),
                "state_prov": profile.state_prov if profile else None,
                "country": profile.country if profile else None,
                "analysis": {
                    "event_matches_analyzed": int(event_analysis_count),
                    "season_matches_analyzed": int(season_analysis_count),
                    "uses_season_fallback": bool(using_season_fallback),
                    "data_freshness": data_freshness,
                },
                "data_coverage": team_data_coverage,
                "rating": rating_payload,
                "tba": {
                    "available": include_tba and settings.tba_auth_key.strip() != "",
                    "event_status_summary": tba_status_summary,
                },
                "statbotics": {
                    "available": isinstance(statbotics_payload, dict) or rating_epa_fallback is not None,
                    "source": (
                        "statbotics_api"
                        if isinstance(statbotics_payload, dict)
                        else ("rating_model_fallback" if rating_epa_fallback is not None else "none")
                    ),
                    "event_epa_points": event_epa_total_points,
                    "event_epa_norm": event_epa_norm,
                    "event_epa_auto": event_epa_auto,
                    "event_epa_teleop": event_epa_teleop,
                    "event_epa_endgame": event_epa_endgame,
                    "norm_epa_current": norm_epa_current,
                    "norm_epa_recent": norm_epa_recent,
                    "active": statbotics_payload.get("active") if isinstance(statbotics_payload, dict) else None,
                },
            }
        )

    warnings: list[str] = []
    if refresh_error:
        warnings.append(f"Roster refresh warning: {refresh_error}")
    if tba_warning:
        warnings.append(f"TBA statuses warning: {tba_warning}")
    if tba_rank_fallback_count > 0:
        warnings.append(
            f"TBA rank unavailable for {tba_rank_fallback_count} team(s); using model rank fallback."
        )
    if statbotics_errors:
        warnings.append(f"Statbotics warnings for {len(statbotics_errors)} teams.")
    if statbotics_rating_fallback_count > 0:
        warnings.append(
            f"Statbotics EPA unavailable for {statbotics_rating_fallback_count} team(s); using rating-model EPA fallback."
        )
    if stale_data_count > 0:
        warnings.append(
            f"{stale_data_count} team(s) have stale/sparse analysis data; refresh ingest for better coverage."
        )
    warnings = _dedupe_warnings(warnings)

    return {
        "ok": True,
        "event_key": normalized_event_key,
        "event_name": event.name,
        "season_year": season_year,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "teams_count": len(teams_payload),
        "teams_with_event_analysis": int(sum(1 for row in teams_payload if row["analysis"]["event_matches_analyzed"] > 0)),
        "teams_with_fallback_analysis": int(fallback_count),
        "teams_with_event_rating": int(sum(1 for row in teams_payload if row["rating"]["source"] == "event_rating")),
        "teams_with_stale_data": int(stale_data_count),
        "teams_with_low_coverage": int(low_coverage_count),
        "teams_with_tba_rank_fallback": int(tba_rank_fallback_count),
        "teams_with_statbotics_fallback": int(statbotics_rating_fallback_count),
        "average_data_coverage_score_0_1": round(
            (sum(coverage_scores) / len(coverage_scores)) if coverage_scores else 0.0,
            4,
        ),
        "teams": teams_payload,
        "warnings": warnings,
    }

# ── Include sub-routers ──────────────────────────────────────────────────
# The sub-modules use ``import app.api.routes_teams as _rt`` to call helpers
# defined above.  Python resolves the circular reference because all helpers
# are defined before the sub-module imports below execute.

from app.api.teams.intel import router as _intel_router  # noqa: E402
from app.api.teams.media import router as _media_router  # noqa: E402
from app.api.teams.stats import router as _stats_router  # noqa: E402

router.include_router(_intel_router)
router.include_router(_media_router)
router.include_router(_stats_router)

# ── Register intel builders with the service layer ───────────────────────
# This allows services (like intel_snapshots) to call these builders
# without importing from the API layer directly.
register_event_teams_intel_builder(_build_event_teams_intel_payload)
register_team_intel_builder(_build_team_intel_payload)
