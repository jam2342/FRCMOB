# Alliance selection tools for Alliance Advisor: who is likely to be picked (a softmax
# draft simulation on ratings and rank) and how good a chosen alliance would be (the
# team model, same as pick values). Replaced the synergy router on 2026-10-17.
from __future__ import annotations

import logging
import math
import random
import threading
import time
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.db.session import get_db
from app.services.cache import get_cache
from app.services.utils import _as_float
from app.tba.client import TBAClient

router = APIRouter(prefix="/events", tags=["alliances"])
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
    # The same draws as _softmax_probabilities + _draw_weighted_team, in the same float
    # order (results are bit-identical), without building dicts for every pick: 2.7 s
    # of CPU for 3,000 runs made this public route a cheap way to slow the API.
    captain_set = set(captain_team_keys)
    pool = sorted(team_key for team_key in event_team_keys if team_key not in captain_set)
    if not captain_team_keys or not pool or simulations <= 0:
        return {}, {}, {}

    safe_scale = max(1e-6, float(scale))
    desirability = [float(desirability_by_team.get(team_key, 0.0)) for team_key in pool]
    exp = math.exp
    first_round_counts: dict[str, int] = dict.fromkeys(event_team_keys, 0)
    second_round_counts: dict[str, int] = dict.fromkeys(event_team_keys, 0)
    draw = random.Random(118).random  # deterministic for reproducible UI outputs  # nosec B311
    # Round 1 picks seed 1 -> seed N, round 2 seed N -> seed 1; only the tally differs.
    tallies = [first_round_counts] * len(captain_team_keys) + [second_round_counts] * len(captain_team_keys)

    for _ in range(simulations):
        available = list(range(len(pool)))
        for counts in tallies:
            if not available:
                break
            top = max([desirability[i] for i in available])
            weights = [exp((desirability[i] - top) / safe_scale) for i in available]
            total = 0.0
            for weight in weights:
                total += weight
            if total <= 0.0:
                uniform = 1.0 / float(len(available))
                rows = sorted([(-uniform, pool[i], i) for i in available])
            else:
                rows = sorted([(-(weight / total), pool[i], i) for weight, i in zip(weights, available)])
            threshold = draw()
            cumulative = 0.0
            chosen = rows[-1][2]
            for negative_probability, _team_key, i in rows:
                cumulative += -negative_probability
                if cumulative >= threshold:
                    chosen = i
                    break
            counts[pool[chosen]] += 1
            available.remove(chosen)

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



@router.get("/{event_key}/alliance-outlook")
def get_alliance_outlook(event_key: str, teams: str = Query(...), db: Session = Depends(get_db)):
    from app.services.ratings.pick_value import alliance_outlook

    if db.get(models.Event, event_key) is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")
    keys = [_normalize_team_key(raw) for raw in teams.split(",") if raw.strip()]
    try:
        return alliance_outlook(db, event_key, keys)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# The simulation is CPU work anyone can ask for, so it's bounded four ways: at most
# MAX_SIMULATIONS runs, rounded inputs whose answers are kept for a minute, a site-wide
# number of fresh simulations a minute, and one at a time per worker (taken before any
# database connection, so a request waiting here never holds one). With only the last
# three, a flood still kept both workers simulating and pages ran ~10x slower.
MAX_SIMULATIONS = 1000
SIMULATIONS_PER_MINUTE = 30
_RUNS_KEY = "selection_model:runs:"
_SELECTION_CACHE_TTL_SEC = 60.0
_SELECTION_CACHE_MAX = 128
_SELECTION_GATE_WAIT_SEC = 2.0
_selection_gate = threading.BoundedSemaphore(1)
_selection_cache: OrderedDict[tuple, tuple[float, dict[str, Any]]] = OrderedDict()
_selection_cache_lock = threading.Lock()


def _cached_selection_model(key: tuple) -> dict[str, Any] | None:
    with _selection_cache_lock:
        hit = _selection_cache.get(key)
        if hit is None:
            return None
        if hit[0] < time.monotonic():
            _selection_cache.pop(key, None)
            return None
        _selection_cache.move_to_end(key)
        return hit[1]


def _fresh_simulation_allowed() -> bool:
    client = get_cache().redis_client
    if client is None:
        return True
    key = _RUNS_KEY + str(int(time.time() // 60))
    try:
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, 120)
        return count <= SIMULATIONS_PER_MINUTE
    except Exception:
        logger.warning("selection model run budget unavailable", exc_info=True)
        return True


def _remember_selection_model(key: tuple, payload: dict[str, Any]) -> None:
    with _selection_cache_lock:
        _selection_cache[key] = (time.monotonic() + _SELECTION_CACHE_TTL_SEC, payload)
        _selection_cache.move_to_end(key)
        while len(_selection_cache) > _SELECTION_CACHE_MAX:
            _selection_cache.popitem(last=False)


@router.get("/{event_key}/selection-model")
def get_selection_model(
    event_key: str,
    rank_weight: float = Query(default=1.0, ge=0.0, le=10.0),
    scale: float = Query(default=35.0, gt=0.0, le=250.0),
    captains: int = Query(default=8, ge=1, le=8),
    # Up to the app's old stepper maximum, so a saved page isn't refused; runs past
    # MAX_SIMULATIONS are clamped and the answer says how many ran.
    simulations: int = Query(default=400, ge=50, le=5000),
    rank_source: str = Query(default="auto", pattern="^(auto|tba|model)$"),
    db: Session = Depends(get_db),
):
    rank_weight = round(float(rank_weight), 1)
    scale = max(0.1, round(float(scale), 1))
    simulations = min(MAX_SIMULATIONS, max(50, int(round(simulations / 50.0)) * 50))
    key = (event_key, rank_weight, scale, int(captains), simulations, rank_source)
    cached = _cached_selection_model(key)
    if cached is not None:
        return cached
    if not _fresh_simulation_allowed() or not _selection_gate.acquire(timeout=_SELECTION_GATE_WAIT_SEC):
        raise HTTPException(
            status_code=503,
            detail="The selection model is busy. Try again in a few seconds.",
            headers={"Retry-After": "5"},
        )
    try:
        cached = _cached_selection_model(key)
        if cached is not None:
            return cached
        payload = _selection_model_payload(
            db, event_key, rank_weight=rank_weight, scale=scale, captains=captains,
            simulations=simulations, rank_source=rank_source,
        )
    finally:
        _selection_gate.release()
    _remember_selection_model(key, payload)
    return payload


def _selection_model_payload(
    db: Session,
    event_key: str,
    *,
    rank_weight: float,
    scale: float,
    captains: int,
    simulations: int,
    rank_source: str,
) -> dict[str, Any]:
    if db.get(models.Event, event_key) is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")
    rank_source_param = rank_source
    event_team_keys = sorted(
        {
            str(row.team_key)
            for row in db.query(models.EventTeam.team_key)
            .filter(models.EventTeam.event_key == event_key)
            .all()
        }
    )
    if len(event_team_keys) < 6:
        raise HTTPException(status_code=400, detail="The selection model needs an event with at least 6 teams.")

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
    if rank_source_param in {"auto", "tba"}:
        tba_rank_map, tba_rank_state = _fetch_event_rank_map_from_tba(event_key)
    if rank_source_param == "tba":
        effective_rank_map = tba_rank_map if tba_rank_map else model_rank_map
        rank_source = "tba" if tba_rank_map else "model_fallback"
    elif rank_source_param == "model":
        effective_rank_map = model_rank_map
        rank_source = "model"
    else:
        effective_rank_map = tba_rank_map if tba_rank_map else model_rank_map
        rank_source = "tba" if tba_rank_map else "model"

    captain_count = max(1, min(int(captains), 8, len(event_team_keys)))
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
        desirability = float(strength_value) - (float(rank_weight) * float(rank_value))
        team_strength_by_key[team_key] = float(strength_value)
        strength_source_by_key[team_key] = strength_source
        desirability_by_key[team_key] = desirability

    first_round_candidates_by_seed: dict[str, list[dict[str, Any]]] = {}
    available_for_first_round: set[str] = set(event_team_keys) - set(captain_team_keys)
    for seed_index, _captain_key in enumerate(captain_team_keys, start=1):
        if not available_for_first_round:
            first_round_candidates_by_seed[str(seed_index)] = []
            continue
        candidates = sorted(available_for_first_round)
        probs = _softmax_probabilities(
            desirability_by_key,
            candidates,
            float(scale),
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
        scale=float(scale),
        simulations=int(simulations),
    )
    expected_board = _build_expected_alliance_board(
        event_team_keys=event_team_keys,
        captain_team_keys=captain_team_keys,
        desirability_by_team=desirability_by_key,
        scale=float(scale),
    )

    top_desirability = sorted(
        event_team_keys,
        key=lambda team_key: (-float(desirability_by_key.get(team_key, 0.0)), team_key),
    )[:20]
    return {
        "ok": True,
        "event_key": event_key,
        "selection_model": {
        "enabled": True,
        "formula": "selection_desirability = strength_score - rank_weight * rank",
        "strength_source_priority": ["scouting_rating_pseudo_epa", "neutral_default"],
        "rank_source": rank_source,
        "rank_source_detail": {
            "requested": rank_source_param,
            "tba_status": tba_rank_state,
        },
        "rank_weight": round(float(rank_weight), 6),
        "scale": round(float(scale), 6),
        "captain_count": captain_count,
        "simulations": int(simulations),
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
    }
