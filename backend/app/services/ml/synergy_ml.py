# Phase 4 — ML pair synergy feature vector builder and training data export.
#
# Builds feature vectors for team-pair synergy regression from:
# - Per-team EventTeamRating subscores (offense, defense, auto, endgame, etc.)
# - Season-prior pair synergy (shrinkage regression baseline)
# - Match count / n_matches_together context
#
# Target: pair synergy residual (deterministic shrinkage value) for supervised
# regression.  At inference time the ML prediction replaces or blends with the
# deterministic shrinkage residual inside compute_match_synergy_projections.

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app.db import models

SYNERGY_PAIR_MODEL_KEY = "synergy_pair"
FEATURE_SCOPE_SYNERGY_PAIR = "synergy_pair"

SYNERGY_PAIR_FEATURE_ORDER: list[str] = [
    # Team A rating features (canonical order: team_key_a < team_key_b)
    "a_rating_0_100",
    "a_throughput_0_100",
    "a_driver_skill_0_100",
    "a_robot_level_0_100",
    "a_endgame_0_100",
    "a_consistency_0_100",
    "a_shift_productivity_0_100",
    "a_auto_contribution_0_100",
    "a_defense_presence_0_100",
    "a_penalty_discipline_0_100",
    "a_confidence_0_1",
    # Team B rating features
    "b_rating_0_100",
    "b_throughput_0_100",
    "b_driver_skill_0_100",
    "b_robot_level_0_100",
    "b_endgame_0_100",
    "b_consistency_0_100",
    "b_shift_productivity_0_100",
    "b_auto_contribution_0_100",
    "b_defense_presence_0_100",
    "b_penalty_discipline_0_100",
    "b_confidence_0_1",
    # Pair interaction features
    "rating_diff_abs",
    "throughput_diff_abs",
    "offense_defense_bridge",
    "auto_combo",
    "endgame_balance",
    "defense_balance",
    "dual_offense",
    # Context features
    "n_matches_together_prior",
    "prior_synergy_points",
    "prior_confidence",
    "event_year",
]

def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    return None

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))

def _subscore(rating: models.EventTeamRating | None, key: str, *, fallback_attr: str | None = None) -> float:
    if rating is None:
        return 50.0
    details = rating.details_json if isinstance(rating.details_json, dict) else {}
    subscores = details.get("subscores") if isinstance(details, dict) else None
    raw = subscores.get(key) if isinstance(subscores, dict) else None
    if not isinstance(raw, (int, float)) and fallback_attr:
        raw = getattr(rating, fallback_attr, None)
    return float(raw) if isinstance(raw, (int, float)) else 50.0

def _rating_float(rating: models.EventTeamRating | None, attr: str, default: float = 50.0) -> float:
    if rating is None:
        return default
    val = getattr(rating, attr, None)
    return float(val) if isinstance(val, (int, float)) else default

def build_synergy_pair_feature_vector(
    rating_a: models.EventTeamRating | None,
    rating_b: models.EventTeamRating | None,
    *,
    prior_synergy_points: float = 0.0,
    prior_confidence: float = 0.0,
    n_matches_together_prior: int = 0,
    event_year: int | None = None,
) -> dict[str, float]:
    a_rating = _rating_float(rating_a, "rating_0_100")
    a_throughput = _subscore(rating_a, "throughput", fallback_attr="throughput")
    a_driver = _rating_float(rating_a, "driver_skill_0_100")
    a_robot = _rating_float(rating_a, "robot_level_0_100")
    a_endgame = _subscore(rating_a, "endgame", fallback_attr="endgame")
    a_consistency = _subscore(rating_a, "consistency", fallback_attr="consistency")
    a_shift = _subscore(rating_a, "shift_productivity", fallback_attr="shift_productivity")
    a_auto = _subscore(rating_a, "auto_contribution")
    a_defense = _subscore(rating_a, "defense_presence")
    a_discipline = _subscore(rating_a, "penalty_discipline")
    a_conf = _rating_float(rating_a, "confidence_0_1", default=0.0)

    b_rating = _rating_float(rating_b, "rating_0_100")
    b_throughput = _subscore(rating_b, "throughput", fallback_attr="throughput")
    b_driver = _rating_float(rating_b, "driver_skill_0_100")
    b_robot = _rating_float(rating_b, "robot_level_0_100")
    b_endgame = _subscore(rating_b, "endgame", fallback_attr="endgame")
    b_consistency = _subscore(rating_b, "consistency", fallback_attr="consistency")
    b_shift = _subscore(rating_b, "shift_productivity", fallback_attr="shift_productivity")
    b_auto = _subscore(rating_b, "auto_contribution")
    b_defense = _subscore(rating_b, "defense_presence")
    b_discipline = _subscore(rating_b, "penalty_discipline")
    b_conf = _rating_float(rating_b, "confidence_0_1", default=0.0)

    # Interaction features (normalized to 0-1 for stability)
    offense_a = _clamp((0.62 * a_throughput + 0.38 * a_shift) / 100.0, 0.0, 1.0)
    offense_b = _clamp((0.62 * b_throughput + 0.38 * b_shift) / 100.0, 0.0, 1.0)

    return {
        "a_rating_0_100": a_rating,
        "a_throughput_0_100": a_throughput,
        "a_driver_skill_0_100": a_driver,
        "a_robot_level_0_100": a_robot,
        "a_endgame_0_100": a_endgame,
        "a_consistency_0_100": a_consistency,
        "a_shift_productivity_0_100": a_shift,
        "a_auto_contribution_0_100": a_auto,
        "a_defense_presence_0_100": a_defense,
        "a_penalty_discipline_0_100": a_discipline,
        "a_confidence_0_1": a_conf,
        "b_rating_0_100": b_rating,
        "b_throughput_0_100": b_throughput,
        "b_driver_skill_0_100": b_driver,
        "b_robot_level_0_100": b_robot,
        "b_endgame_0_100": b_endgame,
        "b_consistency_0_100": b_consistency,
        "b_shift_productivity_0_100": b_shift,
        "b_auto_contribution_0_100": b_auto,
        "b_defense_presence_0_100": b_defense,
        "b_penalty_discipline_0_100": b_discipline,
        "b_confidence_0_1": b_conf,
        "rating_diff_abs": abs(a_rating - b_rating),
        "throughput_diff_abs": abs(a_throughput - b_throughput),
        "offense_defense_bridge": max(offense_a * (b_defense / 100.0), offense_b * (a_defense / 100.0)),
        "auto_combo": max((a_auto / 100.0) * offense_b, (b_auto / 100.0) * offense_a),
        "endgame_balance": _clamp(1.0 - abs(a_endgame - b_endgame) / 100.0, 0.0, 1.0),
        "defense_balance": _clamp(1.0 - abs(a_defense - b_defense) / 100.0, 0.0, 1.0),
        "dual_offense": offense_a * offense_b,
        "n_matches_together_prior": float(max(0, n_matches_together_prior)),
        "prior_synergy_points": float(prior_synergy_points),
        "prior_confidence": _clamp(float(prior_confidence), 0.0, 1.0),
        "event_year": float(event_year or 0),
    }

def _canonical_pair(team_a: str, team_b: str) -> tuple[str, str]:
    return (team_a, team_b) if team_a <= team_b else (team_b, team_a)

def rebuild_synergy_pair_feature_snapshots(
    db: Session,
    *,
    event_keys: set[str],
    source_version: str,
    year_map: dict[str, int],
) -> dict[str, int]:
    if not event_keys:
        return {"rows_written": 0, "skipped_no_prior": 0}

    from app.services.ml.shadow import _latest_year, _split_tag_for_year

    latest_year = _latest_year(year_map)
    rows_written = 0
    skipped_no_prior = 0

    for event_key in sorted(event_keys):
        season_candidate = event_key[:4]
        if not season_candidate.isdigit():
            continue
        season = int(season_candidate)

        rating_rows = (
            db.query(models.EventTeamRating)
            .filter(models.EventTeamRating.event_key == event_key)
            .all()
        )
        rating_by_team = {str(r.team_key): r for r in rating_rows}
        team_keys = sorted(rating_by_team.keys())

        prior_rows = (
            db.query(models.TeamPairSynergyPrior)
            .filter(
                models.TeamPairSynergyPrior.season == season,
            )
            .all()
        )
        prior_by_pair: dict[tuple[str, str], models.TeamPairSynergyPrior] = {}
        for row in prior_rows:
            prior_by_pair[(_canonical_pair(row.team_key_a, row.team_key_b))] = row

        event_pair_rows = (
            db.query(models.TeamPairSynergyEvent)
            .filter(models.TeamPairSynergyEvent.event_key == event_key)
            .all()
        )
        event_by_pair: dict[tuple[str, str], models.TeamPairSynergyEvent] = {}
        for row in event_pair_rows:
            event_by_pair[(_canonical_pair(row.team_key_a, row.team_key_b))] = row

        from itertools import combinations

        for team_a, team_b in combinations(team_keys, 2):
            pair = _canonical_pair(team_a, team_b)
            event_value = event_by_pair.get(pair)
            prior = prior_by_pair.get(pair)

            if event_value is None and prior is None:
                skipped_no_prior += 1
                continue

            target_synergy = float(event_value.synergy_points_event) if event_value is not None else None
            if target_synergy is None:
                skipped_no_prior += 1
                continue

            feature_vector = build_synergy_pair_feature_vector(
                rating_by_team.get(pair[0]),
                rating_by_team.get(pair[1]),
                prior_synergy_points=float(prior.synergy_points_prior) if prior else 0.0,
                prior_confidence=float(prior.confidence_prior) if prior else 0.0,
                n_matches_together_prior=int(prior.n_matches_together) if prior else 0,
                event_year=season,
            )

            split_tag = _split_tag_for_year(year_map.get(event_key), latest_year)
            snapshot_key = f"{FEATURE_SCOPE_SYNERGY_PAIR}:{event_key}:{pair[0]}:{pair[1]}:{source_version}"
            db.merge(
                models.MLFeatureSnapshot(
                    snapshot_key=snapshot_key,
                    scope=FEATURE_SCOPE_SYNERGY_PAIR,
                    event_key=event_key,
                    team_key=pair[0],
                    alliance_color=None,
                    feature_vector=feature_vector,
                    target={"synergy_points": float(target_synergy)},
                    split_tag=split_tag,
                    source_version=source_version,
                )
            )
            rows_written += 1

    return {"rows_written": rows_written, "skipped_no_prior": skipped_no_prior}
