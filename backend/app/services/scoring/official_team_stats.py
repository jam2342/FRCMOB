# Per-robot numbers for a team from official results alone.
#
# Almost no team is ever scouted or filmed by us, so this is what Team Center
# shows for most robots. Two sources, both official:
# - TBA component OPRs (COPRs) for fuel and auto: a least-squares split of the
#   alliance breakdowns onto teams. The best per-robot estimate alliance-only
#   data allows, and the one TBA publishes, but still an estimate.
# - The per-robot tower result in every score breakdown, which is a direct
#   observation, not an estimate.
# Cycle time, defense and reliability cannot be recovered from either, so they
# are left for scouting and video rather than invented.
from __future__ import annotations

import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.services.cache import get_cache
from app.services.scoring.truth import _rebuilt_active_hub_duration_sec, _truth_context
from app.services.scouting_rooms.helpers import extract_climb_status_token, normalize_climb_level_token

logger = logging.getLogger(__name__)

TBA_FINDING_SOURCES = ("tba_score_breakdown", "tba_score_breakdown_backfill")
# Regional, district, district championship (and its divisions), championship
# division and finals. Preseason (100) and offseason (99) events mix B-teams
# and changed robots, so they don't describe a team's season.
OFFICIAL_EVENT_TYPES = frozenset({0, 1, 2, 3, 4, 5})

# TBA names its component OPRs after the season's breakdown fields; a new game
# adds its own entry here.
COPR_METRICS_BY_SEASON: dict[int, dict[str, str]] = {
    2026: {
        "fuel_per_match": "Hub Total Fuel Count",
        "auto_fuel_per_match": "Hub Auto Fuel Count",
        "teleop_fuel_per_match": "Hub Teleop Fuel Count",
        "auto_points_per_match": "totalAutoPoints",
    },
}

_COPR_CACHE_PREFIX = "official:coprs:v1:"
# A finished event's COPRs never change again.
_COPR_TTL_FINISHED_SEC = 7 * 24 * 3600
_COPR_TTL_RUNNING_SEC = 15 * 60
_LEVEL_LABELS = {"level1": "Level 1", "level2": "Level 2", "level3": "Level 3"}
_LEVEL_SCORES = {"level1": 38.0, "level2": 68.0, "level3": 100.0}


@dataclass
class _EventRecord:
    matches: set[str] = field(default_factory=set)
    climbs: int = 0
    levels: Counter = field(default_factory=Counter)


def _official_rows(db: Session, team_key: str, season_year: int) -> list[models.TeamMatchFinding]:
    rows = db.execute(
        select(models.TeamMatchFinding)
        .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
        .where(
            models.TeamMatchFinding.team_key == team_key,
            models.TeamMatchFinding.source.in_(TBA_FINDING_SOURCES),
            models.Event.year == season_year,
        )
        .order_by(models.TeamMatchFinding.id.desc())
    ).scalars()
    # Ingest and the climb backfill can both hold a row for one match; one match
    # counts once, newest row first.
    by_match: dict[str, models.TeamMatchFinding] = {}
    for row in rows:
        by_match.setdefault(row.match_key, row)
    return list(by_match.values())


def _official_events(tba: Any, team_key: str, season_year: int) -> dict[str, dict[str, Any]] | None:
    if tba is None:
        return None
    try:
        payload = tba.team_events(team_key, season_year)
    except Exception:
        logger.warning("official_stats.team_events_failed team_key=%s year=%s", team_key, season_year, exc_info=True)
        return None
    events: dict[str, dict[str, Any]] = {}
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict) or item.get("event_type") not in OFFICIAL_EVENT_TYPES:
            continue
        key = str(item.get("key") or "").strip().lower()
        if key:
            events[key] = item
    return events


def _event_finished(event: dict[str, Any] | None) -> bool:
    try:
        end = date.fromisoformat(str((event or {}).get("end_date") or ""))
    except ValueError:
        return False
    return end < datetime.now(timezone.utc).date() - timedelta(days=1)


def _event_coprs(
    tba: Any,
    event_key: str,
    metric_names: dict[str, str],
    *,
    finished: bool,
) -> dict[str, dict[str, float]] | None:
    cache = get_cache()
    cache_key = f"{_COPR_CACHE_PREFIX}{event_key}"
    cached = cache.get(cache_key)
    if cached:
        try:
            return json.loads(cached)
        except ValueError:
            pass
    if tba is None:
        return None
    try:
        payload = tba.event_coprs(event_key)
    except Exception:
        logger.warning("official_stats.coprs_failed event_key=%s", event_key, exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None
    # Keep only what Team Center reads; a full COPR payload is ~27 metrics per team.
    trimmed = {
        tba_name: {str(team): float(value) for team, value in (payload.get(tba_name) or {}).items() if isinstance(value, (int, float))}
        for tba_name in metric_names.values()
    }
    cache.set(cache_key, json.dumps(trimmed), ttl=_COPR_TTL_FINISHED_SEC if finished else _COPR_TTL_RUNNING_SEC)
    return trimmed


def _level_capability(levels: Counter, matches: int, climbs: int) -> dict[str, Any]:
    best = next((level for level in ("level3", "level2", "level1") if levels.get(level)), None)
    with_level = sum(int(levels.get(level, 0)) for level in _LEVEL_LABELS)
    return {
        "best_level": best,
        "best_level_label": _LEVEL_LABELS[best] if best else ("Success (Level Unknown)" if climbs else "No Climbs"),
        "best_level_score_0_100": _LEVEL_SCORES[best] if best else None,
        "level_counts": {level: int(levels.get(level, 0)) for level in _LEVEL_LABELS},
        "matches_with_level": with_level,
        "matches_with_success_no_level": max(0, climbs - with_level),
        "matches_considered": matches,
    }


def official_team_stats(db: Session, *, team_key: str, season_year: int, tba: Any = None) -> dict[str, Any]:
    rows = _official_rows(db, team_key, season_year)
    official_events = _official_events(tba, team_key, season_year)

    records: dict[str, _EventRecord] = {}
    for row in rows:
        if official_events is not None and row.event_key not in official_events:
            continue
        record = records.setdefault(row.event_key, _EventRecord())
        record.matches.add(row.match_key)
        if row.climb_success_prob is not None and float(row.climb_success_prob) >= 0.5:
            record.climbs += 1
        summary = row.summary if isinstance(row.summary, dict) else {}
        level = normalize_climb_level_token(extract_climb_status_token(summary))
        if level in _LEVEL_LABELS:
            record.levels[level] += 1

    matches = sum(len(record.matches) for record in records.values())
    climbs = sum(record.climbs for record in records.values())
    levels: Counter = Counter()
    for record in records.values():
        levels.update(record.levels)

    metric_names = COPR_METRICS_BY_SEASON.get(int(season_year), {})
    event_keys = sorted(records)
    coprs_by_event: dict[str, dict[str, dict[str, float]] | None] = {}
    if metric_names and event_keys:
        # A season can span nine events; one TBA round trip each, in parallel.
        with ThreadPoolExecutor(max_workers=min(6, len(event_keys))) as pool:
            fetched = pool.map(
                lambda key: _event_coprs(
                    tba,
                    key,
                    metric_names,
                    finished=_event_finished((official_events or {}).get(key)),
                ),
                event_keys,
            )
            coprs_by_event = dict(zip(event_keys, fetched))

    weighted: dict[str, float] = {metric: 0.0 for metric in metric_names}
    copr_matches = 0
    events_payload: list[dict[str, Any]] = []
    for event_key in event_keys:
        appearances = len(records[event_key].matches)
        coprs = coprs_by_event.get(event_key)
        values = {
            metric: (coprs or {}).get(tba_name, {}).get(team_key)
            for metric, tba_name in metric_names.items()
        }
        has_copr = all(isinstance(value, (int, float)) for value in values.values()) and bool(values)
        if has_copr:
            copr_matches += appearances
            for metric, value in values.items():
                # COPR is an unconstrained regression; a weak robot can come out
                # slightly negative, which is noise, not negative scoring.
                weighted[metric] += max(0.0, float(value)) * appearances
        events_payload.append({"event_key": event_key, "matches": appearances, "copr": has_copr})

    estimates: dict[str, float | None] = {
        metric: (round(total / copr_matches, 1) if copr_matches else None) for metric, total in weighted.items()
    }
    teleop_fuel = estimates.get("teleop_fuel_per_match")
    # The same active-hub window the official ingest divides by, so this rate and
    # every official finding's fuel_scoring_rate are one unit.
    active_hub_sec = float(_rebuilt_active_hub_duration_sec(_truth_context()["phases"]))
    return {
        "available": matches > 0,
        "season_year": int(season_year),
        "matches": matches,
        "events": events_payload,
        "events_verified_official": official_events is not None,
        "copr_matches": copr_matches,
        **estimates,
        # Teleop fuel per minute of its own hub being active.
        "fuel_per_active_minute": (
            round(float(teleop_fuel) * 60.0 / active_hub_sec, 1) if isinstance(teleop_fuel, (int, float)) else None
        ),
        "climb": {
            "matches": matches,
            "climbs": climbs,
            "rate": round(climbs / matches, 3) if matches else None,
            "level_capability": _level_capability(levels, matches, climbs),
        },
    }
