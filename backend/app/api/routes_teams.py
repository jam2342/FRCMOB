# Team routes — hub module.
#
# Helper functions live here so that existing unittest.mock.patch targets
# keep working.
# Endpoint handlers have been moved to focused sub-modules:
#
# * ``app.api.teams.intel``  — search, team intel, event teams intel
# * ``app.api.teams.media``  — robot image, team logo
#
# No business logic was changed during the split.

from datetime import datetime, timezone
import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.security import sanitize_external_error
from app.db import models
from app.services.auto_scout.scouting import summarize_team_auto_scout_profile
from app.services.cache import get_async_cache
from app.services.intel.helpers import _normalize_team_key
from app.services.ratings.model import recompute_event_ratings
from app.services.ratings.output_builder import rating_points, rating_subscores
from app.services.scoring.official_team_stats import official_team_stats
from app.services.scouting_rooms.scope import (
    active_game_season_year as _active_game_season_year,
    build_data_freshness_payload as _build_data_freshness_payload,
)
from app.services.team_utils import (
    region_label as _region_label,
    team_number_from_team_key as _team_number_from_team_key,
)
from app.services.utils import _as_float
from app.services.intel.builders import (
    register_event_teams_intel_builder,
    register_team_intel_builder,
)
from app.tba.client import TBAClient

router = APIRouter(prefix="/teams", tags=["teams"])
logger = logging.getLogger(__name__)

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


def _now_unix() -> int:
    return int(time.time())


def _with_own_session(db: Session, fn, *args, **kwargs):
    # For the async intel routes: a database step runs in a worker thread on a short
    # session of its own, closed before the route awaits anything else. The request's
    # session must never hold a pooled connection across an await: the route would
    # then wait for a free thread or connection while holding one, and under load every
    # connection ended up held by routes waiting on threads that waited on connections.
    # Nothing expires on commit, so returned rows stay readable once it closes.
    if not hasattr(db, "get_bind"):
        return fn(db, *args, **kwargs)
    own = Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    try:
        return fn(own, *args, **kwargs)
    finally:
        own.close()

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

_OFFICIAL_ESTIMATE_FIELDS = {
    "fuel_scoring_rate": "fuel_per_active_minute",
    "auto_contribution": "auto_points_per_match",
}


def _enrich_analysis_with_official_stats(
    analysis_payload: dict[str, Any],
    official_stats: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    merged = dict(analysis_payload)
    merged["official_stats"] = official_stats if isinstance(official_stats, dict) else {"available": False}
    averages = dict(merged.get("averages")) if isinstance(merged.get("averages"), dict) else {}
    coverage = dict(merged.get("metric_coverage")) if isinstance(merged.get("metric_coverage"), dict) else {}
    applied: list[str] = []
    if not isinstance(official_stats, dict) or not official_stats.get("available"):
        merged["estimated_averages"] = {"applied": False, "source": "official_tba", "fields": []}
        return merged, applied

    event_count = sum(1 for event in official_stats.get("events") or [] if event.get("copr"))
    for metric_key, stats_key in _OFFICIAL_ESTIMATE_FIELDS.items():
        value = _as_float(official_stats.get(stats_key))
        if value is None or _as_float(averages.get(metric_key)) is not None:
            continue
        averages[metric_key] = value
        entry = dict(coverage.get(metric_key)) if isinstance(coverage.get(metric_key), dict) else {}
        entry["estimated_from_model"] = True
        entry["estimate_source"] = "tba_copr"
        entry["missing_reason"] = (
            f"TBA's per-team estimate (component OPR) over {official_stats.get('copr_matches')} official "
            f"matches at {event_count} event{'s' if event_count != 1 else ''}."
        )
        coverage[metric_key] = entry
        applied.append(metric_key)

    climb = official_stats.get("climb") if isinstance(official_stats.get("climb"), dict) else {}
    climb_rate = _as_float(climb.get("rate"))
    if climb_rate is not None and _as_float(averages.get("climb_success_prob")) is None:
        # The tower result is recorded per robot in every official breakdown, so
        # this is the team's actual record, not an estimate.
        averages["climb_success_prob"] = climb_rate
        entry = dict(coverage.get("climb_success_prob")) if isinstance(coverage.get("climb_success_prob"), dict) else {}
        entry["official_record"] = True
        entry["official_matches"] = int(climb.get("matches") or 0)
        entry["official_climbs"] = int(climb.get("climbs") or 0)
        coverage["climb_success_prob"] = entry
        climb_sources = dict(merged.get("climb_sources")) if isinstance(merged.get("climb_sources"), dict) else {}
        level_capability = climb_sources.get("level_capability") if isinstance(climb_sources.get("level_capability"), dict) else {}
        if not int(level_capability.get("matches_considered") or 0) and isinstance(climb.get("level_capability"), dict):
            climb_sources["level_capability"] = {**climb["level_capability"], "source": "official_score_breakdown"}
            merged["climb_sources"] = climb_sources

    merged["averages"] = averages
    merged["metric_coverage"] = coverage
    merged["estimated_averages"] = {"applied": bool(applied), "source": "official_tba", "fields": applied}
    return merged, applied

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
        "averages": payload.get("averages"),
        "climb_sources": payload.get("climb_sources"),
        "estimated_averages": payload.get("estimated_averages"),
        "official_stats": payload.get("official_stats"),
        "metric_coverage": payload.get("metric_coverage"),
        "event_type_counts": payload.get("event_type_counts") or [],
        "recent_matches": payload.get("recent_matches") or [],
    }

def _round_or_none(value: object) -> float | None:
    parsed = _as_float(value)
    return round(parsed, 4) if parsed is not None else None

def _rating_preview(
    row: models.EventTeamRating,
    *,
    source: str,
    context_event_key: str | None,
    include_details: bool = True,
    include_signals: bool = True,
) -> dict[str, Any]:
    details = row.details_json if isinstance(row.details_json, dict) else {}

    return {
        "available": True,
        "source": source,
        "context_event_key": context_event_key,
        "rating_0_100": _round_or_none(row.rating_0_100),
        "confidence_0_1": float(row.confidence_0_1 or 0.0),
        "robot_level_0_100": _round_or_none(row.robot_level_0_100),
        "driver_skill_0_100": _round_or_none(row.driver_skill_0_100),
        "subscores": rating_subscores(row),
        **rating_points(row),
        "pros": (row.pros_json if isinstance(row.pros_json, list) else []) if include_signals else [],
        "cons": (row.cons_json if isinstance(row.cons_json, list) else []) if include_signals else [],
        "details": details if include_details else {},
        "model_version": row.model_version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }

def _latest_team_rating_row(
    db: Session,
    team_key: str,
    *,
    season: int | None = None,
    before_season: int | None = None,
) -> tuple[models.EventTeamRating | None, str | None]:
    # "Latest" used to mean the newest rating row, which for every Championship team
    # was Einstein: TBA lists all ~600 Championship teams there, a few play 16 matches,
    # and 254 read 39.0, 596th of 605. Prefer the newest season, then the event where
    # the team actually played the most matches.
    played = (
        db.query(models.MatchTeam.event_key, func.count(models.MatchTeam.match_key).label("played"))
        .filter(models.MatchTeam.team_key == team_key)
        .group_by(models.MatchTeam.event_key)
        .subquery()
    )
    query = (
        db.query(models.EventTeamRating, models.Event)
        .join(models.Event, models.Event.event_key == models.EventTeamRating.event_key)
        .outerjoin(played, played.c.event_key == models.EventTeamRating.event_key)
        .filter(models.EventTeamRating.team_key == team_key)
    )
    # Ratings from different seasons come from different games: a fallback stays in one
    # season, and an earlier season is only ever shown as "last season".
    if season is not None:
        query = query.filter(models.Event.year == season)
    if before_season is not None:
        query = query.filter(models.Event.year < before_season)
    row = (
        query.order_by(
            models.Event.year.desc(),
            func.coalesce(played.c.played, 0).desc(),
            models.EventTeamRating.updated_at.desc(),
        )
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

def _rated_field_filter(db: Session, event_key: str):
    # TBA's Championship finals roster lists every Championship team, most of whom
    # never play there; "4th of 605" means nothing. Rank against teams that played
    # at the event whenever it has match rows at all.
    has_matches = (
        db.query(models.MatchTeam.match_key).filter(models.MatchTeam.event_key == event_key).first()
        is not None
    )
    if not has_matches:
        return None
    return models.EventTeamRating.team_key.in_(
        db.query(models.MatchTeam.team_key).filter(models.MatchTeam.event_key == event_key).distinct()
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
    field_query = db.query(func.count(models.EventTeamRating.team_key)).filter(
        models.EventTeamRating.event_key == event_key
    )
    field_filter = _rated_field_filter(db, event_key)
    if field_filter is not None:
        field_query = field_query.filter(field_filter)
    field_size = field_query.scalar()
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
    field_filter = _rated_field_filter(db, event_key)
    higher = db.query(func.count(models.EventTeamRating.team_key)).filter(
        models.EventTeamRating.event_key == event_key,
        models.EventTeamRating.rating_0_100 > row.rating_0_100,
    )
    tied_before = db.query(func.count(models.EventTeamRating.team_key)).filter(
        models.EventTeamRating.event_key == event_key,
        models.EventTeamRating.rating_0_100 == row.rating_0_100,
        models.EventTeamRating.team_key < team_key,
    )
    if field_filter is not None:
        higher = higher.filter(field_filter)
        tied_before = tied_before.filter(field_filter)
    return int(higher.scalar() or 0) + int(tied_before.scalar() or 0) + 1

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

def _official_analysis_base(team_key: str, event_key: str | None) -> dict[str, Any]:
    # Team Center's numbers come from official data alone (_enrich_analysis_with_official_stats
    # fills them in): no scouting or video feed reaches a team's public page.
    season_year = _event_year_from_key(event_key) or _active_game_season_year()
    return {
        "team": {
            "team_key": _normalize_team_key(team_key),
            "team_number": _team_number_from_team_key(team_key),
            "nickname": None,
        },
        "event_key": event_key,
        "season_scope": {
            "mode": "event" if event_key else "season",
            "season_year": season_year,
            "active_season_year": _active_game_season_year(),
            "source": "event_key" if isinstance(_event_year_from_key(event_key), int) else "active_game_config",
            "event_key": event_key,
        },
        "averages": None,
        "metric_coverage": {},
        "event_type_counts": [],
        "recent_matches": [],
    }

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

    from app.services.seasons import SEASON_GAMES, current_season, season_of

    season = season_of(event_key) if event_key else current_season()
    latest_rating, latest_event_key = _latest_team_rating_row(db, team_key, season=season)
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
    unavailable: dict[str, Any] = {"available": False, "source": "none", "context_event_key": event_key, "season": season}
    previous, previous_event_key = _latest_team_rating_row(db, team_key, before_season=season)
    if previous is not None and previous_event_key:
        previous_season = season_of(previous_event_key)
        rank = _event_rating_rank_context(db, event_key=previous_event_key, team_key=team_key) or {}
        event_row = db.get(models.Event, previous_event_key)
        unavailable["previous_season"] = {
            "season": previous_season,
            "game": SEASON_GAMES.get(previous_season or 0),
            "event_key": previous_event_key,
            "event_name": event_row.name if event_row is not None else None,
            "rating_0_100": round(float(previous.rating_0_100), 2),
            "rank": rank.get("rank"),
            "field_size": rank.get("field_size"),
        }
    return unavailable, fallbacks, warnings

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
    allow_season_fallback: bool,
    auto_heal_ratings: bool,
) -> dict[str, Any]:
    normalized_team_key = _normalize_team_key(team_key)
    normalized_event_key = event_key.strip().lower() if isinstance(event_key, str) and event_key.strip() else None

    team = await run_in_threadpool(_with_own_session, db, lambda own: own.get(models.Team, normalized_team_key))
    resolved_team_key = team.team_key if team is not None else normalized_team_key
    resolved_team_number = int(team.team_number) if team is not None and team.team_number is not None else _team_number_from_team_key(normalized_team_key)
    resolved_nickname = team.nickname if team is not None else None
    local_team_missing_warning: str | None = None
    if team is None:
        # Keep intel endpoint resilient for teams that are valid on TBA but
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
        return _official_analysis_base(normalized_team_key, normalized_event_key), [], []

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

    def _sync_official_stats():
        thread_db = _thread_db()
        try:
            return official_team_stats(
                thread_db,
                team_key=normalized_team_key,
                season_year=_event_year_from_key(normalized_event_key) or preferred_year,
                tba=tba_client,
            )
        except Exception:
            logger.exception("team_intel.official_stats_failed team_key=%s", normalized_team_key)
            return {"available": False}
        finally:
            if thread_db is not db and hasattr(thread_db, "close"):
                thread_db.close()

    # Run sync DB + TBA work in the thread pool, all concurrently
    (
        (selected_year, registered_events, registered_source),
        (analysis_payload, analysis_fallbacks, analysis_warnings),
        (rating_payload, rating_fallbacks, rating_warnings),
        auto_scout_profile,
        tba_context,
        official_stats,
    ) = await asyncio.gather(
        loop.run_in_executor(None, _sync_registered_events),
        loop.run_in_executor(None, _sync_analysis),
        loop.run_in_executor(None, _sync_rating),
        loop.run_in_executor(None, _sync_auto_scout_profile),
        loop.run_in_executor(None, _sync_tba_context),
        loop.run_in_executor(None, _sync_official_stats),
    )

    # ── Sequential post-processing (depends on parallel results) ──
    analysis_payload, estimated_average_fields = _enrich_analysis_with_official_stats(
        analysis_payload,
        official_stats,
    )
    if estimated_average_fields:
        analysis_fallbacks.append(
            {
                "kind": "analysis_metric_estimates",
                "from": "missing_analysis_metrics",
                "to": "official_tba",
                "fields": estimated_average_fields,
                "reason": "no_scouting_or_video",
            }
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
            rank_context = await run_in_threadpool(
                _with_own_session,
                db,
                _event_rating_rank_context,
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
            fallback_rank = await run_in_threadpool(
                _with_own_session,
                db,
                _event_rating_rank,
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
    warnings = analysis_warnings + rating_warnings
    if local_team_missing_warning:
        warnings.append(local_team_missing_warning)
    warnings.extend([str(item) for item in tba_context.get("warnings", []) if isinstance(item, str)])
    warnings = _dedupe_warnings(warnings)
    analysis_snapshot = _analysis_snapshot_from_breakdown(analysis_payload)

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
        "fallbacks": analysis_fallbacks + rating_fallbacks,
        "warnings": warnings,
        "source_versions": {
            "scouting": "scouting.team_breakdown_v1",
            "tba": "tba.v3",
        },
    }

async def _build_event_teams_intel_payload(
    *,
    db: Session,
    event_key: str,
    include_tba: bool,
    auto_heal_ratings: bool,
    include_season_fallback: bool,
    include_rating_details: bool,
    include_rating_signals: bool,
) -> dict[str, Any]:
    # Everything here is blocking (SQLAlchemy, the TBA roster/status calls, an optional
    # ratings recompute). This coroutine runs on the event loop that also serves every
    # other request and the room websockets, so it runs on a worker thread instead.
    def _prepare_sync(db: Session) -> tuple[Any, ...]:
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

        return (
            normalized_event_key,
            event,
            source,
            refresh_error,
            team_rows,
            rating_by_team,
            event_rating_rank_by_team,
            latest_rating_by_team,
            latest_rating_rank_by_team,
            findings_event_by_team,
            findings_season_by_team,
            season_year,
            tba_statuses,
            tba_warning,
        )

    (
        normalized_event_key,
        event,
        source,
        refresh_error,
        team_rows,
        rating_by_team,
        event_rating_rank_by_team,
        latest_rating_by_team,
        latest_rating_rank_by_team,
        findings_event_by_team,
        findings_season_by_team,
        season_year,
        tba_statuses,
        tba_warning,
    ) = await asyncio.get_running_loop().run_in_executor(None, _with_own_session, db, _prepare_sync)
    teams_payload: list[dict[str, Any]] = []
    fallback_count = 0
    stale_data_count = 0
    tba_rank_fallback_count = 0
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
        if rating_row is not None:
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
                "rating": rating_payload,
                "tba": {
                    "available": include_tba and settings.tba_auth_key.strip() != "",
                    "event_status_summary": tba_status_summary,
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
        "teams_with_tba_rank_fallback": int(tba_rank_fallback_count),
        "teams": teams_payload,
        "warnings": warnings,
    }

# ── Include sub-routers ──────────────────────────────────────────────────
# The sub-modules use ``import app.api.routes_teams as _rt`` to call helpers
# defined above.  Python resolves the circular reference because all helpers
# are defined before the sub-module imports below execute.

from app.api.teams.intel import router as _intel_router  # noqa: E402
from app.api.teams.media import router as _media_router  # noqa: E402
from app.api.teams.history import router as _history_router  # noqa: E402
from app.api.teams.head_to_head import router as _head_to_head_router  # noqa: E402
from app.api.teams.season_story import router as _season_story_router  # noqa: E402

router.include_router(_intel_router)
router.include_router(_media_router)
router.include_router(_history_router)
router.include_router(_head_to_head_router)
router.include_router(_season_story_router)

# ── Register intel builders with the service layer ───────────────────────
# This allows services (like intel_snapshots) to call these builders
# without importing from the API layer directly.
register_event_teams_intel_builder(_build_event_teams_intel_payload)
register_team_intel_builder(_build_team_intel_payload)
