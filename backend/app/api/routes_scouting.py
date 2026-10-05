from collections import defaultdict
from contextlib import suppress
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin_access, require_write_access, sanitize_external_error
from app.db import models
from app.db.session import get_db
from app.api.schemas import RatingsResponse, TeamsHistoryResponse
from app.services.analysis.runs import RUN_KIND_VIDEO
from app.services.scouting_rooms.helpers import (
    RECENT_STATS_MATCH_WINDOW,
    apply_quality_gate_to_finding_rows as _apply_quality_gate_to_finding_rows,
    compute_averages as _compute_averages,
    compute_climb_sources as _compute_climb_sources,
    dedupe_finding_rows_by_match as _dedupe_finding_rows_by_match,
    extract_perimeter_context as _extract_perimeter_context,
    infer_climb_from_match_events as _infer_climb_from_match_events,
    mean_metric as _mean_metric,
    metric_coverage_payload as _metric_coverage_payload,
    rating_float as _rating_float,
    rows_for_metric_coverage as _rows_for_metric_coverage,
    safe_div as _safe_div,
    safe_int as _safe_int,
    serialize_finding as _serialize_finding,
    serialize_rating_row as _serialize_rating_row,
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
        scored_points = _rating_float(meta.get("official_points"))
        if scored_points is None:
            scored_points = _rating_float(meta.get("estimated_points"))
        if scored_points is None:
            if row.event_type == "climb_success":
                scored_points = 1.0
            else:
                continue
        if scored_points <= 0.0:
            continue
        points_by_team_match[(row.match_key, team_key)] += float(scored_points)

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


@router.get("/event/{event_key}/team/{team_key}/rating")
def get_event_team_rating(
    event_key: str,
    team_key: str,
    request: Request,
    recompute_if_missing: bool = False,
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
            # A whole-event recompute: admin only, like ?refresh on the event ratings.
            require_admin_access(request, "Ratings recompute")
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
        .filter(models.AnalysisRun.run_kind == RUN_KIND_VIDEO)
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
    fetch_limit = max(capped_history, min(capped_history * 3, 180))
    # One query for the whole roster: this ran one per team (40-75 per call, ~3 s
    # on the Events page).
    rows_by_team: dict[str, list[tuple[models.TeamMatchFinding, int | None]]] = defaultdict(list)
    if team_keys:
        history_query = (
            db.query(models.TeamMatchFinding, models.Match.time)
            .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
            .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
            .filter(models.TeamMatchFinding.team_key.in_(team_keys))
            .filter(models.Event.year == event_season_year)
        )
        if exclude_current_event:
            history_query = history_query.filter(models.TeamMatchFinding.event_key != normalized_event_key)
        for finding, match_time in history_query.order_by(
            models.TeamMatchFinding.team_key.asc(),
            models.Match.time.desc().nullslast(),
            models.TeamMatchFinding.id.desc(),
        ):
            bucket = rows_by_team[finding.team_key]
            if len(bucket) < fetch_limit:
                bucket.append((finding, match_time))
    teams_payload = []

    for event_team, team, team_profile in team_rows:
        state_prov = team_profile.state_prov if team_profile else None
        country = team_profile.country if team_profile else None
        raw_history_rows = rows_by_team.get(event_team.team_key, [])
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
