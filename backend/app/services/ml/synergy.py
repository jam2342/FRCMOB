from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.hash_utils import stable_payload_hash
from app.services.utils import (
    _clamp,
    _mean as _mean_or_none,
    _weighted_mean as _weighted_mean_or_none,
    _weighted_median,
)

SYNERGY_MODEL_VERSION = "synergy_v4_rolefit_tuned"
QUALITY_THRESHOLD_DEFAULT = 0.70
ROLE_ADJUSTMENT_VERSION = "role_fit_v1"
SYNERGY_DISCIPLINE_RISK_MULTIPLIER = max(0.0, float(settings.synergy_discipline_risk_multiplier))
SYNERGY_EPA_PAIR_WEIGHT = max(0.0, min(1.0, float(settings.synergy_epa_pair_weight)))
PAIR_PRIOR_SHRINK_K = max(1.0, float(settings.synergy_pair_prior_shrink_k))
PAIR_EVENT_SHRINK_K = max(1.0, float(settings.synergy_pair_event_shrink_k))
PAIR_EVENT_BLEND_K = max(1.0, float(settings.synergy_pair_event_blend_k))

def _mean(values: list[float]) -> float:
    # Synergy-local mean: returns 0.0 instead of None on empty input.
    return _mean_or_none(values) or 0.0

def _weighted_mean(pairs: list[tuple[float, float]]) -> float:
    # Synergy-local weighted mean: returns 0.0 instead of None.
    return _weighted_mean_or_none(pairs) or 0.0

def _fit_linear_model(x_values: list[float], y_values: list[float]) -> tuple[float, float]:
    if len(x_values) != len(y_values) or not x_values:
        return 0.0, 1.0
    if len(x_values) == 1:
        return float(y_values[0]), 0.0
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    denom = sum((x - x_mean) ** 2 for x in x_values)
    if denom <= 1e-9:
        return y_mean, 0.0
    numer = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
    slope = numer / denom
    intercept = y_mean - (slope * x_mean)
    return intercept, slope

def _canonical_pair(team_a: str, team_b: str) -> tuple[str, str]:
    if team_a <= team_b:
        return team_a, team_b
    return team_b, team_a

def _season_from_event_key(event_key: str) -> int | None:
    prefix = (event_key or "")[:4]
    if prefix.isdigit():
        return int(prefix)
    return None

def _quality_and_coverage(
    throughput: models.TeamMatchThroughput,
    quality_by_run_id: dict[int, float],
) -> tuple[float, float]:
    run_quality = quality_by_run_id.get(int(throughput.analysis_run_id), 0.0)
    coverage_score = 0.0
    if isinstance(throughput.metric_coverage, dict):
        raw = throughput.metric_coverage.get("coverage_score")
        if isinstance(raw, (int, float)):
            coverage_score = float(raw)
    coverage_score = _clamp(coverage_score, 0.0, 1.0)
    return run_quality, coverage_score

def _percentile_scores(raw_by_key: dict[str, float | None]) -> dict[str, float]:
    valid = [
        (key, float(value))
        for key, value in raw_by_key.items()
        if value is not None and not math.isnan(float(value))
    ]
    if not valid:
        return {key: 50.0 for key in raw_by_key}
    valid.sort(key=lambda row: row[1])
    n = len(valid)
    result = {key: 50.0 for key in raw_by_key}
    for idx, (key, _) in enumerate(valid):
        result[key] = 50.0 if n == 1 else (idx / (n - 1)) * 100.0
    return result

def _event_team_keys(db: Session, event_key: str) -> set[str]:
    return {
        row[0]
        for row in (
            db.query(models.EventTeam.team_key)
            .filter(models.EventTeam.event_key == event_key)
            .all()
        )
    }

def _season_team_keys(db: Session, season: int) -> set[str]:
    return {
        row[0]
        for row in (
            db.query(models.EventTeam.team_key)
            .join(models.Event, models.Event.event_key == models.EventTeam.event_key)
            .filter(models.Event.year == season)
            .all()
        )
    }

def _quality_map(db: Session, run_ids: set[int] | None = None) -> dict[int, float]:
    query = db.query(models.AnalysisQuality.run_id, models.AnalysisQuality.overall_quality_score)
    if run_ids is not None:
        normalized_ids = sorted(
            {
                int(run_id)
                for run_id in run_ids
                if isinstance(run_id, int) or (isinstance(run_id, str) and run_id.isdigit())
            }
        )
        if not normalized_ids:
            return {}
        query = query.filter(models.AnalysisQuality.run_id.in_(normalized_ids))
    rows = query.all()
    return {int(run_id): float(overall_quality_score or 0.0) for run_id, overall_quality_score in rows}

def _throughputs_for_scope(
    db: Session,
    *,
    season: int | None = None,
    event_key: str | None = None,
) -> list[models.TeamMatchThroughput]:
    query = db.query(models.TeamMatchThroughput)
    if event_key:
        query = query.filter(models.TeamMatchThroughput.event_key == event_key)
    elif season is not None:
        query = query.filter(models.TeamMatchThroughput.event_key.like(f"{season}%"))
    return query.all()

def _normalized_subscore(
    rating: models.EventTeamRating | None,
    key: str,
    *,
    fallback_attr: str | None = None,
) -> float:
    if rating is None:
        return 0.0
    details = rating.details_json if isinstance(rating.details_json, dict) else {}
    subscores = details.get("subscores") if isinstance(details, dict) else None
    raw_value = subscores.get(key) if isinstance(subscores, dict) else None
    if not isinstance(raw_value, (int, float)) and fallback_attr:
        fallback = getattr(rating, fallback_attr, None)
        raw_value = fallback if isinstance(fallback, (int, float)) else None
    if not isinstance(raw_value, (int, float)):
        return 0.0
    return _clamp(float(raw_value) / 100.0, 0.0, 1.0)

def _normalized_epa_percentile(rating: models.EventTeamRating | None) -> float | None:
    if rating is None:
        return None
    details = rating.details_json if isinstance(rating.details_json, dict) else {}
    epa_context = details.get("epa_context") if isinstance(details, dict) else None
    raw_percentile = epa_context.get("percentile") if isinstance(epa_context, dict) else None
    if not isinstance(raw_percentile, (int, float)):
        return None
    return _clamp(float(raw_percentile) / 100.0, 0.0, 1.0)

def compute_pair_role_adjustment(
    rating_by_team: dict[str, models.EventTeamRating],
    team_key_a: str,
    team_key_b: str,
) -> dict[str, Any]:
    rating_a = rating_by_team.get(team_key_a)
    rating_b = rating_by_team.get(team_key_b)
    coverage = 0.5 * float((1 if rating_a is not None else 0) + (1 if rating_b is not None else 0))
    if coverage <= 0.0:
        return {
            "net_adjustment_points": 0.0,
            "complement_bonus_points": 0.0,
            "risk_penalty_points": 0.0,
            "profile_confidence_0_1": 0.0,
            "profile_coverage_0_1": 0.0,
            "source": "no_ratings",
            "role_axes": {},
        }

    throughput_a = _normalized_subscore(rating_a, "throughput", fallback_attr="throughput")
    throughput_b = _normalized_subscore(rating_b, "throughput", fallback_attr="throughput")
    shift_a = _normalized_subscore(rating_a, "shift_productivity", fallback_attr="shift_productivity")
    shift_b = _normalized_subscore(rating_b, "shift_productivity", fallback_attr="shift_productivity")
    auto_a = _normalized_subscore(rating_a, "auto_contribution")
    auto_b = _normalized_subscore(rating_b, "auto_contribution")
    endgame_a = _normalized_subscore(rating_a, "endgame", fallback_attr="endgame")
    endgame_b = _normalized_subscore(rating_b, "endgame", fallback_attr="endgame")
    defense_a = _normalized_subscore(rating_a, "defense_presence")
    defense_b = _normalized_subscore(rating_b, "defense_presence")
    consistency_a = _normalized_subscore(rating_a, "consistency", fallback_attr="consistency")
    consistency_b = _normalized_subscore(rating_b, "consistency", fallback_attr="consistency")
    discipline_a = _normalized_subscore(rating_a, "penalty_discipline")
    discipline_b = _normalized_subscore(rating_b, "penalty_discipline")
    epa_a = _normalized_epa_percentile(rating_a)
    epa_b = _normalized_epa_percentile(rating_b)
    rating_confidence_a = _clamp(float(rating_a.confidence_0_1 or 0.0), 0.0, 1.0) if rating_a else 0.0
    rating_confidence_b = _clamp(float(rating_b.confidence_0_1 or 0.0), 0.0, 1.0) if rating_b else 0.0

    offense_a = _clamp((0.62 * throughput_a) + (0.38 * shift_a), 0.0, 1.0)
    offense_b = _clamp((0.62 * throughput_b) + (0.38 * shift_b), 0.0, 1.0)
    support_a = _clamp((0.56 * endgame_a) + (0.44 * auto_a), 0.0, 1.0)
    support_b = _clamp((0.56 * endgame_b) + (0.44 * auto_b), 0.0, 1.0)

    offense_defense_bridge = max(offense_a * defense_b, offense_b * defense_a)
    support_bridge = _clamp((support_a + support_b) / 2.0, 0.0, 1.0)
    dual_offense = offense_a * offense_b
    auto_combo = max(auto_a * offense_b, auto_b * offense_a)
    endgame_balance = _clamp(1.0 - abs(endgame_a - endgame_b), 0.0, 1.0)
    defense_balance = _clamp(1.0 - abs(defense_a - defense_b), 0.0, 1.0)
    epa_values = [value for value in (epa_a, epa_b) if isinstance(value, (int, float))]
    epa_pair_baseline = _clamp(sum(epa_values) / len(epa_values), 0.0, 1.0) if epa_values else 0.0
    epa_alignment = (
        _clamp(1.0 - abs(float(epa_a) - float(epa_b)), 0.0, 1.0)
        if epa_a is not None and epa_b is not None
        else 0.65
    )
    epa_boost = SYNERGY_EPA_PAIR_WEIGHT * epa_pair_baseline * ((0.6 * epa_alignment) + 0.4)

    discipline_risk = 1.0 - _clamp((discipline_a + discipline_b) / 2.0, 0.0, 1.0)
    consistency_risk = 1.0 - _clamp((consistency_a + consistency_b) / 2.0, 0.0, 1.0)
    role_overlap_penalty = _clamp(
        max(0.0, ((defense_a + defense_b) / 2.0) - 0.72)
        * max(0.0, 0.55 - ((offense_a + offense_b) / 2.0)),
        0.0,
        1.0,
    )
    low_discipline_pair_penalty = _clamp(0.45 - ((discipline_a + discipline_b) / 2.0), 0.0, 1.0)

    base_complement_bonus = (
        (0.22 * offense_defense_bridge)
        + (0.11 * support_bridge)
        + (0.06 * dual_offense)
        + (0.08 * auto_combo)
        + (0.04 * endgame_balance)
        + (0.03 * defense_balance)
        + (0.07 * epa_boost)
    )
    base_risk_penalty = (
        ((0.11 * SYNERGY_DISCIPLINE_RISK_MULTIPLIER) * discipline_risk)
        + (0.09 * consistency_risk)
        + (0.07 * role_overlap_penalty)
        + (0.05 * low_discipline_pair_penalty)
    )
    profile_confidence = _clamp((rating_confidence_a + rating_confidence_b) / 2.0, 0.0, 1.0)
    confidence_multiplier = (0.50 + (0.50 * profile_confidence)) * (0.68 + (0.32 * coverage))

    complement_bonus_points = base_complement_bonus * confidence_multiplier
    risk_penalty_points = base_risk_penalty * confidence_multiplier
    net_adjustment_points = complement_bonus_points - risk_penalty_points

    return {
        "net_adjustment_points": round(float(net_adjustment_points), 6),
        "complement_bonus_points": round(float(complement_bonus_points), 6),
        "risk_penalty_points": round(float(risk_penalty_points), 6),
        "profile_confidence_0_1": round(float(profile_confidence), 6),
        "profile_coverage_0_1": round(float(coverage), 6),
        "source": "event_ratings",
        "role_axes": {
            "offense_a": round(offense_a, 5),
            "offense_b": round(offense_b, 5),
            "support_a": round(support_a, 5),
            "support_b": round(support_b, 5),
            "defense_a": round(defense_a, 5),
            "defense_b": round(defense_b, 5),
            "discipline_a": round(discipline_a, 5),
            "discipline_b": round(discipline_b, 5),
            "consistency_a": round(consistency_a, 5),
            "consistency_b": round(consistency_b, 5),
            "epa_a": round(float(epa_a), 5) if epa_a is not None else None,
            "epa_b": round(float(epa_b), 5) if epa_b is not None else None,
            "epa_pair_baseline": round(epa_pair_baseline, 5),
            "epa_alignment": round(epa_alignment, 5),
            "epa_boost": round(epa_boost, 5),
            "offense_defense_bridge": round(offense_defense_bridge, 5),
            "auto_combo": round(auto_combo, 5),
            "endgame_balance": round(endgame_balance, 5),
            "defense_balance": round(defense_balance, 5),
            "role_overlap_penalty": round(role_overlap_penalty, 5),
            "low_discipline_pair_penalty": round(low_discipline_pair_penalty, 5),
            "discipline_risk": round(discipline_risk, 5),
            "consistency_risk": round(consistency_risk, 5),
        },
    }

def _build_matchup_signals(
    *,
    role_adjustment: dict[str, Any],
    prior_n: int,
    event_n: int,
) -> list[dict[str, Any]]:
    axes = role_adjustment.get("role_axes") if isinstance(role_adjustment.get("role_axes"), dict) else {}
    profile_conf = _clamp(float(role_adjustment.get("profile_confidence_0_1") or 0.0), 0.0, 1.0)
    sample_size = max(0, int(event_n if event_n > 0 else prior_n))

    def _signal(
        *,
        label: str,
        metric: str,
        value: float,
        baseline: float,
        rationale: str,
        impact: str,
    ) -> dict[str, Any]:
        return {
            "label": label,
            "metric": metric,
            "metric_value": round(float(value), 5),
            "delta": round(float(value) - float(baseline), 5),
            "baseline": round(float(baseline), 5),
            "confidence_0_1": round(profile_conf, 4),
            "sample_size": int(sample_size),
            "impact": impact,
            "rationale": rationale,
        }

    offense_bridge = float(axes.get("offense_defense_bridge") or 0.0)
    cycle_balance = _clamp(
        1.0 - abs(float(axes.get("offense_a") or 0.0) - float(axes.get("offense_b") or 0.0)),
        0.0,
        1.0,
    )
    defense_overlap_penalty = float(axes.get("role_overlap_penalty") or 0.0)
    epa_alignment = float(axes.get("epa_alignment") or 0.0)

    signals: list[dict[str, Any]] = []
    if offense_bridge >= 0.55:
        signals.append(
            _signal(
                label="Role-fit bridge is strong",
                metric="offense_defense_bridge",
                value=offense_bridge,
                baseline=0.50,
                rationale="One robot's offensive profile is complemented by the partner's defense profile.",
                impact="upside",
            )
        )
    elif offense_bridge <= 0.25:
        signals.append(
            _signal(
                label="Role-fit bridge is weak",
                metric="offense_defense_bridge",
                value=offense_bridge,
                baseline=0.40,
                rationale="Pair lacks complementary offense/defense role interaction.",
                impact="risk_up",
            )
        )

    if cycle_balance >= 0.72:
        signals.append(
            _signal(
                label="Cycle balance is healthy",
                metric="cycle_balance",
                value=cycle_balance,
                baseline=0.60,
                rationale="Both robots project similar offensive cycle pace, reducing bottlenecks.",
                impact="upside",
            )
        )
    elif cycle_balance <= 0.35:
        signals.append(
            _signal(
                label="Cycle imbalance risk",
                metric="cycle_balance",
                value=cycle_balance,
                baseline=0.50,
                rationale="Large offensive pacing gap may create dead time or handoff pressure.",
                impact="risk_up",
            )
        )

    if defense_overlap_penalty >= 0.30:
        signals.append(
            _signal(
                label="Defense overlap penalty",
                metric="role_overlap_penalty",
                value=defense_overlap_penalty,
                baseline=0.12,
                rationale="Both robots trend into overlapping defensive roles, limiting total alliance throughput.",
                impact="risk_up",
            )
        )
    elif defense_overlap_penalty <= 0.08:
        signals.append(
            _signal(
                label="Defense role overlap is low",
                metric="role_overlap_penalty",
                value=defense_overlap_penalty,
                baseline=0.12,
                rationale="Role distribution avoids defensive duplication and preserves scoring lanes.",
                impact="risk_down",
            )
        )

    if epa_alignment >= 0.72:
        signals.append(
            _signal(
                label="EPA alignment supports pairing",
                metric="epa_alignment",
                value=epa_alignment,
                baseline=0.60,
                rationale="External EPA trends align closely, improving predictability of pair output.",
                impact="upside",
            )
        )

    signals.sort(key=lambda item: abs(float(item.get("delta") or 0.0)), reverse=True)
    return signals[:4]

def compute_team_season_strength(
    db: Session,
    season: int,
    *,
    model_version: str = SYNERGY_MODEL_VERSION,
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    commit: bool = True,
) -> dict[str, Any]:
    rows = _throughputs_for_scope(db, season=season)
    quality_by_run_id = _quality_map(
        db,
        run_ids={int(row.analysis_run_id) for row in rows if row.analysis_run_id is not None},
    )
    team_keys = _season_team_keys(db, season)
    grouped_values: dict[str, list[tuple[float, float]]] = defaultdict(list)
    grouped_qualities: dict[str, list[float]] = defaultdict(list)
    grouped_coverages: dict[str, list[float]] = defaultdict(list)

    for row in rows:
        if row.active_bps is None:
            continue
        quality_score, coverage_score = _quality_and_coverage(row, quality_by_run_id)
        if quality_score < quality_threshold:
            continue
        weight = max(0.0, quality_score * max(0.1, coverage_score))
        grouped_values[row.team_key].append((float(row.active_bps), weight))
        grouped_qualities[row.team_key].append(float(quality_score))
        grouped_coverages[row.team_key].append(float(coverage_score))
        team_keys.add(row.team_key)

    params_hash = stable_payload_hash(
        {
            "season": season,
            "model_version": model_version,
            "quality_threshold": quality_threshold,
            "scope": "team_season_strength",
        }
    )
    now = datetime.now(timezone.utc)
    computed_count = 0
    for team_key in sorted(team_keys):
        values = grouped_values.get(team_key, [])
        matches_used = len(values)
        strength_active_bps = _weighted_median(values)
        avg_quality = _mean(grouped_qualities.get(team_key, []))
        coverage_factor = _mean(grouped_coverages.get(team_key, []))
        good_match_factor = _clamp(matches_used / 10.0, 0.0, 1.0)
        confidence_0_1 = _clamp(
            0.1
            + (0.45 * good_match_factor)
            + (0.25 * avg_quality)
            + (0.20 * coverage_factor),
            0.0,
            1.0,
        )
        if strength_active_bps is None:
            confidence_0_1 = min(confidence_0_1, 0.15)

        db.merge(
            models.TeamSeasonStrength(
                season=season,
                team_key=team_key,
                model_version=model_version,
                strength_active_bps=(
                    float(strength_active_bps)
                    if strength_active_bps is not None
                    else None
                ),
                confidence_0_1=float(confidence_0_1),
                matches_used=int(matches_used),
                avg_quality=float(avg_quality),
                coverage_factor=float(coverage_factor),
                params_hash=params_hash,
                updated_at=now,
            )
        )
        computed_count += 1
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "ok": True,
        "season": season,
        "model_version": model_version,
        "params_hash": params_hash,
        "count": computed_count,
    }

def _load_season_strength_map(
    db: Session,
    season: int,
    model_version: str,
) -> dict[str, models.TeamSeasonStrength]:
    rows = (
        db.query(models.TeamSeasonStrength)
        .filter(
            models.TeamSeasonStrength.season == season,
            models.TeamSeasonStrength.model_version == model_version,
        )
        .all()
    )
    return {row.team_key: row for row in rows}

def _load_event_strength_map(
    db: Session,
    event_key: str,
    model_version: str,
) -> dict[str, models.TeamEventThroughputStrength]:
    rows = (
        db.query(models.TeamEventThroughputStrength)
        .filter(
            models.TeamEventThroughputStrength.event_key == event_key,
            models.TeamEventThroughputStrength.model_version == model_version,
        )
        .all()
    )
    return {row.team_key: row for row in rows}

def _build_alliance_samples(
    db: Session,
    *,
    strengths_by_team: dict[str, float],
    quality_threshold: float,
    season: int | None = None,
    event_key: str | None = None,
) -> list[dict[str, Any]]:
    throughputs = _throughputs_for_scope(db, season=season, event_key=event_key)
    quality_by_run_id = _quality_map(
        db,
        run_ids={int(row.analysis_run_id) for row in throughputs if row.analysis_run_id is not None},
    )
    throughput_map: dict[tuple[str, str], models.TeamMatchThroughput] = {}
    quality_map: dict[tuple[str, str], float] = {}
    coverage_map: dict[tuple[str, str], float] = {}
    for row in throughputs:
        quality_score, coverage_score = _quality_and_coverage(row, quality_by_run_id)
        if quality_score < quality_threshold or row.active_bps is None:
            continue
        key = (row.match_key, row.team_key)
        throughput_map[key] = row
        quality_map[key] = quality_score
        coverage_map[key] = coverage_score

    match_keys = sorted({key[0] for key in throughput_map})
    if not match_keys:
        return []

    match_team_rows = (
        db.query(models.MatchTeam)
        .filter(models.MatchTeam.match_key.in_(match_keys))
        .all()
    )
    teams_by_match_alliance: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in match_team_rows:
        teams_by_match_alliance[(row.match_key, row.alliance)].append(row.team_key)

    samples: list[dict[str, Any]] = []
    for (match_key, alliance), teams in teams_by_match_alliance.items():
        if alliance not in {"red", "blue"}:
            continue
        if len(teams) < 2:
            continue
        observed_components: list[float] = []
        qualities: list[float] = []
        coverages: list[float] = []
        strength_components: list[float] = []
        present_teams: list[str] = []
        for team_key in sorted(teams):
            throughput = throughput_map.get((match_key, team_key))
            if throughput is None:
                continue
            observed_components.append(float(throughput.active_bps or 0.0))
            strength_components.append(float(strengths_by_team.get(team_key, 0.0)))
            qualities.append(float(quality_map.get((match_key, team_key), 0.0)))
            coverages.append(float(coverage_map.get((match_key, team_key), 0.0)))
            present_teams.append(team_key)
        if len(observed_components) < 2:
            continue
        samples.append(
            {
                "match_key": match_key,
                "alliance": alliance,
                "teams": present_teams,
                "observed_y": float(sum(observed_components)),
                "strength_sum": float(sum(strength_components)),
                "quality": _mean(qualities),
                "coverage": _mean(coverages),
            }
        )
    return samples

def _season_expected_model(
    db: Session,
    season: int,
    model_version: str,
    *,
    quality_threshold: float,
) -> tuple[float, float, int]:
    strength_rows = _load_season_strength_map(db, season, model_version)
    strengths = {
        team_key: float(row.strength_active_bps or 0.0)
        for team_key, row in strength_rows.items()
    }
    samples = _build_alliance_samples(
        db,
        strengths_by_team=strengths,
        quality_threshold=quality_threshold,
        season=season,
    )
    if not samples:
        return 0.0, 1.0, 0
    x_values = [float(sample["strength_sum"]) for sample in samples]
    y_values = [float(sample["observed_y"]) for sample in samples]
    intercept, slope = _fit_linear_model(x_values, y_values)
    return intercept, slope, len(samples)

def compute_team_pair_synergy_prior(
    db: Session,
    season: int,
    *,
    model_version: str = SYNERGY_MODEL_VERSION,
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    shrink_k: float = PAIR_PRIOR_SHRINK_K,
    commit: bool = True,
) -> dict[str, Any]:
    compute_team_season_strength(
        db,
        season,
        model_version=model_version,
        quality_threshold=quality_threshold,
        commit=False,
    )
    strengths = _load_season_strength_map(db, season, model_version)
    strength_values = {
        team_key: float(row.strength_active_bps or 0.0)
        for team_key, row in strengths.items()
    }
    samples = _build_alliance_samples(
        db,
        strengths_by_team=strength_values,
        quality_threshold=quality_threshold,
        season=season,
    )
    if not samples:
        return {
            "ok": True,
            "season": season,
            "model_version": model_version,
            "count": 0,
            "params_hash": stable_payload_hash({"scope": "pair_prior_empty", "season": season}),
        }

    x_values = [float(sample["strength_sum"]) for sample in samples]
    y_values = [float(sample["observed_y"]) for sample in samples]
    intercept, slope = _fit_linear_model(x_values, y_values)
    bucket: dict[tuple[str, str], list[tuple[float, float, float, float]]] = defaultdict(list)
    for sample in samples:
        expected = intercept + (slope * float(sample["strength_sum"]))
        residual = float(sample["observed_y"]) - expected
        weight = max(0.05, float(sample["quality"]) * max(0.1, float(sample["coverage"])))
        for team_a, team_b in combinations(sorted(sample["teams"]), 2):
            pair = _canonical_pair(team_a, team_b)
            bucket[pair].append((residual, weight, float(sample["quality"]), float(sample["coverage"])))

    params_hash = stable_payload_hash(
        {
            "season": season,
            "model_version": model_version,
            "quality_threshold": quality_threshold,
            "shrink_k": shrink_k,
            "sample_count": len(samples),
        }
    )
    now = datetime.now(timezone.utc)
    db.query(models.TeamPairSynergyPrior).filter(
        models.TeamPairSynergyPrior.season == season,
        models.TeamPairSynergyPrior.model_version == model_version,
    ).delete(synchronize_session=False)

    for (team_key_a, team_key_b), values in bucket.items():
        weighted_residual = _weighted_mean([(value, weight) for value, weight, _, _ in values])
        n_matches = len(values)
        shrink = n_matches / (n_matches + max(1.0, shrink_k))
        avg_quality = _weighted_mean([(quality, weight) for _, weight, quality, _ in values])
        coverage_factor = _weighted_mean([(coverage, weight) for _, weight, _, coverage in values])
        synergy_points = weighted_residual * shrink
        confidence = _clamp(shrink * avg_quality * coverage_factor, 0.0, 1.0)
        db.add(
            models.TeamPairSynergyPrior(
                season=season,
                team_key_a=team_key_a,
                team_key_b=team_key_b,
                model_version=model_version,
                synergy_points_prior=float(synergy_points),
                confidence_prior=float(confidence),
                n_matches_together=int(n_matches),
                avg_quality=float(avg_quality),
                residual_mean_raw=float(weighted_residual),
                params_hash=params_hash,
                updated_at=now,
            )
        )
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "ok": True,
        "season": season,
        "model_version": model_version,
        "params_hash": params_hash,
        "count": len(bucket),
        "alliance_sample_count": len(samples),
        "expected_model": {
            "intercept": round(intercept, 5),
            "slope": round(slope, 5),
        },
    }

def compute_team_event_throughput_strength(
    db: Session,
    event_key: str,
    *,
    model_version: str = SYNERGY_MODEL_VERSION,
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    commit: bool = True,
) -> dict[str, Any]:
    season = _season_from_event_key(event_key)
    if season is None:
        raise RuntimeError(f"Unable to infer season from event key {event_key}")

    rows = _throughputs_for_scope(db, event_key=event_key)
    quality_by_run_id = _quality_map(
        db,
        run_ids={int(row.analysis_run_id) for row in rows if row.analysis_run_id is not None},
    )
    team_keys = _event_team_keys(db, event_key)
    grouped_values: dict[str, list[tuple[float, float]]] = defaultdict(list)
    grouped_qualities: dict[str, list[float]] = defaultdict(list)
    grouped_coverages: dict[str, list[float]] = defaultdict(list)

    for row in rows:
        if row.active_bps is None:
            continue
        quality_score, coverage_score = _quality_and_coverage(row, quality_by_run_id)
        if quality_score < quality_threshold:
            continue
        weight = max(0.0, quality_score * max(0.1, coverage_score))
        grouped_values[row.team_key].append((float(row.active_bps), weight))
        grouped_qualities[row.team_key].append(float(quality_score))
        grouped_coverages[row.team_key].append(float(coverage_score))
        team_keys.add(row.team_key)

    params_hash = stable_payload_hash(
        {
            "scope": "team_event_strength",
            "event_key": event_key,
            "model_version": model_version,
            "quality_threshold": quality_threshold,
        }
    )
    now = datetime.now(timezone.utc)
    db.query(models.TeamEventThroughputStrength).filter(
        models.TeamEventThroughputStrength.event_key == event_key,
        models.TeamEventThroughputStrength.model_version == model_version,
    ).delete(synchronize_session=False)

    for team_key in sorted(team_keys):
        values = grouped_values.get(team_key, [])
        matches_used = len(values)
        strength_active_bps = _weighted_median(values)
        avg_quality = _mean(grouped_qualities.get(team_key, []))
        coverage_factor = _mean(grouped_coverages.get(team_key, []))
        good_match_factor = _clamp(matches_used / 8.0, 0.0, 1.0)
        confidence_0_1 = _clamp(
            0.1
            + (0.45 * good_match_factor)
            + (0.25 * avg_quality)
            + (0.2 * coverage_factor),
            0.0,
            1.0,
        )
        if strength_active_bps is None:
            confidence_0_1 = min(confidence_0_1, 0.15)
        db.add(
            models.TeamEventThroughputStrength(
                event_key=event_key,
                team_key=team_key,
                model_version=model_version,
                season=season,
                strength_active_bps=(
                    float(strength_active_bps)
                    if strength_active_bps is not None
                    else None
                ),
                confidence_0_1=float(confidence_0_1),
                matches_used=int(matches_used),
                avg_quality=float(avg_quality),
                coverage_factor=float(coverage_factor),
                params_hash=params_hash,
                updated_at=now,
            )
        )
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "ok": True,
        "event_key": event_key,
        "season": season,
        "model_version": model_version,
        "params_hash": params_hash,
        "count": len(team_keys),
    }

def compute_team_pair_synergy_event(
    db: Session,
    event_key: str,
    *,
    model_version: str = SYNERGY_MODEL_VERSION,
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    shrink_k: float = PAIR_EVENT_SHRINK_K,
    commit: bool = True,
) -> dict[str, Any]:
    season = _season_from_event_key(event_key)
    if season is None:
        raise RuntimeError(f"Unable to infer season from event key {event_key}")

    compute_team_event_throughput_strength(
        db,
        event_key,
        model_version=model_version,
        quality_threshold=quality_threshold,
        commit=False,
    )
    strengths = _load_event_strength_map(db, event_key, model_version)
    strength_values = {
        team_key: float(row.strength_active_bps or 0.0)
        for team_key, row in strengths.items()
    }
    samples = _build_alliance_samples(
        db,
        strengths_by_team=strength_values,
        quality_threshold=quality_threshold,
        event_key=event_key,
    )
    if not samples:
        db.query(models.TeamPairSynergyEvent).filter(
            models.TeamPairSynergyEvent.event_key == event_key,
            models.TeamPairSynergyEvent.model_version == model_version,
        ).delete(synchronize_session=False)
        if commit:
            db.commit()
        else:
            db.flush()
        return {
            "ok": True,
            "event_key": event_key,
            "season": season,
            "model_version": model_version,
            "params_hash": stable_payload_hash({"scope": "pair_event_empty", "event_key": event_key}),
            "count": 0,
        }

    x_values = [float(sample["strength_sum"]) for sample in samples]
    y_values = [float(sample["observed_y"]) for sample in samples]
    intercept, slope = _fit_linear_model(x_values, y_values)
    bucket: dict[tuple[str, str], list[tuple[float, float, float, float]]] = defaultdict(list)
    for sample in samples:
        expected = intercept + (slope * float(sample["strength_sum"]))
        residual = float(sample["observed_y"]) - expected
        weight = max(0.05, float(sample["quality"]) * max(0.1, float(sample["coverage"])))
        for team_a, team_b in combinations(sorted(sample["teams"]), 2):
            pair = _canonical_pair(team_a, team_b)
            bucket[pair].append((residual, weight, float(sample["quality"]), float(sample["coverage"])))

    params_hash = stable_payload_hash(
        {
            "scope": "pair_event",
            "event_key": event_key,
            "model_version": model_version,
            "quality_threshold": quality_threshold,
            "shrink_k": shrink_k,
            "sample_count": len(samples),
        }
    )
    now = datetime.now(timezone.utc)
    db.query(models.TeamPairSynergyEvent).filter(
        models.TeamPairSynergyEvent.event_key == event_key,
        models.TeamPairSynergyEvent.model_version == model_version,
    ).delete(synchronize_session=False)
    for (team_key_a, team_key_b), values in bucket.items():
        weighted_residual = _weighted_mean([(value, weight) for value, weight, _, _ in values])
        n_matches = len(values)
        shrink = n_matches / (n_matches + max(1.0, shrink_k))
        avg_quality = _weighted_mean([(quality, weight) for _, weight, quality, _ in values])
        coverage_factor = _weighted_mean([(coverage, weight) for _, weight, _, coverage in values])
        synergy_points = weighted_residual * shrink
        confidence = _clamp(shrink * avg_quality * coverage_factor, 0.0, 1.0)
        db.add(
            models.TeamPairSynergyEvent(
                event_key=event_key,
                team_key_a=team_key_a,
                team_key_b=team_key_b,
                model_version=model_version,
                season=season,
                synergy_points_event=float(synergy_points),
                confidence_event=float(confidence),
                n_matches_together=int(n_matches),
                avg_quality=float(avg_quality),
                residual_mean_raw=float(weighted_residual),
                params_hash=params_hash,
                updated_at=now,
            )
        )
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "ok": True,
        "event_key": event_key,
        "season": season,
        "model_version": model_version,
        "params_hash": params_hash,
        "count": len(bucket),
        "alliance_sample_count": len(samples),
    }

def compute_match_synergy_projections(
    db: Session,
    event_key: str,
    *,
    model_version: str = SYNERGY_MODEL_VERSION,
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
    recompute_dependencies: bool = True,
    commit: bool = True,
) -> dict[str, Any]:
    season = _season_from_event_key(event_key)
    if season is None:
        raise RuntimeError(f"Unable to infer season from event key {event_key}")

    if recompute_dependencies:
        compute_team_season_strength(
            db,
            season,
            model_version=model_version,
            quality_threshold=quality_threshold,
            commit=False,
        )
        compute_team_pair_synergy_prior(
            db,
            season,
            model_version=model_version,
            quality_threshold=quality_threshold,
            commit=False,
        )
        compute_team_event_throughput_strength(
            db,
            event_key,
            model_version=model_version,
            quality_threshold=quality_threshold,
            commit=False,
        )
        compute_team_pair_synergy_event(
            db,
            event_key,
            model_version=model_version,
            quality_threshold=quality_threshold,
            commit=False,
        )

    season_strength_rows = _load_season_strength_map(db, season, model_version)
    strength_by_team = {
        team_key: float(row.strength_active_bps or 0.0)
        for team_key, row in season_strength_rows.items()
    }
    strength_conf_by_team = {
        team_key: float(row.confidence_0_1 or 0.0)
        for team_key, row in season_strength_rows.items()
    }
    prior_rows = (
        db.query(models.TeamPairSynergyPrior)
        .filter(
            models.TeamPairSynergyPrior.season == season,
            models.TeamPairSynergyPrior.model_version == model_version,
        )
        .all()
    )
    prior_by_pair = {
        (row.team_key_a, row.team_key_b): row
        for row in prior_rows
    }
    event_pair_rows = (
        db.query(models.TeamPairSynergyEvent)
        .filter(
            models.TeamPairSynergyEvent.event_key == event_key,
            models.TeamPairSynergyEvent.model_version == model_version,
        )
        .all()
    )
    event_by_pair = {
        (row.team_key_a, row.team_key_b): row
        for row in event_pair_rows
    }
    rating_rows = (
        db.query(models.EventTeamRating)
        .filter(models.EventTeamRating.event_key == event_key)
        .all()
    )
    rating_by_team = {row.team_key: row for row in rating_rows}
    intercept, slope, regression_sample_count = _season_expected_model(
        db,
        season,
        model_version,
        quality_threshold=quality_threshold,
    )

    matches = (
        db.query(models.Match)
        .filter(models.Match.event_key == event_key)
        .order_by(models.Match.comp_level.asc(), models.Match.set_number.asc(), models.Match.match_number.asc())
        .all()
    )
    match_team_rows = (
        db.query(models.MatchTeam)
        .filter(models.MatchTeam.event_key == event_key)
        .all()
    )
    teams_by_match_alliance: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in match_team_rows:
        if row.alliance in {"red", "blue"}:
            teams_by_match_alliance[(row.match_key, row.alliance)].append(row.team_key)

    params_hash = stable_payload_hash(
        {
            "scope": "match_synergy_projection",
            "event_key": event_key,
            "model_version": model_version,
            "quality_threshold": quality_threshold,
            "regression_sample_count": regression_sample_count,
            "role_adjustment_version": ROLE_ADJUSTMENT_VERSION,
            "pair_event_blend_k": PAIR_EVENT_BLEND_K,
        }
    )
    now = datetime.now(timezone.utc)

    # ── Phase 4: ML pair synergy blend ──────────────────────────────────
    ml_synergy_blend_knob = max(0.0, min(1.0, float(getattr(settings, "ml_synergy_blend", 0.0) or 0.0)))
    ml_synergy_prediction_by_pair: dict[tuple[str, str], float] = {}
    ml_synergy_payload: dict[str, Any] = {"prediction_ok": False}
    if ml_synergy_blend_knob > 0.0:
        try:
            from app.services.ml.shadow import infer_synergy_pair_shadow_from_rows
            from app.services.ml.synergy_ml import build_synergy_pair_feature_vector

            all_team_keys_set: set[str] = set()
            for key_tuple in teams_by_match_alliance.values():
                all_team_keys_set.update(key_tuple)
            unique_pairs: set[tuple[str, str]] = set()
            for team_keys_list in teams_by_match_alliance.values():
                for ta, tb in combinations(sorted(team_keys_list), 2):
                    unique_pairs.add(_canonical_pair(ta, tb))

            ml_input_rows: list[dict[str, Any]] = []
            for pair in sorted(unique_pairs):
                prior = prior_by_pair.get(pair)
                feature_vector = build_synergy_pair_feature_vector(
                    rating_by_team.get(pair[0]),
                    rating_by_team.get(pair[1]),
                    prior_synergy_points=float(prior.synergy_points_prior) if prior else 0.0,
                    prior_confidence=float(prior.confidence_prior) if prior else 0.0,
                    n_matches_together_prior=int(prior.n_matches_together) if prior else 0,
                    event_year=season,
                )
                ml_input_rows.append({
                    "team_key_a": pair[0],
                    "team_key_b": pair[1],
                    "event_key": event_key,
                    **feature_vector,
                })

            if ml_input_rows:
                ml_synergy_payload = infer_synergy_pair_shadow_from_rows(db, rows=ml_input_rows)
                if bool(ml_synergy_payload.get("ok")) and ml_synergy_payload.get("predictions"):
                    ml_synergy_payload["prediction_ok"] = True
                    for pred in ml_synergy_payload["predictions"]:
                        pa = str(pred.get("team_key_a") or "")
                        pb = str(pred.get("team_key_b") or "")
                        sp = pred.get("synergy_points_pred")
                        if pa and pb and sp is not None:
                            ml_synergy_prediction_by_pair[_canonical_pair(pa, pb)] = float(sp)
        except Exception:
            ml_synergy_payload = {"prediction_ok": False, "reason": "inference_error"}

    projection_rows: list[models.MatchSynergyProjection] = []
    ml_blended_pair_count = 0
    for match in matches:
        for alliance in ("red", "blue"):
            team_keys = sorted(teams_by_match_alliance.get((match.match_key, alliance), []))
            if not team_keys:
                continue
            team_strength_values = [float(strength_by_team.get(team_key, 0.0)) for team_key in team_keys]
            team_conf_values = [float(strength_conf_by_team.get(team_key, 0.0)) for team_key in team_keys]
            strength_sum = float(sum(team_strength_values))
            expected = (intercept + (slope * strength_sum)) if team_strength_values else None

            pair_breakdown: list[dict[str, Any]] = []
            pair_points: list[float] = []
            pair_confidences: list[float] = []
            source_labels: list[str] = []
            for team_a, team_b in combinations(team_keys, 2):
                pair = _canonical_pair(team_a, team_b)
                prior = prior_by_pair.get(pair)
                event_value = event_by_pair.get(pair)
                blended_value = 0.0
                blended_conf = 0.0
                source_label = "projection"

                if prior is not None and event_value is not None:
                    w_event = float(event_value.n_matches_together) / (
                        float(event_value.n_matches_together) + PAIR_EVENT_BLEND_K
                    )
                    blended_value = (
                        (w_event * float(event_value.synergy_points_event))
                        + ((1.0 - w_event) * float(prior.synergy_points_prior))
                    )
                    blended_conf = (
                        (w_event * float(event_value.confidence_event))
                        + ((1.0 - w_event) * float(prior.confidence_prior))
                    )
                    source_label = "measured" if w_event >= 0.67 else "blended"
                elif event_value is not None:
                    blended_value = float(event_value.synergy_points_event)
                    blended_conf = float(event_value.confidence_event)
                    source_label = "measured"
                elif prior is not None:
                    blended_value = float(prior.synergy_points_prior)
                    blended_conf = float(prior.confidence_prior)
                    source_label = "projection"

                role_adjustment = compute_pair_role_adjustment(
                    rating_by_team,
                    pair[0],
                    pair[1],
                )
                role_adjustment_points = float(role_adjustment.get("net_adjustment_points") or 0.0)
                adjusted_value = blended_value + role_adjustment_points
                profile_confidence = _clamp(
                    float(role_adjustment.get("profile_confidence_0_1") or 0.0),
                    0.0,
                    1.0,
                )
                adjusted_conf = _clamp(blended_conf * (0.78 + (0.22 * profile_confidence)), 0.0, 1.0)

                # Phase 4: ML pair synergy blend
                ml_synergy_pred = ml_synergy_prediction_by_pair.get(pair)
                synergy_points_deterministic = adjusted_value
                synergy_points_ml = None
                pair_blend_knob = 0.0
                if ml_synergy_pred is not None and ml_synergy_blend_knob > 0.0:
                    synergy_points_ml = float(ml_synergy_pred)
                    pair_blend_knob = ml_synergy_blend_knob
                    adjusted_value = (
                        (pair_blend_knob * synergy_points_ml)
                        + ((1.0 - pair_blend_knob) * synergy_points_deterministic)
                    )
                    source_label = f"ml_blend_{pair_blend_knob:.2f}" if pair_blend_knob < 1.0 else "ml"
                    ml_blended_pair_count += 1

                pair_points.append(adjusted_value)
                pair_confidences.append(adjusted_conf)
                source_labels.append(source_label)
                pair_breakdown.append(
                    {
                        "team_key_a": pair[0],
                        "team_key_b": pair[1],
                        "base_synergy_points": round(blended_value, 4),
                        "role_adjustment_points": round(role_adjustment_points, 4),
                        "complement_bonus_points": round(float(role_adjustment.get("complement_bonus_points") or 0.0), 4),
                        "risk_penalty_points": round(float(role_adjustment.get("risk_penalty_points") or 0.0), 4),
                        "synergy_points": round(adjusted_value, 4),
                        "synergy_points_deterministic": round(synergy_points_deterministic, 4),
                        "synergy_points_ml": round(synergy_points_ml, 4) if synergy_points_ml is not None else None,
                        "synergy_blend": round(pair_blend_knob, 4),
                        "confidence": round(adjusted_conf, 4),
                        "role_profile_confidence_0_1": round(profile_confidence, 4),
                        "role_profile_coverage_0_1": round(
                            _clamp(float(role_adjustment.get("profile_coverage_0_1") or 0.0), 0.0, 1.0),
                            4,
                        ),
                        "role_axes": role_adjustment.get("role_axes") or {},
                        "matchup_signals": _build_matchup_signals(
                            role_adjustment=role_adjustment,
                            prior_n=int(prior.n_matches_together) if prior else 0,
                            event_n=int(event_value.n_matches_together) if event_value else 0,
                        ),
                        "source": source_label,
                        "prior_n": prior.n_matches_together if prior else 0,
                        "event_n": event_value.n_matches_together if event_value else 0,
                    }
                )

            alliance_synergy_points = float(sum(pair_points))
            projected = (float(expected) + alliance_synergy_points) if expected is not None else None
            team_conf = _mean(team_conf_values)
            pair_conf = _mean(pair_confidences)
            coverage_factor = _clamp(len(team_keys) / 3.0, 0.0, 1.0)
            confidence_0_1 = _clamp(
                pair_conf * (0.55 + (0.45 * coverage_factor)) * (0.55 + (0.45 * team_conf)),
                0.0,
                1.0,
            )

            has_ml = any(str(label).startswith("ml") for label in source_labels)
            if has_ml:
                overall_source = "ml" if all(str(label).startswith("ml") for label in source_labels) else "ml_blended"
            elif "measured" in source_labels:
                overall_source = "measured" if source_labels.count("measured") == len(source_labels) else "blended"
            elif "blended" in source_labels:
                overall_source = "blended"
            else:
                overall_source = "projection"

            row = models.MatchSynergyProjection(
                event_key=event_key,
                match_key=match.match_key,
                alliance_color=alliance,
                model_version=model_version,
                season=season,
                scheduled_time=match.time,
                expected_throughput=float(expected) if expected is not None else None,
                alliance_synergy_points=float(alliance_synergy_points),
                projected_throughput=float(projected) if projected is not None else None,
                alliance_synergy_score_0_100=50.0,
                confidence_0_1=float(confidence_0_1),
                source_label=overall_source,
                pair_breakdown=pair_breakdown,
                params_hash=params_hash,
                computed_at=now,
            )
            db.merge(row)
            projection_rows.append(row)
    if commit:
        db.commit()
    else:
        db.flush()

    persisted = (
        db.query(models.MatchSynergyProjection)
        .filter(
            models.MatchSynergyProjection.event_key == event_key,
            models.MatchSynergyProjection.model_version == model_version,
        )
        .all()
    )
    key_to_value = {
        f"{row.match_key}:{row.alliance_color}": (
            float(row.projected_throughput)
            if row.projected_throughput is not None
            else None
        )
        for row in persisted
    }
    percentile = _percentile_scores(key_to_value)
    for row in persisted:
        row.alliance_synergy_score_0_100 = float(percentile[f"{row.match_key}:{row.alliance_color}"])
        db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()

    return {
        "ok": True,
        "event_key": event_key,
        "season": season,
        "model_version": model_version,
        "params_hash": params_hash,
        "count": len(persisted),
        "regression": {
            "intercept": round(intercept, 6),
            "slope": round(slope, 6),
            "sample_count": regression_sample_count,
        },
        "ml_synergy_blend": {
            "blend_knob": round(ml_synergy_blend_knob, 4),
            "prediction_ok": bool(ml_synergy_payload.get("prediction_ok")),
            "ml_blended_pair_count": ml_blended_pair_count,
            "model_version": ml_synergy_payload.get("model_version"),
        },
    }

def precompute_event_synergy(
    db: Session,
    event_key: str,
    *,
    model_version: str = SYNERGY_MODEL_VERSION,
    quality_threshold: float = QUALITY_THRESHOLD_DEFAULT,
) -> dict[str, Any]:
    season = _season_from_event_key(event_key)
    if season is None:
        raise RuntimeError(f"Unable to infer season from event key {event_key}")

    try:
        season_strength = compute_team_season_strength(
            db,
            season,
            model_version=model_version,
            quality_threshold=quality_threshold,
            commit=False,
        )
        prior = compute_team_pair_synergy_prior(
            db,
            season,
            model_version=model_version,
            quality_threshold=quality_threshold,
            commit=False,
        )
        event_strength = compute_team_event_throughput_strength(
            db,
            event_key,
            model_version=model_version,
            quality_threshold=quality_threshold,
            commit=False,
        )
        event_pair = compute_team_pair_synergy_event(
            db,
            event_key,
            model_version=model_version,
            quality_threshold=quality_threshold,
            commit=False,
        )
        projections = compute_match_synergy_projections(
            db,
            event_key,
            model_version=model_version,
            quality_threshold=quality_threshold,
            recompute_dependencies=False,
            commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "ok": True,
        "event_key": event_key,
        "season": season,
        "model_version": model_version,
        "season_strength": season_strength,
        "pair_prior": prior,
        "event_strength": event_strength,
        "event_pair": event_pair,
        "projections": projections,
    }
