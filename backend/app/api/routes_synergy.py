from bisect import bisect_right
from itertools import combinations
import math
import logging
import random
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin_access, require_write_access, sanitize_external_error
from app.db import models
from app.db.session import get_db
from app.services.ml.synergy import (
    PAIR_EVENT_BLEND_K,
    QUALITY_THRESHOLD_DEFAULT,
    SYNERGY_MODEL_VERSION,
    compute_pair_role_adjustment,
    precompute_event_synergy,
)
from app.services.utils import _as_float, _clamp, _mean as _mean_or_none
from app.tba.client import TBAClient

router = APIRouter(prefix="/synergy", tags=["synergy"])
logger = logging.getLogger(__name__)


def _normalize_team_key(raw_team_key: str) -> str:
    value = raw_team_key.strip().lower()
    if value.startswith("frc"):
        suffix = value[3:]
    else:
        suffix = value
    if suffix.isdigit():
        return f"frc{int(suffix)}"
    return value


def _canonical_pair(team_key_a: str, team_key_b: str) -> tuple[str, str]:
    return tuple(sorted((team_key_a, team_key_b)))


def _mean(values: list[float]) -> float:
    return _mean_or_none(values) or 0.0


def _percentile_score(values: list[float], value: float) -> float:
    if not values:
        return 50.0
    ordered = sorted(float(v) for v in values)
    rank = bisect_right(ordered, float(value))
    return _clamp((float(rank) / float(len(ordered))) * 100.0, 0.0, 100.0)


def _pick_top_signals(
    signals: list[dict[str, Any]],
    *,
    limit: int = 3,
    higher_percentile_first: bool = True,
) -> list[dict[str, Any]]:
    ranked: list[tuple[float, dict[str, Any]]] = []
    for item in signals:
        if not isinstance(item, dict):
            continue
        percentile = item.get("percentile")
        score = float(percentile) if isinstance(percentile, (int, float)) else (0.0 if higher_percentile_first else 100.0)
        ranked.append((score, item))
    ranked.sort(key=lambda row: row[0], reverse=higher_percentile_first)
    return [item for _, item in ranked[:limit]]


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return int(value)
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(float(raw))
        except ValueError:
            return None
    return None


def _parse_rank_map_from_tba_payload(payload: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {}
    raw_rankings = payload.get("rankings")
    rankings = raw_rankings if isinstance(raw_rankings, list) else []
    rank_map: dict[str, int] = {}
    for row in rankings:
        if not isinstance(row, dict):
            continue
        team_key = _normalize_team_key(str(row.get("team_key") or ""))
        if not team_key:
            continue
        rank_value = _as_int(row.get("rank"))
        if rank_value is None or rank_value <= 0:
            continue
        rank_map[team_key] = rank_value
    return rank_map


def _fetch_event_rank_map_from_tba(event_key: str) -> tuple[dict[str, int], str]:
    if not settings.tba_auth_key.strip():
        return {}, "tba_unconfigured"
    try:
        payload = TBAClient().event_rankings(event_key)
    except Exception:
        return {}, "tba_unavailable"
    rank_map = _parse_rank_map_from_tba_payload(payload if isinstance(payload, dict) else None)
    return rank_map, ("tba" if rank_map else "tba_empty")


def _build_model_rank_map(
    event_team_keys: list[str],
    rating_by_team: dict[str, models.EventTeamRating],
) -> dict[str, int]:
    def _key(team_key: str) -> tuple[float, str]:
        row = rating_by_team.get(team_key)
        rating = float(row.rating_0_100) if row is not None else 50.0
        return (-rating, team_key)

    ordered = sorted(event_team_keys, key=_key)
    return {team_key: index + 1 for index, team_key in enumerate(ordered)}


def _selection_strength_from_rating(rating: models.EventTeamRating | None) -> tuple[float, str]:
    if rating is not None and isinstance(rating.details_json, dict):
        epa_context = rating.details_json.get("epa_context")
        if isinstance(epa_context, dict):
            raw_epa = _as_float(epa_context.get("raw_value"))
            if raw_epa is not None:
                return raw_epa, "statbotics_norm_epa"
    if rating is not None:
        rating_value = _as_float(rating.rating_0_100)
        if rating_value is not None:
            # Map 0..100 scouting rating to an EPA-like baseline centered near 1800.
            return 1300.0 + (10.0 * rating_value), "scouting_rating_pseudo_epa"
    return 1800.0, "neutral_default"


def _softmax_probabilities(
    desirability_by_team: dict[str, float],
    team_keys: list[str],
    scale: float,
) -> list[dict[str, float | str]]:
    if not team_keys:
        return []
    safe_scale = max(1e-6, float(scale))
    max_value = max(float(desirability_by_team.get(team_key, 0.0)) for team_key in team_keys)
    exp_rows: list[tuple[str, float]] = []
    exp_sum = 0.0
    for team_key in team_keys:
        desirability = float(desirability_by_team.get(team_key, 0.0))
        weight = math.exp((desirability - max_value) / safe_scale)
        exp_rows.append((team_key, weight))
        exp_sum += weight
    if exp_sum <= 0.0:
        uniform = 1.0 / float(len(team_keys))
        return [
            {
                "team_key": team_key,
                "probability_0_1": uniform,
                "selection_desirability": float(desirability_by_team.get(team_key, 0.0)),
            }
            for team_key in team_keys
        ]
    rows = [
        {
            "team_key": team_key,
            "probability_0_1": (weight / exp_sum),
            "selection_desirability": float(desirability_by_team.get(team_key, 0.0)),
        }
        for team_key, weight in exp_rows
    ]
    rows.sort(
        key=lambda row: (
            -float(row.get("probability_0_1") or 0.0),
            str(row.get("team_key") or ""),
        )
    )
    return rows


def _draw_weighted_team(
    probabilities: list[dict[str, float | str]],
    rng: random.Random,
) -> str | None:
    if not probabilities:
        return None
    threshold = rng.random()
    cumulative = 0.0
    for row in probabilities:
        cumulative += float(row.get("probability_0_1") or 0.0)
        team_key = str(row.get("team_key") or "")
        if cumulative >= threshold and team_key:
            return team_key
    fallback = str(probabilities[-1].get("team_key") or "")
    return fallback or None


def _simulate_alliance_selection(
    *,
    event_team_keys: list[str],
    captain_team_keys: list[str],
    desirability_by_team: dict[str, float],
    scale: float,
    simulations: int,
) -> tuple[dict[str, float], dict[str, float], dict[str, list[str]]]:
    non_captains = [team_key for team_key in event_team_keys if team_key not in set(captain_team_keys)]
    if not captain_team_keys or not non_captains or simulations <= 0:
        return {}, {}, {}

    first_round_counts: dict[str, int] = {team_key: 0 for team_key in event_team_keys}
    second_round_counts: dict[str, int] = {team_key: 0 for team_key in event_team_keys}
    rng = random.Random(118)  # deterministic for reproducible UI outputs  # nosec B311
    captain_set = set(captain_team_keys)

    for _ in range(simulations):
        available: set[str] = set(non_captains)
        alliances: dict[str, list[str]] = {captain: [captain] for captain in captain_team_keys}

        # Round 1 picks: seed 1 -> seed N
        for captain in captain_team_keys:
            if not available:
                break
            candidates = sorted(available)
            probs = _softmax_probabilities(desirability_by_team, candidates, scale)
            selected = _draw_weighted_team(probs, rng)
            if not selected or selected not in available:
                continue
            alliances[captain].append(selected)
            first_round_counts[selected] = first_round_counts.get(selected, 0) + 1
            available.remove(selected)

        # Round 2 picks: seed N -> seed 1
        for captain in reversed(captain_team_keys):
            if not available:
                break
            candidates = sorted(available)
            probs = _softmax_probabilities(desirability_by_team, candidates, scale)
            selected = _draw_weighted_team(probs, rng)
            if not selected or selected not in available:
                continue
            alliances[captain].append(selected)
            second_round_counts[selected] = second_round_counts.get(selected, 0) + 1
            available.remove(selected)

    first_round_prob = {
        team_key: (float(count) / float(simulations))
        for team_key, count in first_round_counts.items()
        if team_key not in captain_set
    }
    second_round_prob = {
        team_key: (float(count) / float(simulations))
        for team_key, count in second_round_counts.items()
        if team_key not in captain_set
    }
    return first_round_prob, second_round_prob, {}


def _build_expected_alliance_board(
    *,
    event_team_keys: list[str],
    captain_team_keys: list[str],
    desirability_by_team: dict[str, float],
    scale: float,
) -> dict[str, list[str]]:
    non_captains = [team_key for team_key in event_team_keys if team_key not in set(captain_team_keys)]
    available: set[str] = set(non_captains)
    board: dict[str, list[str]] = {captain: [captain] for captain in captain_team_keys}

    for captain in captain_team_keys:
        if not available:
            break
        probs = _softmax_probabilities(desirability_by_team, sorted(available), scale)
        selected = str(probs[0].get("team_key") or "") if probs else ""
        if not selected or selected not in available:
            continue
        board[captain].append(selected)
        available.remove(selected)

    for captain in reversed(captain_team_keys):
        if not available:
            break
        probs = _softmax_probabilities(desirability_by_team, sorted(available), scale)
        selected = str(probs[0].get("team_key") or "") if probs else ""
        if not selected or selected not in available:
            continue
        board[captain].append(selected)
        available.remove(selected)

    return board


class TheoreticalAllianceRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    team_keys: list[str] = Field(min_length=3, max_length=3)
    compatibility_weight: float = Field(default=0.6, ge=0.0)
    pros_weight: float = Field(default=0.25, ge=0.0)
    cons_weight: float = Field(default=0.15, ge=0.0)
    include_selection_model: bool = True
    selection_rank_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    selection_scale: float = Field(default=35.0, gt=0.0, le=250.0)
    selection_captains: int = Field(default=8, ge=1, le=8)
    selection_simulations: int = Field(default=400, ge=50, le=3000)
    selection_rank_source: str = Field(default="auto", pattern="^(auto|tba|model)$")
    model_version: str = Field(default=SYNERGY_MODEL_VERSION, min_length=1)
    quality_threshold: float = Field(default=QUALITY_THRESHOLD_DEFAULT, ge=0.0, le=1.0)
    auto_precompute: bool = True


@router.post("/event/{event_key}/precompute")
def precompute_synergy_for_event(
    event_key: str,
    synergy_model_version: str = Query(default=SYNERGY_MODEL_VERSION, alias="model_version"),
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    db: Session = Depends(get_db),
):
    require_write_access("Synergy precompute")
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")
    logger.info(
        "synergy.precompute.start event=%s model_version=%s quality_threshold=%s",
        event_key,
        synergy_model_version,
        quality_threshold,
    )

    if quality_threshold < 0.0 or quality_threshold > 1.0:
        raise HTTPException(status_code=400, detail="quality_threshold must be between 0 and 1")

    try:
        result = precompute_event_synergy(
            db,
            event_key,
            model_version=synergy_model_version,
            quality_threshold=quality_threshold,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.get("/event/{event_key}/projections")
def list_event_projections(
    event_key: str,
    synergy_model_version: str = Query(default=SYNERGY_MODEL_VERSION, alias="model_version"),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.MatchSynergyProjection)
        .filter(
            models.MatchSynergyProjection.event_key == event_key,
            models.MatchSynergyProjection.model_version == synergy_model_version,
        )
        .order_by(
            models.MatchSynergyProjection.scheduled_time.asc().nullslast(),
            models.MatchSynergyProjection.match_key.asc(),
            models.MatchSynergyProjection.alliance_color.asc(),
        )
        .all()
    )
    return {
        "ok": True,
        "event_key": event_key,
        "model_version": synergy_model_version,
        "count": len(rows),
        "projections": [
            {
                "match_key": row.match_key,
                "alliance_color": row.alliance_color,
                "scheduled_time": row.scheduled_time,
                "expected_throughput": row.expected_throughput,
                "alliance_synergy_points": row.alliance_synergy_points,
                "projected_throughput": row.projected_throughput,
                "alliance_synergy_score_0_100": row.alliance_synergy_score_0_100,
                "confidence_0_1": row.confidence_0_1,
                "source_label": row.source_label,
                "pair_breakdown": row.pair_breakdown or [],
                "params_hash": row.params_hash,
                "computed_at": row.computed_at.isoformat() if row.computed_at else None,
            }
            for row in rows
        ],
    }


@router.post("/event/{event_key}/theoretical-alliance")
def score_theoretical_alliance(
    event_key: str,
    payload: TheoreticalAllianceRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")

    normalized_team_keys = [_normalize_team_key(team_key) for team_key in payload.team_keys]
    unique_team_keys = sorted(set(normalized_team_keys))
    if len(unique_team_keys) != 3:
        raise HTTPException(
            status_code=400,
            detail="Exactly 3 unique team keys are required for theoretical team builder.",
        )

    event_team_rows = (
        db.query(models.EventTeam.team_key)
        .filter(
            models.EventTeam.event_key == event_key,
            models.EventTeam.team_key.in_(unique_team_keys),
        )
        .all()
    )
    valid_team_keys = {row.team_key for row in event_team_rows}
    missing_team_keys = [team_key for team_key in unique_team_keys if team_key not in valid_team_keys]
    if missing_team_keys:
        raise HTTPException(
            status_code=400,
            detail=(
                "Theoretical team builder only supports teams from this event. "
                f"Missing from {event_key}: {', '.join(missing_team_keys)}"
            ),
        )

    weight_sum = payload.compatibility_weight + payload.pros_weight + payload.cons_weight
    if weight_sum <= 0.0:
        raise HTTPException(status_code=400, detail="At least one weight must be greater than zero.")
    compatibility_weight = payload.compatibility_weight / weight_sum
    pros_weight = payload.pros_weight / weight_sum
    cons_weight = payload.cons_weight / weight_sum

    projection_rows = (
        db.query(models.MatchSynergyProjection)
        .filter(
            models.MatchSynergyProjection.event_key == event_key,
            models.MatchSynergyProjection.model_version == payload.model_version,
        )
        .all()
    )
    auto_precompute_effective = bool(payload.auto_precompute)
    warnings: list[str] = []
    if auto_precompute_effective and settings.public_readonly_mode:
        auto_precompute_effective = False
        warnings.append("Auto precompute disabled in public mode; using existing synergy projections only.")

    if auto_precompute_effective and not projection_rows:
        require_admin_access(request, "Synergy auto precompute")
        require_write_access("Synergy auto precompute")
        try:
            precompute_event_synergy(
                db,
                event_key,
                model_version=payload.model_version,
                quality_threshold=payload.quality_threshold,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=sanitize_external_error(exc, default="Unable to precompute synergy for this event."),
            ) from exc
        projection_rows = (
            db.query(models.MatchSynergyProjection)
            .filter(
                models.MatchSynergyProjection.event_key == event_key,
                models.MatchSynergyProjection.model_version == payload.model_version,
            )
            .all()
        )

    prior_rows = (
        db.query(models.TeamPairSynergyPrior)
        .filter(
            models.TeamPairSynergyPrior.season == event.year,
            models.TeamPairSynergyPrior.model_version == payload.model_version,
            models.TeamPairSynergyPrior.team_key_a.in_(unique_team_keys),
            models.TeamPairSynergyPrior.team_key_b.in_(unique_team_keys),
        )
        .all()
    )
    prior_by_pair = {
        _canonical_pair(row.team_key_a, row.team_key_b): row
        for row in prior_rows
    }

    event_pair_rows = (
        db.query(models.TeamPairSynergyEvent)
        .filter(
            models.TeamPairSynergyEvent.event_key == event_key,
            models.TeamPairSynergyEvent.model_version == payload.model_version,
            models.TeamPairSynergyEvent.team_key_a.in_(unique_team_keys),
            models.TeamPairSynergyEvent.team_key_b.in_(unique_team_keys),
        )
        .all()
    )
    event_by_pair = {
        _canonical_pair(row.team_key_a, row.team_key_b): row
        for row in event_pair_rows
    }

    event_strength_rows = (
        db.query(models.TeamEventThroughputStrength)
        .filter(
            models.TeamEventThroughputStrength.event_key == event_key,
            models.TeamEventThroughputStrength.model_version == payload.model_version,
            models.TeamEventThroughputStrength.team_key.in_(unique_team_keys),
        )
        .all()
    )
    confidence_by_team = {
        row.team_key: float(row.confidence_0_1 or 0.0)
        for row in event_strength_rows
    }
    rating_rows = (
        db.query(models.EventTeamRating)
        .filter(
            models.EventTeamRating.event_key == event_key,
            models.EventTeamRating.team_key.in_(unique_team_keys),
        )
        .all()
    )
    rating_by_team = {row.team_key: row for row in rating_rows}

    selection_model_payload: dict[str, Any] | None = None
    selection_prob_by_team: dict[str, float] = {}
    selection_rank_by_team: dict[str, int] = {}
    selection_strength_by_team: dict[str, float] = {}
    selection_desirability_by_team: dict[str, float] = {}
    selection_strength_source_by_team: dict[str, str] = {}
    selection_captain_set: set[str] = set()
    if payload.include_selection_model:
        event_team_keys = sorted(
            {
                str(row.team_key)
                for row in db.query(models.EventTeam.team_key)
                .filter(models.EventTeam.event_key == event_key)
                .all()
            }
        )
        if not event_team_keys:
            event_team_keys = unique_team_keys.copy()

        all_rating_rows = (
            db.query(models.EventTeamRating)
            .filter(
                models.EventTeamRating.event_key == event_key,
                models.EventTeamRating.team_key.in_(event_team_keys),
            )
            .all()
        )
        all_rating_by_team = {row.team_key: row for row in all_rating_rows}
        model_rank_map = _build_model_rank_map(event_team_keys, all_rating_by_team)

        tba_rank_map: dict[str, int] = {}
        tba_rank_state = "skipped"
        if payload.selection_rank_source in {"auto", "tba"}:
            tba_rank_map, tba_rank_state = _fetch_event_rank_map_from_tba(event_key)
        if payload.selection_rank_source == "tba":
            effective_rank_map = tba_rank_map if tba_rank_map else model_rank_map
            rank_source = "tba" if tba_rank_map else "model_fallback"
        elif payload.selection_rank_source == "model":
            effective_rank_map = model_rank_map
            rank_source = "model"
        else:
            effective_rank_map = tba_rank_map if tba_rank_map else model_rank_map
            rank_source = "tba" if tba_rank_map else "model"

        captain_count = max(1, min(int(payload.selection_captains), 8, len(event_team_keys)))
        captain_team_keys = sorted(
            event_team_keys,
            key=lambda team_key: (
                int(effective_rank_map.get(team_key, model_rank_map.get(team_key, 9999))),
                int(model_rank_map.get(team_key, 9999)),
                team_key,
            ),
        )[:captain_count]

        team_strength_by_key: dict[str, float] = {}
        strength_source_by_key: dict[str, str] = {}
        desirability_by_key: dict[str, float] = {}
        for team_key in event_team_keys:
            rank_value = int(effective_rank_map.get(team_key, model_rank_map.get(team_key, len(event_team_keys) + 1)))
            strength_value, strength_source = _selection_strength_from_rating(all_rating_by_team.get(team_key))
            desirability = float(strength_value) - (float(payload.selection_rank_weight) * float(rank_value))
            team_strength_by_key[team_key] = float(strength_value)
            strength_source_by_key[team_key] = strength_source
            desirability_by_key[team_key] = desirability

        first_round_candidates_by_seed: dict[str, list[dict[str, Any]]] = {}
        available_for_first_round: set[str] = set(event_team_keys) - set(captain_team_keys)
        for seed_index, captain_key in enumerate(captain_team_keys, start=1):
            if not available_for_first_round:
                first_round_candidates_by_seed[str(seed_index)] = []
                continue
            candidates = sorted(available_for_first_round)
            probs = _softmax_probabilities(
                desirability_by_key,
                candidates,
                float(payload.selection_scale),
            )
            first_round_candidates_by_seed[str(seed_index)] = [
                {
                    "team_key": str(row.get("team_key") or ""),
                    "probability_0_1": round(float(row.get("probability_0_1") or 0.0), 6),
                    "selection_desirability": round(float(row.get("selection_desirability") or 0.0), 4),
                }
                for row in probs[:12]
            ]
            if probs:
                top_choice = str(probs[0].get("team_key") or "")
                if top_choice and top_choice in available_for_first_round:
                    available_for_first_round.remove(top_choice)

        first_round_prob, second_round_prob, _ = _simulate_alliance_selection(
            event_team_keys=event_team_keys,
            captain_team_keys=captain_team_keys,
            desirability_by_team=desirability_by_key,
            scale=float(payload.selection_scale),
            simulations=int(payload.selection_simulations),
        )
        expected_board = _build_expected_alliance_board(
            event_team_keys=event_team_keys,
            captain_team_keys=captain_team_keys,
            desirability_by_team=desirability_by_key,
            scale=float(payload.selection_scale),
        )
        selection_prob_by_team = first_round_prob.copy()
        selection_rank_by_team = {
            team_key: int(effective_rank_map.get(team_key, model_rank_map.get(team_key, len(event_team_keys) + 1)))
            for team_key in event_team_keys
        }
        selection_strength_by_team = team_strength_by_key
        selection_desirability_by_team = desirability_by_key
        selection_strength_source_by_team = strength_source_by_key
        selection_captain_set = set(captain_team_keys)

        top_desirability = sorted(
            event_team_keys,
            key=lambda team_key: (-float(desirability_by_key.get(team_key, 0.0)), team_key),
        )[:20]
        selection_model_payload = {
            "enabled": True,
            "formula": "selection_desirability = strength_score - rank_weight * rank",
            "strength_source_priority": ["statbotics_norm_epa", "scouting_rating_pseudo_epa", "neutral_default"],
            "rank_source": rank_source,
            "rank_source_detail": {
                "requested": payload.selection_rank_source,
                "tba_status": tba_rank_state,
            },
            "rank_weight": round(float(payload.selection_rank_weight), 6),
            "scale": round(float(payload.selection_scale), 6),
            "captain_count": captain_count,
            "simulations": int(payload.selection_simulations),
            "declines_mode": "not_modeled",
            "notes": [
                "Declines and captain-invite strategies (burning picks, intentional ordering) are not modeled.",
                "Probabilities are logistic/softmax approximations and should be treated as directional.",
            ],
            "captains": captain_team_keys,
            "expected_alliance_board": expected_board,
            "top_desirability": [
                {
                    "team_key": team_key,
                    "rank": int(effective_rank_map.get(team_key, model_rank_map.get(team_key, len(event_team_keys) + 1))),
                    "strength_score": round(float(team_strength_by_key.get(team_key, 0.0)), 4),
                    "strength_source": strength_source_by_key.get(team_key, "neutral_default"),
                    "selection_desirability": round(float(desirability_by_key.get(team_key, 0.0)), 4),
                    "is_captain": team_key in set(captain_team_keys),
                    "first_round_pick_probability_0_1": round(float(first_round_prob.get(team_key, 0.0)), 6),
                    "second_round_pick_probability_0_1": round(float(second_round_prob.get(team_key, 0.0)), 6),
                }
                for team_key in top_desirability
            ],
            "first_round_seed_pick_probabilities": first_round_candidates_by_seed,
            "team_pick_probability_0_1": {
                team_key: round(float(first_round_prob.get(team_key, 0.0)), 6)
                for team_key in event_team_keys
            },
        }

    pair_breakdown: list[dict[str, Any]] = []
    pair_points: list[float] = []
    pair_confidences: list[float] = []
    source_labels: list[str] = []
    points_by_team: dict[str, float] = {team_key: 0.0 for team_key in unique_team_keys}
    counts_by_team: dict[str, int] = {team_key: 0 for team_key in unique_team_keys}

    for team_key_a, team_key_b in combinations(unique_team_keys, 2):
        pair = _canonical_pair(team_key_a, team_key_b)
        prior = prior_by_pair.get(pair)
        event_value = event_by_pair.get(pair)
        blended_value = 0.0
        blended_confidence = 0.0
        source_label = "projection"

        if prior is not None and event_value is not None:
            w_event = float(event_value.n_matches_together) / (
                float(event_value.n_matches_together) + PAIR_EVENT_BLEND_K
            )
            blended_value = (
                (w_event * float(event_value.synergy_points_event))
                + ((1.0 - w_event) * float(prior.synergy_points_prior))
            )
            blended_confidence = (
                (w_event * float(event_value.confidence_event))
                + ((1.0 - w_event) * float(prior.confidence_prior))
            )
            source_label = "measured" if w_event >= 0.67 else "blended"
        elif event_value is not None:
            blended_value = float(event_value.synergy_points_event)
            blended_confidence = float(event_value.confidence_event)
            source_label = "measured"
        elif prior is not None:
            blended_value = float(prior.synergy_points_prior)
            blended_confidence = float(prior.confidence_prior)
            source_label = "projection"

        role_adjustment = compute_pair_role_adjustment(
            rating_by_team,
            pair[0],
            pair[1],
        )
        role_adjustment_points = float(role_adjustment.get("net_adjustment_points") or 0.0)
        adjusted_value = blended_value + role_adjustment_points
        profile_confidence = _clamp(float(role_adjustment.get("profile_confidence_0_1") or 0.0), 0.0, 1.0)
        adjusted_confidence = _clamp(
            blended_confidence * (0.78 + (0.22 * profile_confidence)),
            0.0,
            1.0,
        )

        pair_points.append(adjusted_value)
        pair_confidences.append(adjusted_confidence)
        source_labels.append(source_label)
        points_by_team[team_key_a] += adjusted_value
        points_by_team[team_key_b] += adjusted_value
        counts_by_team[team_key_a] += 1
        counts_by_team[team_key_b] += 1

        pair_breakdown.append(
            {
                "team_key_a": pair[0],
                "team_key_b": pair[1],
                "base_synergy_points": round(blended_value, 4),
                "role_adjustment_points": round(role_adjustment_points, 4),
                "complement_bonus_points": round(float(role_adjustment.get("complement_bonus_points") or 0.0), 4),
                "risk_penalty_points": round(float(role_adjustment.get("risk_penalty_points") or 0.0), 4),
                "synergy_points": round(adjusted_value, 4),
                "confidence": round(adjusted_confidence, 4),
                "role_profile_confidence_0_1": round(profile_confidence, 4),
                "role_profile_coverage_0_1": round(
                    _clamp(float(role_adjustment.get("profile_coverage_0_1") or 0.0), 0.0, 1.0),
                    4,
                ),
                "role_axes": role_adjustment.get("role_axes") or {},
                "source": source_label,
                "prior_n": prior.n_matches_together if prior else 0,
                "event_n": event_value.n_matches_together if event_value else 0,
            }
        )

    if "measured" in source_labels:
        compatibility_source = "measured" if source_labels.count("measured") == len(source_labels) else "blended"
    elif "blended" in source_labels:
        compatibility_source = "blended"
    else:
        compatibility_source = "projection"

    alliance_synergy_points = float(sum(pair_points))
    projection_points = [float(row.alliance_synergy_points or 0.0) for row in projection_rows]
    compatibility_score_0_100 = _percentile_score(projection_points, alliance_synergy_points)
    pair_confidence = _mean(pair_confidences)
    team_confidence = _mean([confidence_by_team.get(team_key, 0.0) for team_key in unique_team_keys])
    confidence_0_1 = _clamp(pair_confidence * (0.55 + (0.45 * team_confidence)), 0.0, 1.0)

    team_compatibility_distribution = [float(v) for v in points_by_team.values()]
    team_summaries = []
    pros_scores: list[float] = []
    cons_risks: list[float] = []
    for team_key in unique_team_keys:
        rating = rating_by_team.get(team_key)
        pros_signals = rating.pros_json if rating is not None and isinstance(rating.pros_json, list) else []
        cons_signals = rating.cons_json if rating is not None and isinstance(rating.cons_json, list) else []

        pros_percentiles = [
            float(item.get("percentile"))
            for item in pros_signals
            if isinstance(item, dict) and isinstance(item.get("percentile"), (int, float))
        ]
        cons_percentiles = [
            float(item.get("percentile"))
            for item in cons_signals
            if isinstance(item, dict) and isinstance(item.get("percentile"), (int, float))
        ]
        pros_score_0_100 = _mean(pros_percentiles) if pros_percentiles else 40.0
        cons_risk_0_100 = _mean([100.0 - value for value in cons_percentiles]) if cons_percentiles else 15.0

        team_pair_count = max(1, counts_by_team.get(team_key, 0))
        team_synergy_points = float(points_by_team.get(team_key, 0.0))
        team_synergy_avg = team_synergy_points / float(team_pair_count)
        team_compatibility_score_0_100 = _percentile_score(team_compatibility_distribution, team_synergy_points)
        team_weighted_score_0_100 = _clamp(
            (compatibility_weight * team_compatibility_score_0_100)
            + (pros_weight * pros_score_0_100)
            + (cons_weight * (100.0 - cons_risk_0_100)),
            0.0,
            100.0,
        )

        pros_scores.append(pros_score_0_100)
        cons_risks.append(cons_risk_0_100)
        team_summaries.append(
            {
                "team_key": team_key,
                "rating_0_100": float(rating.rating_0_100) if rating is not None else None,
                "model_confidence_0_1": float(rating.confidence_0_1) if rating is not None else None,
                "compatibility_points": round(team_synergy_points, 4),
                "compatibility_avg_pair_points": round(team_synergy_avg, 4),
                "compatibility_score_0_100": round(team_compatibility_score_0_100, 2),
                "pros_score_0_100": round(pros_score_0_100, 2),
                "cons_risk_0_100": round(cons_risk_0_100, 2),
                "weighted_score_0_100": round(team_weighted_score_0_100, 2),
                "selection_rank": selection_rank_by_team.get(team_key),
                "selection_strength_score": (
                    round(float(selection_strength_by_team.get(team_key, 0.0)), 4)
                    if team_key in selection_strength_by_team
                    else None
                ),
                "selection_strength_source": selection_strength_source_by_team.get(team_key),
                "selection_desirability": (
                    round(float(selection_desirability_by_team.get(team_key, 0.0)), 4)
                    if team_key in selection_desirability_by_team
                    else None
                ),
                "selection_pick_probability_0_1": (
                    round(float(selection_prob_by_team.get(team_key, 0.0)), 6)
                    if team_key in selection_prob_by_team
                    else 0.0
                ),
                "selection_is_captain": team_key in selection_captain_set,
                "pros_top": _pick_top_signals(pros_signals, limit=3, higher_percentile_first=True),
                "cons_top": _pick_top_signals(cons_signals, limit=3, higher_percentile_first=False),
            }
        )

    alliance_pros_score_0_100 = _mean(pros_scores)
    alliance_cons_risk_0_100 = _mean(cons_risks)
    weighted_total_score_0_100 = _clamp(
        (compatibility_weight * compatibility_score_0_100)
        + (pros_weight * alliance_pros_score_0_100)
        + (cons_weight * (100.0 - alliance_cons_risk_0_100)),
        0.0,
        100.0,
    )

    return {
        "ok": True,
        "event_key": event_key,
        "team_keys": unique_team_keys,
        "warnings": warnings,
        "auto_precompute_requested": bool(payload.auto_precompute),
        "auto_precompute_applied": auto_precompute_effective,
        "weights": {
            "compatibility_weight": round(compatibility_weight, 6),
            "pros_weight": round(pros_weight, 6),
            "cons_weight": round(cons_weight, 6),
        },
        "compatibility": {
            "alliance_synergy_points": round(alliance_synergy_points, 4),
            "compatibility_score_0_100": round(compatibility_score_0_100, 2),
            "confidence_0_1": round(confidence_0_1, 4),
            "source_label": compatibility_source,
            "pair_breakdown": pair_breakdown,
        },
        "pros_cons": {
            "alliance_pros_score_0_100": round(alliance_pros_score_0_100, 2),
            "alliance_cons_risk_0_100": round(alliance_cons_risk_0_100, 2),
        },
        "weighted_total_score_0_100": round(weighted_total_score_0_100, 2),
        "selection_model": selection_model_payload,
        "teams": sorted(team_summaries, key=lambda row: row["weighted_score_0_100"], reverse=True),
    }
