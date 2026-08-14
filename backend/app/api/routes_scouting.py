from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timezone
import logging
import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin_access, require_write_access, sanitize_external_error
from app.db import models
from app.db.session import get_db
from app.api.schemas import RatingsResponse, TeamsHistoryResponse
from app.services.scouting_rooms.helpers import (
    RECENT_STATS_MATCH_WINDOW,
    apply_quality_gate_to_finding_rows as _apply_quality_gate_to_finding_rows,
    clamp_metric as _clamp_metric,
    compute_averages as _compute_averages,
    compute_climb_sources as _compute_climb_sources,
    dedupe_finding_rows_by_match as _dedupe_finding_rows_by_match,
    delta_or_none as _delta_or_none,
    extract_perimeter_context as _extract_perimeter_context,
    infer_climb_from_match_events as _infer_climb_from_match_events,
    mean_metric as _mean_metric,
    metric_coverage_payload as _metric_coverage_payload,
    pearson_correlation as _pearson_correlation,
    rating_display_value as _rating_display_value,
    rating_float as _rating_float,
    rows_for_metric_coverage as _rows_for_metric_coverage,
    safe_div as _safe_div,
    safe_int as _safe_int,
    serialize_finding as _serialize_finding,
    serialize_rating_row as _serialize_rating_row,
    sigmoid as _sigmoid,
    signal_labels as _signal_labels,
)

from app.services.quality_gate import quality_gate_config_payload
from app.services.ml.shadow import (
    TEAM_STRENGTH_MODEL_KEY,
    build_team_strength_shadow_input_from_rating_row,
    infer_team_strength_shadow_from_rows,
)
from app.services.ratings.model import (
    MODEL_VERSION,
    recompute_event_ratings,
)
from app.services.ratings.snapshots import compute_event_rating_trends
from app.services.scouting_rooms.scope import (
    active_game_season_year as _active_game_season_year,
    build_data_freshness_payload as _build_data_freshness_payload,
    event_year_from_key as _event_year_from_key,
)
from app.services.ml.synergy import SYNERGY_MODEL_VERSION, precompute_event_synergy
from app.services.team_utils import (
    region_label as _region_label,
    team_number_from_team_key as _team_number_from_team_key,
)
from app.services.utils import _as_float
from app.tba.client import TBAClient

router = APIRouter(prefix="/scouting", tags=["scouting"])
logger = logging.getLogger(__name__)



def _resolve_team_season_scope(
    db: Session,
    team_key: str,
    event_key: str | None = None,
) -> dict:
    active_season_year = _active_game_season_year()
    if event_key:
        event = db.get(models.Event, event_key)
        scoped_year = (
            int(event.year)
            if event and isinstance(event.year, int)
            else (_event_year_from_key(event_key) or active_season_year)
        )
        return {
            "season_year": scoped_year,
            "active_season_year": active_season_year,
            "source": "event",
            "uses_active_season": scoped_year == active_season_year,
        }

    has_active_season_data = (
        db.query(models.TeamMatchFinding.id)
        .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
        .filter(
            models.TeamMatchFinding.team_key == team_key,
            models.Event.year == active_season_year,
        )
        .first()
        is not None
    )
    if has_active_season_data:
        return {
            "season_year": active_season_year,
            "active_season_year": active_season_year,
            "source": "active_season",
            "uses_active_season": True,
        }

    latest_available_year = (
        db.query(func.max(models.Event.year))
        .join(models.TeamMatchFinding, models.TeamMatchFinding.event_key == models.Event.event_key)
        .filter(models.TeamMatchFinding.team_key == team_key)
        .scalar()
    )
    if isinstance(latest_available_year, int):
        return {
            "season_year": int(latest_available_year),
            "active_season_year": active_season_year,
            "source": "latest_available",
            "uses_active_season": int(latest_available_year) == active_season_year,
        }

    return {
        "season_year": active_season_year,
        "active_season_year": active_season_year,
        "source": "active_season_default",
        "uses_active_season": True,
    }


def _team_breakdown_rating_row(
    db: Session,
    *,
    team_key: str,
    event_key: str | None,
    season_year: int,
) -> tuple[models.EventTeamRating | None, str]:
    if event_key:
        event_row = (
            db.query(models.EventTeamRating)
            .filter(
                models.EventTeamRating.event_key == event_key,
                models.EventTeamRating.team_key == team_key,
            )
            .one_or_none()
        )
        if event_row is not None:
            return event_row, "event_rating"

    season_row = (
        db.query(models.EventTeamRating, models.Event)
        .join(models.Event, models.Event.event_key == models.EventTeamRating.event_key)
        .filter(
            models.EventTeamRating.team_key == team_key,
            models.Event.year == int(season_year),
        )
        .order_by(models.EventTeamRating.updated_at.desc(), models.EventTeamRating.event_key.asc())
        .first()
    )
    if season_row is not None:
        return season_row[0], "season_latest_rating"
    return None, "rating_context_unavailable"


def _team_breakdown_ml_projection(
    db: Session,
    *,
    team_key: str,
    event_key: str | None,
    season_year: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": False,
        "model_key": TEAM_STRENGTH_MODEL_KEY,
        "model_version": None,
        "source": None,
        "source_event_key": None,
        "predicted_active_bps": None,
        "predicted_scores_per_min": None,
        "reason": "disabled",
        "detail": None,
        "input": None,
    }
    if not bool(getattr(settings, "ml_shadow_enabled", False)):
        return payload

    rating_row, rating_source = _team_breakdown_rating_row(
        db,
        team_key=team_key,
        event_key=event_key,
        season_year=season_year,
    )
    payload["source"] = rating_source
    if rating_row is None:
        payload["reason"] = "rating_context_unavailable"
        return payload

    ml_input = build_team_strength_shadow_input_from_rating_row(rating_row)
    payload["source_event_key"] = rating_row.event_key
    payload["input"] = {
        "rating_0_100": _as_float(ml_input.get("rating_0_100")),
        "throughput_0_100": _as_float(ml_input.get("throughput_0_100")),
        "confidence_0_1": _as_float(ml_input.get("confidence_0_1")),
        "match_count": int(_safe_int(ml_input.get("match_count")) or 0),
        "coverage_factor_0_1": _as_float(ml_input.get("coverage_factor_0_1")),
    }

    try:
        inference = infer_team_strength_shadow_from_rows(db, rows=[ml_input])
    except Exception as exc:
        payload["reason"] = "inference_failed"
        payload["detail"] = str(exc)
        return payload

    payload["model_version"] = inference.get("model_version")
    if not bool(inference.get("ok")):
        payload["reason"] = str(inference.get("reason") or "prediction_failed")
        payload["detail"] = inference.get("detail")
        return payload

    predictions = inference.get("predictions") if isinstance(inference.get("predictions"), list) else []
    prediction = predictions[0] if predictions and isinstance(predictions[0], dict) else {}
    predicted_bps = _as_float(prediction.get("strength_active_bps_pred"))
    if predicted_bps is None:
        payload["reason"] = "prediction_missing"
        return payload

    payload["available"] = True
    payload["reason"] = "ok"
    payload["predicted_active_bps"] = round(float(predicted_bps), 6)
    payload["predicted_scores_per_min"] = round(float(predicted_bps) * 60.0, 3)
    return payload


def _upsert_event_roster_if_missing(
    db: Session,
    event_key: str,
    event: models.Event | None,
) -> tuple[models.Event | None, bool, str | None]:
    if settings.public_readonly_mode:
        return event, False, "public_mode"
    if settings.tba_auth_key.strip() == "":
        return event, False, "tba_not_configured"

    try:
        tba = TBAClient()
        event_payload = tba.event(event_key)
        teams_payload = tba.event_teams(event_key)
    except Exception as exc:
        return event, False, sanitize_external_error(exc, default="TBA roster fetch failed.")

    if not isinstance(event_payload, dict):
        return event, False, "invalid_event_payload"
    if not isinstance(teams_payload, list):
        return event, False, "invalid_team_payload"

    event_key_payload = str(event_payload.get("key") or event_key).strip().lower()
    event_name = str(event_payload.get("name") or event_key).strip() or event_key
    year_raw = event_payload.get("year")
    year = _event_year_from_key(event_key_payload) or _active_game_season_year()
    if isinstance(year_raw, int):
        year = year_raw
    elif isinstance(year_raw, float):
        year = int(year_raw)
    elif isinstance(year_raw, str):
        with suppress(ValueError):
            year = int(year_raw.strip())

    db.merge(
        models.Event(
            event_key=event_key_payload,
            name=event_name,
            year=year,
        )
    )
    db.merge(
        models.EventProfile(
            event_key=event_key_payload,
            city=event_payload.get("city"),
            state_prov=event_payload.get("state_prov"),
            country=event_payload.get("country"),
        )
    )

    normalized_team_keys: list[str] = []
    for team_payload in teams_payload:
        if not isinstance(team_payload, dict):
            continue
        team_key_raw = team_payload.get("key")
        if not isinstance(team_key_raw, str) or not team_key_raw.strip():
            continue
        team_key = team_key_raw.strip().lower()
        normalized_team_keys.append(team_key)
        team_number = (
            int(team_payload.get("team_number"))
            if isinstance(team_payload.get("team_number"), int)
            else _team_number_from_team_key(team_key)
        )
        db.merge(
            models.Team(
                team_key=team_key,
                team_number=team_number,
                nickname=team_payload.get("nickname"),
            )
        )
        db.merge(
            models.TeamProfile(
                team_key=team_key,
                city=team_payload.get("city"),
                state_prov=team_payload.get("state_prov"),
                country=team_payload.get("country"),
            )
        )

    try:
        db.flush()
    except Exception as exc:
        db.rollback()
        return event, False, sanitize_external_error(exc, default="Roster team upsert failed.")

    for team_key in normalized_team_keys:
        db.merge(
            models.EventTeam(
                event_key=event_key_payload,
                team_key=team_key,
            )
        )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        return event, False, sanitize_external_error(exc, default="Roster upsert failed.")

    refreshed_event = db.get(models.Event, event_key_payload) or db.get(models.Event, event_key)
    return refreshed_event, True, None























def _query_event_rating_rows(
    db: Session,
    event_key: str,
) -> list[tuple[models.EventTeamRating, models.Team | None]]:
    return (
        db.query(models.EventTeamRating, models.Team)
        .outerjoin(models.Team, models.Team.team_key == models.EventTeamRating.team_key)
        .filter(models.EventTeamRating.event_key == event_key)
        .order_by(models.EventTeamRating.rating_0_100.desc(), models.EventTeamRating.team_key.asc())
        .all()
    )











def _qa_match_alliance_team_keys(match_payload: dict[str, Any], alliance: str) -> list[str]:
    alliances = match_payload.get("alliances")
    if not isinstance(alliances, dict):
        return []
    payload = alliances.get(alliance)
    if not isinstance(payload, dict):
        return []
    values = payload.get("team_keys")
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for item in values:
        if isinstance(item, str) and item.strip():
            result.append(item.strip().lower())
    return result


def _qa_match_score(match_payload: dict[str, Any], alliance: str) -> int | None:
    alliances = match_payload.get("alliances")
    if not isinstance(alliances, dict):
        return None
    payload = alliances.get(alliance)
    if not isinstance(payload, dict):
        return None
    score = _safe_int(payload.get("score"))
    if isinstance(score, int) and score >= 0:
        return score
    return None


def _qa_winner_alliance(match_payload: dict[str, Any]) -> str | None:
    winner_raw = match_payload.get("winning_alliance")
    winner = winner_raw.strip().lower() if isinstance(winner_raw, str) else ""
    if winner in {"red", "blue"}:
        return winner

    red_score = _qa_match_score(match_payload, "red")
    blue_score = _qa_match_score(match_payload, "blue")
    if isinstance(red_score, int) and isinstance(blue_score, int):
        if red_score == blue_score:
            return "tie"
        return "red" if red_score > blue_score else "blue"
    return None


def _qa_is_match_completed(match_payload: dict[str, Any]) -> bool:
    winner = _qa_winner_alliance(match_payload)
    if winner in {"red", "blue"}:
        return True
    red_score = _qa_match_score(match_payload, "red")
    blue_score = _qa_match_score(match_payload, "blue")
    return isinstance(red_score, int) and isinstance(blue_score, int)


def _build_event_match_truth_rows(db: Session, event_key: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    source = "none"
    detail: str | None = None

    if settings.tba_auth_key.strip():
        try:
            payload = TBAClient().event_matches(event_key)
            if isinstance(payload, list):
                for match_payload in payload:
                    if not isinstance(match_payload, dict):
                        continue
                    if not _qa_is_match_completed(match_payload):
                        continue
                    match_key_raw = match_payload.get("key")
                    match_key = str(match_key_raw).strip().lower() if isinstance(match_key_raw, str) else ""
                    if not match_key:
                        continue
                    red_team_keys = _qa_match_alliance_team_keys(match_payload, "red")
                    blue_team_keys = _qa_match_alliance_team_keys(match_payload, "blue")
                    if len(red_team_keys) < 3 or len(blue_team_keys) < 3:
                        continue
                    rows.append(
                        {
                            "match_key": match_key,
                            "red_team_keys": red_team_keys,
                            "blue_team_keys": blue_team_keys,
                            "winner_alliance": _qa_winner_alliance(match_payload),
                            "red_score": _qa_match_score(match_payload, "red"),
                            "blue_score": _qa_match_score(match_payload, "blue"),
                            "source": "tba",
                        }
                    )
            if rows:
                source = "tba"
        except Exception as exc:  # pragma: no cover - defensive fallback path
            detail = sanitize_external_error(exc, default="TBA matches fetch failed.")
            source = "tba_unavailable"

    if rows:
        return {
            "source": source,
            "detail": detail,
            "rows": rows,
        }

    teams_by_match_alliance: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"red": [], "blue": []})
    match_team_rows = (
        db.query(models.MatchTeam)
        .filter(models.MatchTeam.event_key == event_key)
        .all()
    )
    for row in match_team_rows:
        alliance = str(row.alliance or "").strip().lower()
        if alliance not in {"red", "blue"}:
            continue
        if isinstance(row.team_key, str) and row.team_key.strip():
            teams_by_match_alliance[row.match_key][alliance].append(row.team_key.strip().lower())

    official_event_rows = (
        db.query(models.MatchEvent)
        .filter(
            models.MatchEvent.event_key == event_key,
            models.MatchEvent.team_key.isnot(None),
            models.MatchEvent.event_type.in_(
                ["auto_points_scored", "teleop_fuel_score_success", "climb_success", "climb_attempt"]
            ),
        )
        .all()
    )

    points_by_team_match: dict[tuple[str, str], float] = defaultdict(float)
    for row in official_event_rows:
        meta = row.meta if isinstance(row.meta, dict) else {}
        source_token = str(meta.get("source") or "").strip().lower()
        if not source_token.startswith("tba_score_breakdown"):
            continue
        team_key = str(row.team_key or "").strip().lower()
        if not team_key:
            continue
        official_points = _rating_float(meta.get("official_points"))
        if official_points is None:
            if row.event_type == "climb_success":
                official_points = 1.0
            else:
                continue
        if official_points <= 0.0:
            continue
        points_by_team_match[(row.match_key, team_key)] += float(official_points)

    for match_key, alliance_payload in teams_by_match_alliance.items():
        red_team_keys = sorted(alliance_payload.get("red", []))
        blue_team_keys = sorted(alliance_payload.get("blue", []))
        if len(red_team_keys) < 3 or len(blue_team_keys) < 3:
            continue
        red_score_raw = sum(points_by_team_match.get((match_key, team_key), 0.0) for team_key in red_team_keys)
        blue_score_raw = sum(points_by_team_match.get((match_key, team_key), 0.0) for team_key in blue_team_keys)
        if red_score_raw <= 0.0 and blue_score_raw <= 0.0:
            continue
        red_score = int(round(red_score_raw))
        blue_score = int(round(blue_score_raw))
        winner = "tie"
        if red_score > blue_score:
            winner = "red"
        elif blue_score > red_score:
            winner = "blue"
        rows.append(
            {
                "match_key": match_key,
                "red_team_keys": red_team_keys,
                "blue_team_keys": blue_team_keys,
                "winner_alliance": winner,
                "red_score": red_score,
                "blue_score": blue_score,
                "source": "score_breakdown_local",
            }
        )

    return {
        "source": "score_breakdown_local" if rows else source,
        "detail": detail,
        "rows": rows,
    }





def _rating_row_components(row: models.EventTeamRating) -> dict[str, float | None]:
    details = row.details_json if isinstance(row.details_json, dict) else {}
    subscores = details.get("subscores") if isinstance(details.get("subscores"), dict) else {}
    model_components = details.get("model_components") if isinstance(details.get("model_components"), dict) else {}
    return {
        "rating": _rating_display_value(row.rating_0_100, row.model_version),
        "confidence": _rating_float(row.confidence_0_1),
        "results_anchor": _rating_float(row.results_anchor),
        "performance": _rating_float(model_components.get("performance")),
        "driver_skill": _rating_display_value(row.driver_skill_0_100, row.model_version),
        "robot_level": _rating_display_value(row.robot_level_0_100, row.model_version),
        "manual_points_impact": _rating_float(subscores.get("manual_points_impact")),
        "rp_contribution": _rating_float(subscores.get("rp_contribution")),
        "auto_contribution": _rating_float(subscores.get("auto_contribution")),
        "anti_defense": _rating_float(subscores.get("anti_defense")),
        "defense_presence": _rating_float(subscores.get("defense_presence")),
        "endgame": _rating_float(row.endgame),
        "consistency": _rating_float(row.consistency),
        "penalty_discipline": _rating_float(subscores.get("penalty_discipline")),
    }


def _compose_shadow_rating(components: dict[str, float | None], profile: dict[str, Any]) -> float | None:
    raw_rating = components.get("rating")
    if profile.get("use_stored_rating"):
        return raw_rating

    weights = profile.get("weights")
    if not isinstance(weights, dict):
        return raw_rating
    weighted_sum = 0.0
    total_weight = 0.0
    for key, raw_weight in weights.items():
        if not isinstance(raw_weight, (int, float)):
            continue
        weight = float(raw_weight)
        if weight <= 0.0:
            continue
        value = components.get(str(key))
        if value is None:
            continue
        weighted_sum += weight * float(value)
        total_weight += weight
    if total_weight <= 1e-9:
        return raw_rating

    base_score = weighted_sum / total_weight
    penalty_discipline = components.get("penalty_discipline")
    penalty_scale = float(profile.get("penalty_scale", 0.0) or 0.0)
    if penalty_discipline is not None and penalty_scale > 0.0:
        penalty_risk = _clamp_metric((100.0 - float(penalty_discipline)) / 100.0, 0.0, 1.0)
        base_score -= penalty_risk * penalty_scale

    confidence = _clamp_metric(float(components.get("confidence") or 0.0), 0.0, 1.0)
    confidence_floor = _clamp_metric(float(profile.get("confidence_floor", 0.0) or 0.0), 0.0, 0.95)
    effective_conf = confidence_floor + ((1.0 - confidence_floor) * confidence)
    return _clamp_metric(50.0 + (effective_conf * (base_score - 50.0)), 0.0, 100.0)


def _team_score_profiles(after_rows: list[tuple[models.EventTeamRating, models.Team | None]]) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = [
        {
            "name": "current_rating",
            "label": "Current Rating",
            "use_stored_rating": True,
            "synergy_weight": 0.0,
        },
        {
            "name": "balanced_formula",
            "label": "Balanced Formula",
            "weights": {
                "results_anchor": 0.21,
                "performance": 0.29,
                "driver_skill": 0.16,
                "robot_level": 0.11,
                "manual_points_impact": 0.09,
                "rp_contribution": 0.05,
                "auto_contribution": 0.09,
                "anti_defense": 0.06,
            },
            "penalty_scale": 2.8,
            "confidence_floor": 0.0,
            "synergy_weight": 0.0,
        },
        {
            "name": "auto_priority",
            "label": "Auto Priority",
            "weights": {
                "results_anchor": 0.18,
                "performance": 0.24,
                "driver_skill": 0.13,
                "robot_level": 0.09,
                "manual_points_impact": 0.08,
                "rp_contribution": 0.05,
                "auto_contribution": 0.17,
                "anti_defense": 0.06,
            },
            "penalty_scale": 2.6,
            "confidence_floor": 0.0,
            "synergy_weight": 0.2,
        },
        {
            "name": "playoff_sharp",
            "label": "Playoff Sharp",
            "weights": {
                "results_anchor": 0.16,
                "performance": 0.20,
                "driver_skill": 0.12,
                "robot_level": 0.10,
                "manual_points_impact": 0.07,
                "rp_contribution": 0.04,
                "auto_contribution": 0.12,
                "anti_defense": 0.14,
                "defense_presence": 0.06,
                "endgame": 0.09,
                "consistency": 0.08,
            },
            "penalty_scale": 3.2,
            "confidence_floor": 0.08,
            "synergy_weight": 0.6,
        },
        {
            "name": "consistency_safe",
            "label": "Consistency Safe",
            "weights": {
                "results_anchor": 0.19,
                "performance": 0.22,
                "driver_skill": 0.12,
                "robot_level": 0.09,
                "manual_points_impact": 0.06,
                "rp_contribution": 0.05,
                "auto_contribution": 0.09,
                "anti_defense": 0.07,
                "consistency": 0.11,
                "penalty_discipline": 0.08,
            },
            "penalty_scale": 2.4,
            "confidence_floor": 0.05,
            "synergy_weight": 0.2,
        },
    ]

    for profile in profiles:
        score_by_team: dict[str, float] = {}
        for row, _ in after_rows:
            components = _rating_row_components(row)
            score = _compose_shadow_rating(components, profile)
            if score is None:
                continue
            score_by_team[row.team_key] = float(score)
        profile["team_score_by_key"] = score_by_team
    return profiles


def _rank_stability_snapshot(
    before_rank_by_team: dict[str, int],
    after_rank_by_team: dict[str, int],
    *,
    top_window: int = 8,
) -> dict[str, Any]:
    common_keys = sorted(set(before_rank_by_team.keys()) & set(after_rank_by_team.keys()))
    if not common_keys:
        return {
            "teams_compared": 0,
            "rank_spearman_rho": None,
            "mean_abs_rank_shift": None,
            "max_abs_rank_shift": None,
            "top_window": int(top_window),
            "top_window_overlap_count": 0,
            "top_window_overlap_0_1": None,
        }

    shifts = [
        abs(int(after_rank_by_team[team_key]) - int(before_rank_by_team[team_key]))
        for team_key in common_keys
    ]
    n = len(common_keys)
    d2_sum = sum(
        (int(after_rank_by_team[team_key]) - int(before_rank_by_team[team_key])) ** 2
        for team_key in common_keys
    )
    rho = 1.0 - ((6.0 * float(d2_sum)) / float(n * ((n * n) - 1))) if n > 1 else None

    top_before = {
        team_key
        for team_key, rank in before_rank_by_team.items()
        if isinstance(rank, int) and rank > 0 and rank <= top_window
    }
    top_after = {
        team_key
        for team_key, rank in after_rank_by_team.items()
        if isinstance(rank, int) and rank > 0 and rank <= top_window
    }
    overlap = top_before & top_after
    overlap_rate = _safe_div(float(len(overlap)), float(max(1, len(top_before | top_after))))

    return {
        "teams_compared": int(n),
        "rank_spearman_rho": round(float(rho), 5) if isinstance(rho, float) else None,
        "mean_abs_rank_shift": round(float(_mean_metric([float(value) for value in shifts]) or 0.0), 4),
        "max_abs_rank_shift": int(max(shifts) if shifts else 0),
        "top_window": int(top_window),
        "top_window_overlap_count": int(len(overlap)),
        "top_window_overlap_0_1": round(float(overlap_rate), 5) if isinstance(overlap_rate, float) else None,
    }


def _signal_quality_snapshot(after_rows: list[tuple[models.EventTeamRating, models.Team | None]]) -> dict[str, Any]:
    team_count = len(after_rows)
    teams_with_pros = 0
    teams_with_cons = 0
    teams_with_both = 0
    pros_counts: list[float] = []
    cons_counts: list[float] = []
    confidence_values: list[float] = []
    total_signals = 0
    signals_with_evidence = 0
    top_pro_labels: dict[str, int] = defaultdict(int)
    top_con_labels: dict[str, int] = defaultdict(int)

    for row, _ in after_rows:
        pros = row.pros_json if isinstance(row.pros_json, list) else []
        cons = row.cons_json if isinstance(row.cons_json, list) else []
        pros_count = len([item for item in pros if isinstance(item, dict)])
        cons_count = len([item for item in cons if isinstance(item, dict)])
        pros_counts.append(float(pros_count))
        cons_counts.append(float(cons_count))
        if pros_count > 0:
            teams_with_pros += 1
        if cons_count > 0:
            teams_with_cons += 1
        if pros_count > 0 and cons_count > 0:
            teams_with_both += 1

        for bucket, label_counts in ((pros, top_pro_labels), (cons, top_con_labels)):
            for item in bucket:
                if not isinstance(item, dict):
                    continue
                total_signals += 1
                label = item.get("label")
                if isinstance(label, str) and label.strip():
                    label_counts[label.strip()] += 1
                confidence = _rating_float(item.get("signal_confidence_0_1"))
                if confidence is not None:
                    confidence_values.append(confidence)
                evidence = item.get("evidence")
                if isinstance(evidence, list) and evidence:
                    signals_with_evidence += 1

    evidence_rate = _safe_div(float(signals_with_evidence), float(max(1, total_signals)))
    teams_with_both_rate = _safe_div(float(teams_with_both), float(max(1, team_count)))
    avg_signal_conf = _mean_metric(confidence_values)

    top_pro = sorted(top_pro_labels.items(), key=lambda item: (-item[1], item[0]))[:8]
    top_con = sorted(top_con_labels.items(), key=lambda item: (-item[1], item[0]))[:8]

    return {
        "teams_count": int(team_count),
        "teams_with_pros": int(teams_with_pros),
        "teams_with_cons": int(teams_with_cons),
        "teams_with_both": int(teams_with_both),
        "teams_with_both_0_1": round(float(teams_with_both_rate), 5) if isinstance(teams_with_both_rate, float) else None,
        "avg_pros_per_team": round(float(_mean_metric(pros_counts) or 0.0), 4),
        "avg_cons_per_team": round(float(_mean_metric(cons_counts) or 0.0), 4),
        "total_signals": int(total_signals),
        "signals_with_evidence": int(signals_with_evidence),
        "signal_evidence_coverage_0_1": round(float(evidence_rate), 5) if isinstance(evidence_rate, float) else None,
        "avg_signal_confidence_0_1": round(float(avg_signal_conf), 5) if isinstance(avg_signal_conf, float) else None,
        "top_pro_labels": [{"label": label, "count": int(count)} for label, count in top_pro],
        "top_con_labels": [{"label": label, "count": int(count)} for label, count in top_con],
    }


def _evaluate_profile_against_truth(
    *,
    truth_rows: list[dict[str, Any]],
    score_by_team: dict[str, float],
    synergy_points_by_match_alliance: dict[tuple[str, str], float],
    synergy_weight: float,
    scale_candidates: list[float],
) -> dict[str, Any]:
    rows_scored: list[dict[str, Any]] = []
    skipped_missing = 0
    for row in truth_rows:
        red_team_keys = row.get("red_team_keys") if isinstance(row.get("red_team_keys"), list) else []
        blue_team_keys = row.get("blue_team_keys") if isinstance(row.get("blue_team_keys"), list) else []
        if len(red_team_keys) < 3 or len(blue_team_keys) < 3:
            skipped_missing += 1
            continue
        red_scores = [score_by_team.get(team_key) for team_key in red_team_keys]
        blue_scores = [score_by_team.get(team_key) for team_key in blue_team_keys]
        if any(score is None for score in red_scores + blue_scores):
            skipped_missing += 1
            continue
        match_key = str(row.get("match_key") or "")
        red_synergy = float(synergy_points_by_match_alliance.get((match_key, "red"), 0.0))
        blue_synergy = float(synergy_points_by_match_alliance.get((match_key, "blue"), 0.0))
        red_strength = float(sum(float(score or 0.0) for score in red_scores)) + (float(synergy_weight) * red_synergy)
        blue_strength = float(sum(float(score or 0.0) for score in blue_scores)) + (float(synergy_weight) * blue_synergy)
        winner = str(row.get("winner_alliance") or "").strip().lower()
        rows_scored.append(
            {
                "match_key": match_key,
                "winner_alliance": winner,
                "red_score": _safe_int(row.get("red_score")),
                "blue_score": _safe_int(row.get("blue_score")),
                "strength_delta": red_strength - blue_strength,
            }
        )

    if not rows_scored:
        return {
            "matches_considered": 0,
            "matches_scored": 0,
            "matches_skipped_missing_ratings": int(skipped_missing),
            "winner_accuracy_0_1": None,
            "brier_score": None,
            "log_loss": None,
            "margin_mae_points": None,
            "margin_rmse_points": None,
            "margin_correlation": None,
            "margin_scale": None,
            "best_logistic_scale": None,
        }

    best_summary: dict[str, Any] | None = None
    for scale in scale_candidates:
        winner_hits = 0
        winner_total = 0
        brier_values: list[float] = []
        log_loss_values: list[float] = []
        margin_deltas: list[float] = []
        actual_margins: list[float] = []
        for row in rows_scored:
            winner = row.get("winner_alliance")
            if winner not in {"red", "blue"}:
                continue
            winner_total += 1
            delta = float(row["strength_delta"])
            p_red = _sigmoid(delta / max(1e-6, float(scale)))
            p_red_clamped = _clamp_metric(p_red, 1e-6, 1.0 - 1e-6)
            actual_red = 1.0 if winner == "red" else 0.0
            predicted_winner = "red" if p_red >= 0.5 else "blue"
            if predicted_winner == winner:
                winner_hits += 1
            brier_values.append((p_red - actual_red) ** 2)
            log_loss_values.append(
                -((actual_red * math.log(p_red_clamped)) + ((1.0 - actual_red) * math.log(1.0 - p_red_clamped)))
            )

            red_score = row.get("red_score")
            blue_score = row.get("blue_score")
            if isinstance(red_score, int) and isinstance(blue_score, int):
                margin_deltas.append(delta)
                actual_margins.append(float(red_score - blue_score))

        if winner_total <= 0:
            continue

        margin_scale = None
        margin_mae = None
        margin_rmse = None
        margin_corr = None
        if margin_deltas and len(margin_deltas) == len(actual_margins):
            denom = sum(delta * delta for delta in margin_deltas)
            if denom > 1e-9:
                margin_scale = sum(delta * margin for delta, margin in zip(margin_deltas, actual_margins)) / denom
                residuals = [
                    (delta * float(margin_scale)) - margin
                    for delta, margin in zip(margin_deltas, actual_margins)
                ]
                margin_mae = _mean_metric([abs(value) for value in residuals])
                margin_rmse = math.sqrt(_mean_metric([value * value for value in residuals]) or 0.0)
            margin_corr = _pearson_correlation(margin_deltas, actual_margins)

        summary = {
            "matches_considered": int(len(truth_rows)),
            "matches_scored": int(len(rows_scored)),
            "matches_skipped_missing_ratings": int(skipped_missing),
            "winner_accuracy_0_1": round(float(winner_hits) / float(winner_total), 5),
            "winner_hits": int(winner_hits),
            "winner_total": int(winner_total),
            "brier_score": round(float(_mean_metric(brier_values) or 0.0), 6),
            "log_loss": round(float(_mean_metric(log_loss_values) or 0.0), 6),
            "margin_mae_points": round(float(margin_mae), 5) if isinstance(margin_mae, float) else None,
            "margin_rmse_points": round(float(margin_rmse), 5) if isinstance(margin_rmse, float) else None,
            "margin_correlation": round(float(margin_corr), 5) if isinstance(margin_corr, float) else None,
            "margin_scale": round(float(margin_scale), 6) if isinstance(margin_scale, float) else None,
            "best_logistic_scale": float(scale),
        }
        if best_summary is None:
            best_summary = summary
            continue
        current_brier = float(summary.get("brier_score") or 1.0)
        best_brier = float(best_summary.get("brier_score") or 1.0)
        current_accuracy = float(summary.get("winner_accuracy_0_1") or 0.0)
        best_accuracy = float(best_summary.get("winner_accuracy_0_1") or 0.0)
        if current_brier < best_brier - 1e-9:
            best_summary = summary
        elif abs(current_brier - best_brier) <= 1e-9 and current_accuracy > best_accuracy:
            best_summary = summary

    if best_summary is None:
        return {
            "matches_considered": int(len(truth_rows)),
            "matches_scored": int(len(rows_scored)),
            "matches_skipped_missing_ratings": int(skipped_missing),
            "winner_accuracy_0_1": None,
            "brier_score": None,
            "log_loss": None,
            "margin_mae_points": None,
            "margin_rmse_points": None,
            "margin_correlation": None,
            "margin_scale": None,
            "best_logistic_scale": None,
        }
    return best_summary

@router.post("/event/{event_key}/ratings/recompute")
def recompute_ratings_for_event(event_key: str, db: Session = Depends(get_db)):
    require_write_access("Ratings recompute")
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")
    logger.info("scouting.ratings_recompute.start event=%s", event_key)
    return recompute_event_ratings(db, event_key)


@router.get("/event/{event_key}/ratings", response_model=RatingsResponse)
def get_event_ratings(
    event_key: str,
    request: Request,
    refresh: bool = False,
    include_trend: bool = False,
    db: Session = Depends(get_db),
):
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")

    source = "local"
    if refresh:
        require_admin_access(request, "Ratings refresh")
        require_write_access("Ratings refresh")
        recompute_event_ratings(db, event_key)
        source = "recomputed"

    rows = (
        db.query(models.EventTeamRating, models.Team)
        .outerjoin(models.Team, models.Team.team_key == models.EventTeamRating.team_key)
        .filter(models.EventTeamRating.event_key == event_key)
        .order_by(models.EventTeamRating.rating_0_100.desc(), models.EventTeamRating.team_key.asc())
        .all()
    )

    if not rows:
        source = "local_missing"

    payload = [_serialize_rating_row(row, team) for row, team in rows]

    # The live view requests include_trend=true to get sparkline/momentum data
    # without a second round trip; the default response stays unchanged.
    last_updated_at: str | None = None
    if rows:
        latest = max((r.updated_at for r, _ in rows if r.updated_at), default=None)
        last_updated_at = latest.isoformat() if latest else None
    if include_trend:
        trends = compute_event_rating_trends(db, event_key)
        for item in payload:
            item["rating_trend"] = trends.get(item.get("team_key"))

    return {
        "ok": True,
        "event_key": event_key,
        "event_name": event.name,
        "model_version": MODEL_VERSION,
        "source": source,
        "count": len(payload),
        "ratings": payload,
        "last_updated_at": last_updated_at,
    }


@router.post("/event/{event_key}/model-qa/refresh")
def refresh_event_model_qa(
    event_key: str,
    top_n: int = 8,
    refresh_ratings: bool = True,
    refresh_synergy: bool = True,
    run_validation: bool = True,
    run_tuning: bool = True,
    db: Session = Depends(get_db),
):
    require_write_access("Model QA refresh")
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")
    logger.info(
        "scouting.model_qa_refresh.start event=%s refresh_ratings=%s refresh_synergy=%s",
        event_key,
        refresh_ratings,
        refresh_synergy,
    )

    capped_top_n = max(3, min(top_n, 30))

    team_rows = (
        db.query(models.EventTeam, models.Team)
        .outerjoin(models.Team, models.Team.team_key == models.EventTeam.team_key)
        .filter(models.EventTeam.event_key == event_key)
        .all()
    )
    team_meta_by_key = {
        event_team.team_key: {
            "team_number": team.team_number if team else None,
            "nickname": team.nickname if team else None,
        }
        for event_team, team in team_rows
    }

    before_rows = _query_event_rating_rows(db, event_key)
    before_by_team = {row.team_key: row for row, _ in before_rows}
    before_rank_by_team = {row.team_key: index + 1 for index, (row, _) in enumerate(before_rows)}

    ratings_refresh_summary: dict[str, object]
    if refresh_ratings or not before_rows:
        ratings_refresh = recompute_event_ratings(db, event_key)
        ratings_refresh_summary = {
            "ok": bool(ratings_refresh.get("ok", False)),
            "event_key": ratings_refresh.get("event_key"),
            "model_version": ratings_refresh.get("model_version"),
            "count": int(ratings_refresh.get("count", 0) or 0),
            "quality_gate": ratings_refresh.get("quality_gate"),
        }
    else:
        ratings_refresh_summary = {
            "ok": True,
            "event_key": event_key,
            "model_version": MODEL_VERSION,
            "count": len(before_rows),
            "skipped": True,
            "reason": "refresh_ratings=false",
        }

    after_rows = _query_event_rating_rows(db, event_key)
    after_by_team = {row.team_key: row for row, _ in after_rows}
    after_rank_by_team = {row.team_key: index + 1 for index, (row, _) in enumerate(after_rows)}

    all_team_keys = sorted(
        {
            *team_meta_by_key.keys(),
            *before_by_team.keys(),
            *after_by_team.keys(),
        }
    )

    team_deltas: list[dict[str, object]] = []
    changed_rating_count = 0
    changed_rank_count = 0
    changed_signal_count = 0
    for team_key in all_team_keys:
        before_row = before_by_team.get(team_key)
        after_row = after_by_team.get(team_key)
        before_rating = (
            _rating_display_value(before_row.rating_0_100, before_row.model_version)
            if before_row
            else None
        )
        after_rating = (
            _rating_display_value(after_row.rating_0_100, after_row.model_version)
            if after_row
            else None
        )
        before_confidence = _rating_float(before_row.confidence_0_1) if before_row else None
        after_confidence = _rating_float(after_row.confidence_0_1) if after_row else None
        rating_delta = _delta_or_none(after_rating, before_rating)
        confidence_delta = _delta_or_none(after_confidence, before_confidence)
        before_rank = before_rank_by_team.get(team_key)
        after_rank = after_rank_by_team.get(team_key)
        rank_delta = (
            int(before_rank) - int(after_rank)
            if isinstance(before_rank, int) and isinstance(after_rank, int)
            else None
        )

        pros_before = _signal_labels(before_row.pros_json if before_row else None)
        pros_after = _signal_labels(after_row.pros_json if after_row else None)
        cons_before = _signal_labels(before_row.cons_json if before_row else None)
        cons_after = _signal_labels(after_row.cons_json if after_row else None)
        pros_added = sorted(pros_after - pros_before)
        pros_removed = sorted(pros_before - pros_after)
        cons_added = sorted(cons_after - cons_before)
        cons_removed = sorted(cons_before - cons_after)

        if rating_delta is not None and abs(rating_delta) > 1e-9:
            changed_rating_count += 1
        if rank_delta is not None and rank_delta != 0:
            changed_rank_count += 1
        if pros_added or pros_removed or cons_added or cons_removed:
            changed_signal_count += 1

        meta = team_meta_by_key.get(team_key, {})
        team_deltas.append(
            {
                "team_key": team_key,
                "team_number": meta.get("team_number"),
                "nickname": meta.get("nickname"),
                "before_rating_0_100": round(before_rating, 3) if before_rating is not None else None,
                "after_rating_0_100": round(after_rating, 3) if after_rating is not None else None,
                "delta_rating_0_100": round(rating_delta, 3) if rating_delta is not None else None,
                "before_confidence_0_1": round(before_confidence, 4) if before_confidence is not None else None,
                "after_confidence_0_1": round(after_confidence, 4) if after_confidence is not None else None,
                "delta_confidence_0_1": round(confidence_delta, 4) if confidence_delta is not None else None,
                "before_rank": before_rank,
                "after_rank": after_rank,
                "rank_delta": rank_delta,
                "pros_added": pros_added,
                "pros_removed": pros_removed,
                "cons_added": cons_added,
                "cons_removed": cons_removed,
            }
        )

    def _abs_rating_delta(payload: dict[str, object]) -> float:
        raw = payload.get("delta_rating_0_100")
        return abs(float(raw)) if isinstance(raw, (int, float)) else 0.0

    def _rank_delta_value(payload: dict[str, object]) -> int:
        raw = payload.get("rank_delta")
        return int(raw) if isinstance(raw, int) else 0

    movers_up = [
        payload
        for payload in sorted(
            [item for item in team_deltas if isinstance(item.get("delta_rating_0_100"), (int, float))],
            key=lambda item: float(item.get("delta_rating_0_100") or 0.0),
            reverse=True,
        )
        if float(payload.get("delta_rating_0_100") or 0.0) > 0
    ][:capped_top_n]
    movers_down = [
        payload
        for payload in sorted(
            [item for item in team_deltas if isinstance(item.get("delta_rating_0_100"), (int, float))],
            key=lambda item: float(item.get("delta_rating_0_100") or 0.0),
        )
        if float(payload.get("delta_rating_0_100") or 0.0) < 0
    ][:capped_top_n]
    movers_abs = sorted(team_deltas, key=_abs_rating_delta, reverse=True)[:capped_top_n]
    rank_up = [
        payload
        for payload in sorted(team_deltas, key=_rank_delta_value, reverse=True)
        if _rank_delta_value(payload) > 0
    ][:capped_top_n]
    rank_down = [
        payload
        for payload in sorted(team_deltas, key=_rank_delta_value)
        if _rank_delta_value(payload) < 0
    ][:capped_top_n]
    signal_changes = sorted(
        [
            item
            for item in team_deltas
            if item.get("pros_added")
            or item.get("pros_removed")
            or item.get("cons_added")
            or item.get("cons_removed")
        ],
        key=lambda item: (
            len(item.get("pros_added") or [])
            + len(item.get("pros_removed") or [])
            + len(item.get("cons_added") or [])
            + len(item.get("cons_removed") or [])
        ),
        reverse=True,
    )[:capped_top_n]

    synergy_refresh_summary: dict[str, object]
    if refresh_synergy:
        try:
            synergy_refresh = precompute_event_synergy(db, event_key, model_version=SYNERGY_MODEL_VERSION)
            projection_count = 0
            if isinstance(synergy_refresh, dict):
                projections = synergy_refresh.get("projections")
                if isinstance(projections, dict):
                    projection_count = int(projections.get("count", 0) or 0)
            synergy_refresh_summary = {
                "ok": True,
                "event_key": event_key,
                "model_version": SYNERGY_MODEL_VERSION,
                "projection_count": projection_count,
            }
        except Exception as exc:  # pragma: no cover - defensive API fallback
            synergy_refresh_summary = {
                "ok": False,
                "event_key": event_key,
                "model_version": SYNERGY_MODEL_VERSION,
                "detail": sanitize_external_error(exc, default="Synergy refresh failed."),
            }
    else:
        synergy_refresh_summary = {
            "ok": True,
            "event_key": event_key,
            "model_version": SYNERGY_MODEL_VERSION,
            "skipped": True,
            "reason": "refresh_synergy=false",
        }

    synergy_points_by_match_alliance: dict[tuple[str, str], float] = {}
    synergy_rows = (
        db.query(
            models.MatchSynergyProjection.match_key,
            models.MatchSynergyProjection.alliance_color,
            models.MatchSynergyProjection.alliance_synergy_points,
        )
        .filter(
            models.MatchSynergyProjection.event_key == event_key,
            models.MatchSynergyProjection.model_version == SYNERGY_MODEL_VERSION,
        )
        .all()
    )
    for match_key, alliance_color, synergy_points in synergy_rows:
        match_key_norm = str(match_key or "").strip().lower()
        alliance_norm = str(alliance_color or "").strip().lower()
        if not match_key_norm or alliance_norm not in {"red", "blue"}:
            continue
        synergy_points_by_match_alliance[(match_key_norm, alliance_norm)] = float(synergy_points or 0.0)

    rank_stability_snapshot = _rank_stability_snapshot(
        before_rank_by_team=before_rank_by_team,
        after_rank_by_team=after_rank_by_team,
        top_window=max(3, min(8, len(after_rows))),
    )
    signal_quality_snapshot = _signal_quality_snapshot(after_rows)

    validation_summary: dict[str, Any] = {
        "ran": bool(run_validation),
        "match_truth": {
            "source": "not_run",
            "detail": None,
            "completed_matches": 0,
            "scored_matches": 0,
        },
        "prediction_quality": {
            "matches_considered": 0,
            "matches_scored": 0,
            "matches_skipped_missing_ratings": 0,
            "winner_accuracy_0_1": None,
            "winner_hits": 0,
            "winner_total": 0,
            "brier_score": None,
            "log_loss": None,
            "margin_mae_points": None,
            "margin_rmse_points": None,
            "margin_correlation": None,
            "margin_scale": None,
            "best_logistic_scale": None,
        },
        "rank_stability": rank_stability_snapshot,
        "signal_quality": signal_quality_snapshot,
    }
    tuning_summary: dict[str, Any] = {
        "ran": False,
        "reason": "validation_not_run",
        "profiles_evaluated": 0,
        "best_profile": None,
        "baseline_profile": None,
        "delta_vs_baseline": None,
        "leaderboard": [],
    }

    if run_validation:
        truth_payload = _build_event_match_truth_rows(db, event_key)
        truth_rows = truth_payload.get("rows") if isinstance(truth_payload.get("rows"), list) else []
        completed_matches = len(truth_rows)
        scored_matches = len(
            [
                row
                for row in truth_rows
                if isinstance(row, dict)
                and isinstance(row.get("red_score"), int)
                and isinstance(row.get("blue_score"), int)
            ]
        )

        profiles = _team_score_profiles(after_rows)
        profile_by_name = {
            str(profile.get("name")): profile
            for profile in profiles
            if isinstance(profile, dict) and isinstance(profile.get("name"), str)
        }
        baseline_profile = profile_by_name.get("current_rating")
        scale_candidates = [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0, 24.0]

        baseline_eval = _evaluate_profile_against_truth(
            truth_rows=truth_rows,
            score_by_team=(
                baseline_profile.get("team_score_by_key")
                if isinstance(baseline_profile, dict) and isinstance(baseline_profile.get("team_score_by_key"), dict)
                else {}
            ),
            synergy_points_by_match_alliance=synergy_points_by_match_alliance,
            synergy_weight=float(baseline_profile.get("synergy_weight", 0.0) or 0.0)
            if isinstance(baseline_profile, dict)
            else 0.0,
            scale_candidates=scale_candidates,
        )

        validation_summary = {
            "ran": True,
            "match_truth": {
                "source": truth_payload.get("source"),
                "detail": truth_payload.get("detail"),
                "completed_matches": int(completed_matches),
                "scored_matches": int(scored_matches),
                "synergy_projection_count": int(len(synergy_points_by_match_alliance)),
            },
            "prediction_quality": baseline_eval,
            "rank_stability": rank_stability_snapshot,
            "signal_quality": signal_quality_snapshot,
        }

        if run_tuning:
            profile_rows: list[dict[str, Any]] = []
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                profile_name = str(profile.get("name") or "")
                profile_label = str(profile.get("label") or profile_name or "profile")
                score_map = profile.get("team_score_by_key")
                if not isinstance(score_map, dict):
                    continue
                metrics = _evaluate_profile_against_truth(
                    truth_rows=truth_rows,
                    score_by_team=score_map,
                    synergy_points_by_match_alliance=synergy_points_by_match_alliance,
                    synergy_weight=float(profile.get("synergy_weight", 0.0) or 0.0),
                    scale_candidates=scale_candidates,
                )
                accuracy = float(metrics.get("winner_accuracy_0_1") or 0.0)
                brier = float(metrics.get("brier_score") or 1.0)
                log_loss = float(metrics.get("log_loss") or 1.0)
                margin_mae = metrics.get("margin_mae_points")
                margin_quality = (
                    0.5
                    if not isinstance(margin_mae, (int, float))
                    else (1.0 - _clamp_metric(float(margin_mae) / 35.0, 0.0, 1.0))
                )
                composite_score = (
                    (0.60 * accuracy)
                    + (0.25 * (1.0 - _clamp_metric(brier, 0.0, 1.0)))
                    + (0.10 * (1.0 - _clamp_metric(log_loss / 0.8, 0.0, 1.0)))
                    + (0.05 * margin_quality)
                )
                profile_rows.append(
                    {
                        "name": profile_name,
                        "label": profile_label,
                        "synergy_weight": round(float(profile.get("synergy_weight", 0.0) or 0.0), 4),
                        "weights": profile.get("weights"),
                        "metrics": metrics,
                        "composite_score_0_100": round(100.0 * composite_score, 4),
                    }
                )

            profile_rows.sort(
                key=lambda item: (
                    float(item.get("composite_score_0_100") or 0.0),
                    float((item.get("metrics") or {}).get("winner_accuracy_0_1") or 0.0),
                    -float((item.get("metrics") or {}).get("brier_score") or 1.0),
                ),
                reverse=True,
            )

            baseline_row = next((item for item in profile_rows if item.get("name") == "current_rating"), None)
            best_row = profile_rows[0] if profile_rows else None

            delta_vs_baseline = None
            if isinstance(best_row, dict) and isinstance(baseline_row, dict):
                best_metrics = best_row.get("metrics") if isinstance(best_row.get("metrics"), dict) else {}
                base_metrics = baseline_row.get("metrics") if isinstance(baseline_row.get("metrics"), dict) else {}
                delta_vs_baseline = {
                    "winner_accuracy_0_1": round(
                        float(best_metrics.get("winner_accuracy_0_1") or 0.0)
                        - float(base_metrics.get("winner_accuracy_0_1") or 0.0),
                        6,
                    ),
                    "brier_score": round(
                        float(best_metrics.get("brier_score") or 1.0) - float(base_metrics.get("brier_score") or 1.0),
                        6,
                    ),
                    "log_loss": round(
                        float(best_metrics.get("log_loss") or 1.0) - float(base_metrics.get("log_loss") or 1.0),
                        6,
                    ),
                    "margin_mae_points": round(
                        float(best_metrics.get("margin_mae_points") or 0.0)
                        - float(base_metrics.get("margin_mae_points") or 0.0),
                        6,
                    )
                    if isinstance(best_metrics.get("margin_mae_points"), (int, float))
                    and isinstance(base_metrics.get("margin_mae_points"), (int, float))
                    else None,
                }

            weight_delta: dict[str, float] | None = None
            if (
                isinstance(best_row, dict)
                and isinstance(best_row.get("weights"), dict)
                and isinstance(baseline_row, dict)
                and isinstance(baseline_row.get("weights"), dict)
            ):
                merged_keys = sorted(set(best_row["weights"].keys()) | set(baseline_row["weights"].keys()))
                delta: dict[str, float] = {}
                for key in merged_keys:
                    best_weight = float(best_row["weights"].get(key, 0.0) or 0.0)
                    base_weight = float(baseline_row["weights"].get(key, 0.0) or 0.0)
                    if abs(best_weight - base_weight) <= 1e-9:
                        continue
                    delta[str(key)] = round(best_weight - base_weight, 6)
                if delta:
                    weight_delta = delta

            tuning_summary = {
                "ran": True,
                "reason": None,
                "profiles_evaluated": int(len(profile_rows)),
                "best_profile": best_row,
                "baseline_profile": baseline_row,
                "delta_vs_baseline": delta_vs_baseline,
                "suggested_weight_delta_vs_baseline": weight_delta,
                "leaderboard": profile_rows[:8],
            }
        else:
            tuning_summary = {
                "ran": False,
                "reason": "run_tuning=false",
                "profiles_evaluated": 0,
                "best_profile": None,
                "baseline_profile": None,
                "delta_vs_baseline": None,
                "leaderboard": [],
            }

    return {
        "ok": True,
        "event_key": event_key,
        "event_name": event.name,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "top_n": capped_top_n,
        "ratings_before_count": len(before_rows),
        "ratings_after_count": len(after_rows),
        "changed_rating_count": changed_rating_count,
        "changed_rank_count": changed_rank_count,
        "changed_signal_count": changed_signal_count,
        "ratings_refresh": ratings_refresh_summary,
        "synergy_refresh": synergy_refresh_summary,
        "validation": validation_summary,
        "tuning": tuning_summary,
        "movers": {
            "rating_up": movers_up,
            "rating_down": movers_down,
            "rating_abs": movers_abs,
            "rank_up": rank_up,
            "rank_down": rank_down,
            "signal_changes": signal_changes,
        },
        "team_deltas": team_deltas,
        "config_snapshot": {
            "rating_model_version": MODEL_VERSION,
            "synergy_model_version": SYNERGY_MODEL_VERSION,
            "recency": {
                "match_window": settings.rating_recent_match_window,
                "priority_window": settings.rating_recent_priority_window,
                "priority_weight": settings.rating_recent_priority_weight,
                "base_weight": settings.rating_recent_base_weight,
            },
            "penalty_impact": {
                "net_points": settings.rating_penalty_impact_net_points,
                "subscores": settings.rating_penalty_impact_subscores,
                "driver": settings.rating_penalty_impact_driver,
                "base_rating": settings.rating_penalty_impact_base_rating,
            },
            "autonomous_weighting": {
                "performance_auto_weight": settings.rating_performance_auto_weight,
                "base_auto_weight": settings.rating_base_auto_weight,
            },
            "anti_defense_weighting": {
                "performance_antidefense_weight": settings.rating_performance_antidefense_weight,
                "base_antidefense_weight": settings.rating_base_antidefense_weight,
                "stage_early_quals_multiplier": settings.rating_antidefense_stage_early_quals_multiplier,
                "stage_late_quals_multiplier": settings.rating_antidefense_stage_late_quals_multiplier,
                "stage_elims_multiplier": settings.rating_antidefense_stage_elims_multiplier,
                "stage_support_matches": settings.rating_antidefense_stage_support_matches,
            },
            "trend_thresholds": {
                "throughput_delta": settings.rating_trend_throughput_delta,
                "reliability_delta": settings.rating_trend_reliability_delta,
                "cycle_delta": settings.rating_trend_cycle_delta,
                "penalty_delta": settings.rating_trend_penalty_delta,
            },
            "synergy": {
                "discipline_risk_multiplier": settings.synergy_discipline_risk_multiplier,
                "pair_prior_shrink_k": settings.synergy_pair_prior_shrink_k,
                "pair_event_shrink_k": settings.synergy_pair_event_shrink_k,
                "pair_event_blend_k": settings.synergy_pair_event_blend_k,
            },
        },
    }


@router.get("/event/{event_key}/team/{team_key}/rating")
def get_event_team_rating(
    event_key: str,
    team_key: str,
    recompute_if_missing: bool = True,
    db: Session = Depends(get_db),
):
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")

    normalized_team_key = team_key.strip().lower()
    if normalized_team_key.startswith("frc"):
        normalized_team_key = f"frc{normalized_team_key[3:]}"
    elif normalized_team_key.isdigit():
        normalized_team_key = f"frc{int(normalized_team_key)}"

    event_team = (
        db.query(models.EventTeam)
        .filter(
            models.EventTeam.event_key == event_key,
            models.EventTeam.team_key == normalized_team_key,
        )
        .first()
    )
    if event_team is None:
        raise HTTPException(
            status_code=404,
            detail=f"Team {normalized_team_key} is not registered for event {event_key}",
        )

    row = (
        db.query(models.EventTeamRating, models.Team)
        .outerjoin(models.Team, models.Team.team_key == models.EventTeamRating.team_key)
        .filter(
            models.EventTeamRating.event_key == event_key,
            models.EventTeamRating.team_key == normalized_team_key,
        )
        .first()
    )
    source = "local"
    if row is None and recompute_if_missing:
        if settings.public_readonly_mode:
            source = "local_missing"
        else:
            recompute_event_ratings(db, event_key)
            source = "recomputed"
            row = (
                db.query(models.EventTeamRating, models.Team)
                .outerjoin(models.Team, models.Team.team_key == models.EventTeamRating.team_key)
                .filter(
                    models.EventTeamRating.event_key == event_key,
                    models.EventTeamRating.team_key == normalized_team_key,
                )
                .first()
            )

    if row is None:
        missing_detail = (
            f"No rating available for {normalized_team_key} at {event_key}. "
            "Run /scouting/event/{event_key}/ratings/recompute first."
        )
        if settings.public_readonly_mode:
            missing_detail = (
                f"No rating available for {normalized_team_key} at {event_key}. "
                "In public mode, ratings are read-only and must be precomputed by an admin."
            )
        raise HTTPException(
            status_code=404,
            detail=missing_detail,
        )

    rating, team = row
    return {
        "ok": True,
        "event_key": event_key,
        "event_name": event.name,
        "source": source,
        "rating": _serialize_rating_row(rating, team),
    }


@router.get("/team/{team_key}/history")
def get_team_history(team_key: str, limit: int = 20, db: Session = Depends(get_db)):
    season_scope = _resolve_team_season_scope(db, team_key)
    capped_limit = max(1, min(limit, 100))
    fetch_limit = max(capped_limit, min(capped_limit * 3, 300))
    raw_rows = (
        db.query(models.TeamMatchFinding, models.Match.time)
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
        .filter(models.TeamMatchFinding.team_key == team_key)
        .filter(models.Event.year == season_scope["season_year"])
        .order_by(models.Match.time.desc().nullslast(), models.TeamMatchFinding.id.desc())
        .limit(fetch_limit)
        .all()
    )
    rows, quality_gate = _apply_quality_gate_to_finding_rows(raw_rows)
    rows = _dedupe_finding_rows_by_match(rows)
    rows_for_history, analysis_coverage = _rows_for_metric_coverage(raw_rows, rows, quality_gate)
    rows_for_history = _dedupe_finding_rows_by_match(rows_for_history)[:capped_limit]
    data_freshness = _build_data_freshness_payload(
        season_scope=season_scope,
        match_times=[match_time for _, match_time in rows_for_history],
        analyzed_matches=len(rows),
        raw_analyzed_matches=len(raw_rows),
        quality_gate_enabled=bool(quality_gate.get("enabled")),
        quality_gate_fallback_used=bool(analysis_coverage.get("fallback_used")),
    )

    return {
        "ok": True,
        "team_key": team_key,
        "season_scope": season_scope,
        "data_freshness": data_freshness,
        "analysis_coverage": analysis_coverage,
        "history_count": len(rows_for_history),
        "history_count_quality_gate_accepted": len(rows),
        "quality_gate": quality_gate,
        "matches": [_serialize_finding(finding, match_time) for finding, match_time in rows_for_history],
    }


@router.get("/team/{team_key}/breakdown")
def get_team_breakdown(
    team_key: str,
    event_key: str | None = None,
    match_limit: int = RECENT_STATS_MATCH_WINDOW,
    event_limit: int = 400,
    track_limit: int = 800,
    db: Session = Depends(get_db),
):
    team = db.get(models.Team, team_key)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    season_scope = _resolve_team_season_scope(db, team_key, event_key=event_key)

    capped_match_limit = max(RECENT_STATS_MATCH_WINDOW, min(match_limit, 100))
    capped_event_limit = max(50, min(event_limit, 2000))
    capped_track_limit = max(50, min(track_limit, 4000))

    findings_query = (
        db.query(models.TeamMatchFinding, models.Match.time, models.AnalysisRun.version)
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
        .join(models.AnalysisRun, models.AnalysisRun.id == models.TeamMatchFinding.analysis_run_id)
        .filter(models.TeamMatchFinding.team_key == team_key)
        .filter(models.Event.year == season_scope["season_year"])
    )
    if event_key:
        findings_query = findings_query.filter(models.TeamMatchFinding.event_key == event_key)

    raw_finding_rows = (
        findings_query
        .order_by(models.Match.time.desc().nullslast(), models.TeamMatchFinding.id.desc())
        .limit(max(capped_match_limit, min(capped_match_limit * 3, 300)))
        .all()
    )
    finding_rows, quality_gate = _apply_quality_gate_to_finding_rows(raw_finding_rows)
    finding_rows = _dedupe_finding_rows_by_match(finding_rows)
    metric_rows, analysis_coverage = _rows_for_metric_coverage(raw_finding_rows, finding_rows, quality_gate)
    metric_rows = _dedupe_finding_rows_by_match(metric_rows)[:capped_match_limit]
    data_freshness = _build_data_freshness_payload(
        season_scope=season_scope,
        match_times=[match_time for _, match_time, _ in metric_rows],
        analyzed_matches=len(finding_rows),
        raw_analyzed_matches=len(raw_finding_rows),
        quality_gate_enabled=bool(quality_gate.get("enabled")),
        quality_gate_fallback_used=bool(analysis_coverage.get("fallback_used")),
    )

    findings = [row[0] for row in metric_rows]
    run_ids = [finding.analysis_run_id for finding in findings]
    run_id_set = set(run_ids)
    versions = sorted({row[2] for row in metric_rows if row[2]})
    perimeter_types_in_order: list[str] = []
    perimeter_sources_in_order: list[str] = []
    for finding in findings:
        perimeter_type, perimeter_source = _extract_perimeter_context(finding)
        if perimeter_type and perimeter_type not in perimeter_types_in_order:
            perimeter_types_in_order.append(perimeter_type)
        if perimeter_source and perimeter_source not in perimeter_sources_in_order:
            perimeter_sources_in_order.append(perimeter_source)

    match_events: list[models.MatchEvent] = []
    event_type_counts_rows: list[tuple[str, int]] = []
    robot_tracks: list[models.RobotTrack] = []
    if (not quality_gate["enabled"]) or run_id_set:
        match_events_query = db.query(models.MatchEvent).filter(models.MatchEvent.team_key == team_key)
        if event_key:
            match_events_query = match_events_query.filter(models.MatchEvent.event_key == event_key)
        else:
            match_events_query = (
                match_events_query
                .join(models.Event, models.Event.event_key == models.MatchEvent.event_key)
                .filter(models.Event.year == season_scope["season_year"])
            )
        if quality_gate["enabled"]:
            match_events_query = match_events_query.filter(models.MatchEvent.analysis_run_id.in_(run_id_set))
        match_events = (
            match_events_query
            .order_by(models.MatchEvent.time_sec.desc(), models.MatchEvent.id.desc())
            .limit(capped_event_limit)
            .all()
        )

        event_type_counts_query = (
            db.query(models.MatchEvent.event_type, func.count(models.MatchEvent.id))
            .filter(models.MatchEvent.team_key == team_key)
        )
        if event_key:
            event_type_counts_query = event_type_counts_query.filter(models.MatchEvent.event_key == event_key)
        else:
            event_type_counts_query = (
                event_type_counts_query
                .join(models.Event, models.Event.event_key == models.MatchEvent.event_key)
                .filter(models.Event.year == season_scope["season_year"])
            )
        if quality_gate["enabled"]:
            event_type_counts_query = event_type_counts_query.filter(models.MatchEvent.analysis_run_id.in_(run_id_set))
        event_type_counts_rows = (
            event_type_counts_query
            .group_by(models.MatchEvent.event_type)
            .order_by(func.count(models.MatchEvent.id).desc(), models.MatchEvent.event_type.asc())
            .all()
        )

        robot_tracks_query = db.query(models.RobotTrack).filter(models.RobotTrack.team_key == team_key)
        if event_key:
            robot_tracks_query = robot_tracks_query.filter(models.RobotTrack.event_key == event_key)
        else:
            robot_tracks_query = (
                robot_tracks_query
                .join(models.Event, models.Event.event_key == models.RobotTrack.event_key)
                .filter(models.Event.year == season_scope["season_year"])
            )
        if quality_gate["enabled"]:
            robot_tracks_query = robot_tracks_query.filter(models.RobotTrack.analysis_run_id.in_(run_id_set))
        robot_tracks = (
            robot_tracks_query
            .order_by(models.RobotTrack.time_sec.desc(), models.RobotTrack.id.desc())
            .limit(capped_track_limit)
            .all()
        )

    zone_time_sec: dict[str, float] = defaultdict(float)
    for event in match_events:
        if event.event_type != "zone_dwell":
            continue
        zone_key = str((event.meta or {}).get("zone_key") or "unknown")
        duration = float((event.meta or {}).get("duration_sec") or 0.0)
        zone_time_sec[zone_key] += duration

    climb_fallback_by_match = _infer_climb_from_match_events(match_events)
    metric_coverage = _metric_coverage_payload(
        findings,
        climb_fallback_by_match=climb_fallback_by_match,
        quality_gate_fallback_used=bool(analysis_coverage.get("fallback_used")),
    )
    ml_projection = _team_breakdown_ml_projection(
        db,
        team_key=team_key,
        event_key=event_key,
        season_year=int(season_scope["season_year"]),
    )

    return {
        "ok": True,
        "team": {
            "team_key": team.team_key,
            "team_number": team.team_number,
            "nickname": team.nickname,
        },
        "event_key": event_key,
        "season_scope": season_scope,
        "data_freshness": data_freshness,
        "quality_gate": quality_gate,
        "analysis_coverage": analysis_coverage,
        "ml_projection": ml_projection,
        "matches_analyzed": len(finding_rows),
        "matches_used_for_metrics": len(findings),
        "matches_before_quality_gate": quality_gate["raw_count"],
        "matches_excluded_by_quality_gate": quality_gate["excluded_count"],
        "averages": _compute_averages(
            findings,
            climb_fallback_by_match=climb_fallback_by_match,
        ),
        "metric_units": {
            "fuel_scoring_rate": "scores_per_min",
            "cycle_time_sec": "sec",
            "auto_contribution": "points",
            "climb_success_prob": "probability_0_1",
            "defensive_engagement_sec": "sec",
            "reliability_score": "probability_0_1",
        },
        "climb_sources": _compute_climb_sources(
            findings,
            climb_fallback_by_match=climb_fallback_by_match,
        ),
        "metric_coverage": metric_coverage,
        "active_perimeter_type": perimeter_types_in_order[0] if perimeter_types_in_order else None,
        "perimeter_types": perimeter_types_in_order,
        "perimeter_sources": perimeter_sources_in_order,
        "analysis_versions": versions,
        "event_type_counts": [
            {"event_type": event_type, "count": count}
            for event_type, count in event_type_counts_rows
        ],
        "zone_time_sec": {key: round(value, 3) for key, value in sorted(zone_time_sec.items())},
        "recent_matches": [
            {
                **_serialize_finding(finding, match_time),
                "analysis_run_id": finding.analysis_run_id,
                "source_version": version,
            }
            for finding, match_time, version in metric_rows
        ],
        "recent_events": [
            {
                "id": event.id,
                "analysis_run_id": event.analysis_run_id,
                "match_key": event.match_key,
                "event_key": event.event_key,
                "track_id": event.track_id,
                "time_sec": event.time_sec,
                "frame_index": event.frame_index,
                "event_type": event.event_type,
                "confidence": event.confidence,
                "field_x": event.field_x,
                "field_y": event.field_y,
                "meta": event.meta,
            }
            for event in match_events[:120]
        ],
        "recent_track_points": [
            {
                "id": track.id,
                "analysis_run_id": track.analysis_run_id,
                "match_key": track.match_key,
                "track_id": track.track_id,
                "frame_index": track.frame_index,
                "time_sec": track.time_sec,
                "field_x": track.field_x,
                "field_y": track.field_y,
                "speed_mps": track.speed_mps,
                "zone_key": track.zone_key,
                "bbox": [track.bbox_x1, track.bbox_y1, track.bbox_x2, track.bbox_y2],
            }
            for track in robot_tracks[:300]
        ],
        "run_ids": sorted(set(run_ids), reverse=True),
    }


@router.get("/event/{event_key}/teams/history", response_model=TeamsHistoryResponse)
def get_event_team_histories(
    event_key: str,
    history_limit: int = RECENT_STATS_MATCH_WINDOW,
    exclude_current_event: bool = True,
    db: Session = Depends(get_db),
):
    normalized_event_key = event_key.strip().lower()
    event = db.get(models.Event, normalized_event_key)
    active_season_year = _active_game_season_year()
    event_season_year = (
        int(event.year)
        if event is not None and isinstance(event.year, int)
        else (_event_year_from_key(normalized_event_key) or active_season_year)
    )
    season_scope = {
        "season_year": event_season_year,
        "active_season_year": active_season_year,
        "source": "event",
        "uses_active_season": event_season_year == active_season_year,
    }

    def _load_team_rows() -> list[tuple[models.EventTeam, models.Team, models.TeamProfile | None]]:
        return (
            db.query(models.EventTeam, models.Team, models.TeamProfile)
            .join(models.Team, models.Team.team_key == models.EventTeam.team_key)
            .outerjoin(models.TeamProfile, models.TeamProfile.team_key == models.Team.team_key)
            .filter(models.EventTeam.event_key == normalized_event_key)
            .order_by(models.Team.team_number.asc())
            .all()
        )

    source = "local"
    refresh_error_detail: str | None = None
    team_rows = _load_team_rows()
    if not team_rows:
        refreshed_event, refreshed, refresh_error = _upsert_event_roster_if_missing(
            db=db,
            event_key=normalized_event_key,
            event=event,
        )
        if refreshed:
            event = refreshed_event
            event_season_year = (
                int(event.year)
                if event is not None and isinstance(event.year, int)
                else (_event_year_from_key(normalized_event_key) or active_season_year)
            )
            season_scope = {
                "season_year": event_season_year,
                "active_season_year": active_season_year,
                "source": "event",
                "uses_active_season": event_season_year == active_season_year,
            }
            source = "remote_refreshed"
            team_rows = _load_team_rows()
        elif refresh_error:
            source = "remote_refresh_failed"
            refresh_error_detail = refresh_error

    team_keys = [event_team.team_key for event_team, *_ in team_rows]
    climb_events_by_team_match: dict[tuple[str, str], list[models.MatchEvent]] = defaultdict(list)
    if team_keys:
        climb_events = (
            db.query(models.MatchEvent)
            .filter(
                models.MatchEvent.event_key == normalized_event_key,
                models.MatchEvent.team_key.in_(team_keys),
                func.lower(models.MatchEvent.event_type).contains("climb"),
            )
            .all()
        )
        for event_row in climb_events:
            if not event_row.team_key:
                continue
            climb_events_by_team_match[(event_row.team_key, event_row.match_key)].append(event_row)

    capped_history = max(RECENT_STATS_MATCH_WINDOW, min(history_limit, 30))
    teams_payload = []

    for event_team, team, team_profile in team_rows:
        state_prov = team_profile.state_prov if team_profile else None
        country = team_profile.country if team_profile else None
        history_query = (
            db.query(models.TeamMatchFinding, models.Match.time)
            .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
            .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
            .filter(models.TeamMatchFinding.team_key == event_team.team_key)
            .filter(models.Event.year == event_season_year)
        )
        if exclude_current_event:
            history_query = history_query.filter(models.TeamMatchFinding.event_key != normalized_event_key)

        raw_history_rows = (
            history_query
            .order_by(models.Match.time.desc().nullslast(), models.TeamMatchFinding.id.desc())
            .limit(max(capped_history, min(capped_history * 3, 180)))
            .all()
        )
        history_rows, history_quality_gate = _apply_quality_gate_to_finding_rows(raw_history_rows)
        history_rows = _dedupe_finding_rows_by_match(history_rows)
        history_rows_for_metrics, history_analysis_coverage = _rows_for_metric_coverage(
            raw_history_rows,
            history_rows,
            history_quality_gate,
        )
        history_rows_for_metrics = _dedupe_finding_rows_by_match(history_rows_for_metrics)[:capped_history]
        data_freshness = _build_data_freshness_payload(
            season_scope=season_scope,
            match_times=[match_time for _, match_time in history_rows_for_metrics],
            analyzed_matches=len(history_rows),
            raw_analyzed_matches=len(raw_history_rows),
            quality_gate_enabled=bool(history_quality_gate.get("enabled")),
            quality_gate_fallback_used=bool(history_analysis_coverage.get("fallback_used")),
        )

        findings = [row[0] for row in history_rows_for_metrics]
        climb_fallback_by_match: dict[str, float] = {}
        for finding in findings:
            key = (event_team.team_key, finding.match_key)
            climb_events = climb_events_by_team_match.get(key) or []
            if not climb_events:
                continue
            inferred = _infer_climb_from_match_events(climb_events)
            if finding.match_key in inferred:
                climb_fallback_by_match[finding.match_key] = inferred[finding.match_key]
        averages_payload = _compute_averages(
            findings,
            climb_fallback_by_match=climb_fallback_by_match,
        )
        teams_payload.append(
            {
                "team_key": team.team_key,
                "team_number": team.team_number,
                "nickname": team.nickname,
                "region": _region_label(state_prov, country),
                "state_prov": state_prov,
                "country": country,
                "analysis_coverage": history_analysis_coverage,
                "history_count": len(history_rows_for_metrics),
                "history_count_quality_gate_accepted": len(history_rows),
                "history_count_before_quality_gate": history_quality_gate["raw_count"],
                "history_excluded_by_quality_gate": history_quality_gate["excluded_count"],
                "data_freshness": data_freshness,
                "averages": averages_payload if isinstance(averages_payload, dict) else {},
                "previous_games": [
                    _serialize_finding(finding, match_time)
                    for finding, match_time in history_rows_for_metrics
                ],
            }
        )

    return {
        "ok": True,
        "event_key": normalized_event_key,
        "event_name": event.name if event else None,
        "source": source,
        "refresh_error": refresh_error_detail,
        "season_scope": season_scope,
        "quality_gate": quality_gate_config_payload(),
        "teams_count": len(teams_payload),
        "teams": teams_payload,
    }
