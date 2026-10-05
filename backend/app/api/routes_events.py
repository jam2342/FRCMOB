from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import math
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin_access, require_write_access, sanitize_external_error
from app.db import models
from app.db.session import get_db
from app.api.schemas import ScheduleWithSynergyResponse
from app.services.ml.shadow import (
    MATCH_OUTCOME_MODEL_KEY,
    infer_match_outcome_shadow_from_rows,
)
from app.services.cache import get_cache
from app.services.events.fuel_win_probability import fuel_margin_details, fuel_red_win_prob
from app.services.ml.synergy import SYNERGY_MODEL_VERSION, precompute_event_synergy
from app.services.season_config import CURRENT_SEASON_YEAR, PREVIOUS_SEASON_YEAR
from app.services.utils import COMP_LEVEL_ORDER, _as_float
from app.tba.client import TBAClient

router = APIRouter(prefix="/events", tags=["events"])
logger = logging.getLogger(__name__)
# Speed-first defaults for local/private operation.
# Set to >0 only when you intentionally want slower remote enrichment.
SUGGESTED_EVENTS_REMOTE_TEAM_COUNT_FETCH_LIMIT = 0
EVENT_SEARCH_AUTOBACKFILL_ENABLED = False
EVENT_SEARCH_REMOTE_FALLBACK_ENABLED = bool(
    getattr(settings, "events_search_remote_fallback_enabled", True)
)

US_STATE_NAME_BY_CODE: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}
US_STATE_CODE_BY_NAME = {
    value.lower(): key for key, value in US_STATE_NAME_BY_CODE.items()
}

def _region_label(state_prov: str | None, country: str | None) -> str:
    normalized_country = (country or "").strip()
    if normalized_country in {"USA", "Canada"}:
        if state_prov and state_prov.strip():
            code = state_prov.strip().upper()
            if normalized_country == "USA" and code in US_STATE_NAME_BY_CODE:
                return US_STATE_NAME_BY_CODE[code]
            return state_prov.strip()
        return normalized_country
    if normalized_country:
        return normalized_country
    return "Unknown"

def _state_codes_for_query(query: str) -> set[str]:
    normalized = query.strip().lower()
    if not normalized:
        return set()
    tokens = re.findall(r"[a-z]+", normalized)
    if not tokens:
        return set()

    matches: set[str] = set()
    for token in tokens:
        if token in US_STATE_CODE_BY_NAME:
            matches.add(US_STATE_CODE_BY_NAME[token])
            continue
        if len(token) == 2:
            code = token.upper()
            if code in US_STATE_NAME_BY_CODE:
                matches.add(code)
    return matches

def _year_for_query(query: str) -> int | None:
    normalized = query.strip()
    if not re.fullmatch(r"\d{4}", normalized):
        return None
    year = int(normalized)
    if year < 1992 or year > 2100:
        return None
    return year

def _event_search_cache_key(query: str, limit: int) -> str:
    return f"events:search:v2:{query}:{int(limit)}"

def _event_search_cache_get(query: str, limit: int) -> dict | None:
    ttl_sec = int(getattr(settings, "events_search_cache_ttl_sec", 90) or 90)
    if ttl_sec <= 0:
        return None
    try:
        raw = get_cache().get(_event_search_cache_key(query, limit))
    except Exception:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None

def _event_search_cache_set(query: str, limit: int, payload: dict) -> None:
    ttl_sec = max(5, int(getattr(settings, "events_search_cache_ttl_sec", 90) or 90))
    try:
        get_cache().set(
            _event_search_cache_key(query, limit),
            json.dumps(payload, default=str),
            ttl=ttl_sec,
        )
    except Exception:
        return

def _remote_event_rows_for_search(
    *,
    query: str,
    limit: int,
    year_query: int | None,
) -> list[dict]:
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return []
    if not settings.tba_auth_key.strip():
        return []
    if not EVENT_SEARCH_REMOTE_FALLBACK_ENABLED:
        return []

    year_span = max(1, min(int(getattr(settings, "events_search_remote_year_span", 3) or 3), 5))
    if year_query is not None:
        years = [int(year_query)]
    else:
        current_year = datetime.now(timezone.utc).year
        years = [int(current_year - offset) for offset in range(year_span)]

    matched_by_key: dict[str, dict] = {}
    tba = TBAClient()
    for year in years:
        try:
            remote_payload = tba.events(int(year))
        except Exception as exc:
            logger.warning(
                "events.search remote fallback failed year=%s error=%s",
                year,
                sanitize_external_error(exc, default="remote_request_failed"),
            )
            continue
        if not isinstance(remote_payload, list):
            continue
        for item in remote_payload:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_remote_event(item, int(year))
            if normalized is None:
                continue
            searchable = " ".join(
                [
                    str(normalized.get("event_key") or ""),
                    str(normalized.get("name") or ""),
                    str(normalized.get("city") or ""),
                    str(normalized.get("state_prov") or ""),
                    str(normalized.get("country") or ""),
                ]
            ).lower()
            if normalized_query not in searchable:
                continue
            event_key = str(normalized.get("event_key") or "").strip()
            if not event_key:
                continue
            matched_by_key[event_key] = normalized

    return sorted(
        matched_by_key.values(),
        key=lambda row: (
            -int(row.get("year") or 0),
            str(row.get("event_key") or ""),
        ),
    )[: max(1, int(limit))]

def _serialize_remote_events(rows: list[dict]) -> list[dict]:
    events: list[dict] = []
    for remote in rows:
        start_date = remote.get("start_date")
        end_date = remote.get("end_date")
        start_date_iso = start_date.isoformat() if isinstance(start_date, date) else None
        end_date_iso = end_date.isoformat() if isinstance(end_date, date) else start_date_iso
        city = remote.get("city")
        state_prov = remote.get("state_prov")
        country = remote.get("country")
        events.append(
            {
                "event_key": str(remote.get("event_key") or ""),
                "name": str(remote.get("name") or remote.get("event_key") or ""),
                "year": int(remote.get("year") or 0),
                "city": city,
                "state_prov": state_prov,
                "country": country,
                "start_date": start_date_iso,
                "end_date": end_date_iso,
                "region": _region_label(state_prov, country),
            }
        )
    return events

def _serialize_events(rows):
    return [
        {
            "event_key": event_row.event_key,
            "name": event_row.name,
            "year": event_row.year,
            "city": event_profile.city if event_profile else None,
            "state_prov": event_profile.state_prov if event_profile else None,
            "country": event_profile.country if event_profile else None,
            "start_date": None,
            "end_date": None,
            "region": _region_label(
                event_profile.state_prov if event_profile else None,
                event_profile.country if event_profile else None,
            ),
        }
        for event_row, event_profile in rows
    ]

def _station_sort_key(station: str | None) -> tuple[str, int]:
    if not station:
        return ("z", 99)
    prefix = station[:1].lower()
    suffix = station[1:]
    try:
        number = int(suffix)
    except ValueError:
        number = 99
    return (prefix, number)

def _deterministic_red_win_prob(
    rating_margin: float,
    synergy_margin: float,
    projected_margin: float,
) -> float:
    # Sigmoid-blended deterministic baseline for red-alliance win probability.
    #
    # Uses the same feature margins the ML model consumes so the two are
    # directly comparable. Weights are calibrated so that:
    # - a +10 rating_margin alone → ~0.68
    # - a +20 rating_margin alone → ~0.82
    # - synergy/throughput contribute a smaller secondary nudge
    z = (
        0.075 * float(rating_margin)
        + 0.015 * float(synergy_margin)
        + 0.020 * float(projected_margin)
    )
    # Clamp z to avoid overflow on degenerate inputs
    if z > 30.0:
        return 1.0
    if z < -30.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def _has_profile_data(event_profile: models.EventProfile | None) -> bool:
    if event_profile is None:
        return False
    return any(
        bool((value or "").strip())
        for value in (event_profile.city, event_profile.state_prov, event_profile.country)
    )

def _backfill_event_profiles_for_year(
    db: Session,
    tba: TBAClient,
    year: int,
    target_event_keys: set[str] | None = None,
) -> bool:
    try:
        payload = tba.events(year)
    except Exception:
        return False

    if not isinstance(payload, list):
        return False

    changed = False
    seen_event_keys: set[str] = set()
    for event in payload:
        event_key = event.get("key")
        if not event_key:
            continue
        if event_key in seen_event_keys:
            continue
        seen_event_keys.add(event_key)
        if target_event_keys is not None and event_key not in target_event_keys:
            continue
        db.merge(
            models.Event(
                event_key=event_key,
                name=event.get("name") or event_key,
                year=int(event.get("year") or year),
            )
        )
        db.merge(
            models.EventProfile(
                event_key=event_key,
                city=event.get("city"),
                state_prov=event.get("state_prov"),
                country=event.get("country"),
            )
        )
        changed = True

    if changed:
        try:
            db.commit()
        except IntegrityError:
            # Concurrent request may have inserted the same rows first.
            # Roll back this transaction and continue with fresh query results.
            db.rollback()
            return True
    return changed

def _get_tba_client_or_503() -> TBAClient:
    if not settings.tba_auth_key.strip():
        raise HTTPException(status_code=503, detail="TBA auth key is not configured.")
    return TBAClient()

def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    token = raw[:10]
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None

def _normalize_remote_event(event: dict, default_year: int) -> dict | None:
    event_key = event.get("key")
    if not isinstance(event_key, str) or not event_key.strip():
        return None
    event_year_raw = event.get("year")
    event_year = int(event_year_raw) if isinstance(event_year_raw, int) else int(default_year)
    start_date = _parse_iso_date(event.get("start_date"))
    end_date = _parse_iso_date(event.get("end_date")) or start_date
    if start_date is not None and end_date is not None and end_date < start_date:
        start_date, end_date = end_date, start_date

    explicit_count = event.get("team_count")
    team_count_hint: int | None = int(explicit_count) if isinstance(explicit_count, int) else None
    team_keys = event.get("team_keys")
    if isinstance(team_keys, list):
        team_count_hint = len([item for item in team_keys if isinstance(item, str) and item.strip()])

    return {
        "event_key": event_key.strip(),
        "name": str(event.get("name") or event_key).strip() or event_key.strip(),
        "year": event_year,
        "city": event.get("city"),
        "state_prov": event.get("state_prov"),
        "country": event.get("country"),
        "start_date": start_date,
        "end_date": end_date,
        "team_count_hint": team_count_hint,
    }

# Runs on nearly every page load (the suggested-events picker). It used to
# db.merge() each of ~1,000 TBA events, two primary-key SELECTs apiece across the
# Phoenix-to-Virginia link: ~80 s per call, holding a pool connection throughout,
# which starved every other request. Now: two bulk reads, writes only for new or
# changed rows, and nothing at all if this process wrote the same data recently.
_REMOTE_EVENT_SYNC_TTL_SEC = 900.0
_remote_event_synced_at: dict[str, float] = {}


def _upsert_remote_event_snapshots(db: Session, event_rows: list[dict]) -> None:
    wanted: dict[str, dict] = {}
    for event in event_rows or []:
        event_key = str(event.get("event_key") or "").strip()
        if not event_key:
            continue
        event_year = event.get("year")
        wanted[event_key] = {
            "name": str(event.get("name") or event_key),
            "year": int(event_year) if isinstance(event_year, int) else 0,
            "city": event.get("city"),
            "state_prov": event.get("state_prov"),
            "country": event.get("country"),
        }
    if not wanted:
        return
    fingerprint = hashlib.sha1(json.dumps(sorted(wanted.items()), default=str).encode("utf-8")).hexdigest()
    now = time.monotonic()
    if now - _remote_event_synced_at.get(fingerprint, -_REMOTE_EVENT_SYNC_TTL_SEC) < _REMOTE_EVENT_SYNC_TTL_SEC:
        return

    keys = sorted(wanted)
    events = {row.event_key: row for row in db.query(models.Event).filter(models.Event.event_key.in_(keys))}
    profiles = {
        row.event_key: row for row in db.query(models.EventProfile).filter(models.EventProfile.event_key.in_(keys))
    }
    for event_key, fields in wanted.items():
        event = events.get(event_key)
        if event is None:
            db.add(models.Event(event_key=event_key, name=fields["name"], year=fields["year"]))
        else:
            if event.name != fields["name"]:
                event.name = fields["name"]
            if event.year != fields["year"]:
                event.year = fields["year"]
        profile = profiles.get(event_key)
        if profile is None:
            db.add(
                models.EventProfile(
                    event_key=event_key,
                    city=fields["city"],
                    state_prov=fields["state_prov"],
                    country=fields["country"],
                )
            )
        else:
            for column in ("city", "state_prov", "country"):
                if getattr(profile, column) != fields[column]:
                    setattr(profile, column, fields[column])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return
    _remote_event_synced_at[fingerprint] = now

def _event_team_counts(db: Session, event_keys: set[str] | None = None) -> dict[str, int]:
    query = (
        db.query(
            models.EventTeam.event_key,
            func.count(models.EventTeam.team_key),
        )
        .group_by(models.EventTeam.event_key)
    )
    if event_keys:
        query = query.filter(models.EventTeam.event_key.in_(sorted(event_keys)))
    rows = query.all()
    return {
        str(event_key): int(count)
        for event_key, count in rows
        if isinstance(event_key, str)
    }

def _local_events_most_teams(
    db: Session,
    years: list[int],
    limit: int,
) -> tuple[list[dict], int | None, str]:
    rows = (
        db.query(
            models.Event,
            models.EventProfile,
            func.count(models.EventTeam.team_key).label("team_count"),
        )
        .outerjoin(models.EventProfile, models.EventProfile.event_key == models.Event.event_key)
        .outerjoin(models.EventTeam, models.EventTeam.event_key == models.Event.event_key)
        .filter(models.Event.year.in_(years))
        .group_by(
            models.Event.event_key,
            models.Event.name,
            models.Event.year,
            models.EventProfile.event_key,
            models.EventProfile.city,
            models.EventProfile.state_prov,
            models.EventProfile.country,
        )
        .all()
    )
    if not rows:
        latest_year = db.query(func.max(models.Event.year)).scalar()
        if latest_year is None:
            return [], None, "none"
        rows = (
            db.query(
                models.Event,
                models.EventProfile,
                func.count(models.EventTeam.team_key).label("team_count"),
            )
            .outerjoin(models.EventProfile, models.EventProfile.event_key == models.Event.event_key)
            .outerjoin(models.EventTeam, models.EventTeam.event_key == models.Event.event_key)
            .filter(models.Event.year == int(latest_year))
            .group_by(
                models.Event.event_key,
                models.Event.name,
                models.Event.year,
                models.EventProfile.event_key,
                models.EventProfile.city,
                models.EventProfile.state_prov,
                models.EventProfile.country,
            )
            .all()
        )
        source = "local_latest_most_teams"
        selected_year = int(latest_year)
    else:
        source = "local_most_teams"
        selected_year = years[0] if years else None

    rows_sorted = sorted(
        rows,
        key=lambda item: (
            -int(item[2] or 0),
            -int(item[0].year),
            item[0].event_key,
        ),
    )[:limit]

    event_keys = [event_row.event_key for event_row, _event_profile, _team_count in rows_sorted]
    schedule_bounds_by_event: dict[str, tuple[str | None, str | None]] = {}
    if event_keys:
        bounds_rows = (
            db.query(
                models.Match.event_key,
                func.min(models.Match.time).label("min_time"),
                func.max(models.Match.time).label("max_time"),
            )
            .filter(models.Match.event_key.in_(event_keys))
            .group_by(models.Match.event_key)
            .all()
        )
        for event_key, min_time, max_time in bounds_rows:
            min_epoch = int(min_time) if isinstance(min_time, int) else None
            max_epoch = int(max_time) if isinstance(max_time, int) else None
            start_date = (
                datetime.fromtimestamp(min_epoch, tz=timezone.utc).date().isoformat()
                if min_epoch is not None and min_epoch > 0
                else None
            )
            end_date = (
                datetime.fromtimestamp(max_epoch, tz=timezone.utc).date().isoformat()
                if max_epoch is not None and max_epoch > 0
                else start_date
            )
            schedule_bounds_by_event[str(event_key)] = (start_date, end_date)

    return (
        [
            {
                "event_key": event_row.event_key,
                "name": event_row.name,
                "year": int(event_row.year),
                "city": event_profile.city if event_profile else None,
                "state_prov": event_profile.state_prov if event_profile else None,
                "country": event_profile.country if event_profile else None,
                "start_date": schedule_bounds_by_event.get(event_row.event_key, (None, None))[0],
                "end_date": schedule_bounds_by_event.get(event_row.event_key, (None, None))[1],
                "region": _region_label(
                    event_profile.state_prov if event_profile else None,
                    event_profile.country if event_profile else None,
                ),
            }
            for event_row, event_profile, _ in rows_sorted
        ],
        selected_year if rows_sorted else None,
        source if rows_sorted else "none",
    )

@router.get("/suggested")
def suggested_events(
    preferred_year: int = CURRENT_SEASON_YEAR,
    fallback_year: int = PREVIOUS_SEASON_YEAR,
    limit: int = 20,
    prioritize_running: bool = True,
    team_count_fetch_limit: int | None = Query(default=None, ge=0, le=500),
    db: Session = Depends(get_db),
):
    capped_limit = max(1, min(limit, 250))
    years = [preferred_year] + ([fallback_year] if fallback_year != preferred_year else [])
    now_date = datetime.now(timezone.utc).date()

    if not settings.tba_auth_key.strip():
        local_events, selected_year, source = _local_events_most_teams(db, years, capped_limit)
        return {
            "ok": True,
            "preferred_year": preferred_year,
            "fallback_year": fallback_year,
            "selected_year": selected_year,
            "source": source,
            "count": len(local_events),
            "events": local_events,
        }

    tba = TBAClient()
    remote_events_by_key: dict[str, dict] = {}
    remote_failed = False
    for year in years:
        try:
            payload = tba.events(year)
        except Exception:
            remote_failed = True
            continue
        if not isinstance(payload, list):
            remote_failed = True
            continue
        for event in payload:
            if not isinstance(event, dict):
                continue
            normalized = _normalize_remote_event(event, year)
            if normalized is not None:
                remote_events_by_key[str(normalized["event_key"])] = normalized

    remote_events = list(remote_events_by_key.values())

    if remote_events:
        if not settings.public_readonly_mode:
            _upsert_remote_event_snapshots(db, remote_events)
        team_counts = _event_team_counts(db, {str(item["event_key"]) for item in remote_events})
        team_count_cache: dict[str, int] = {
            event_key: count
            for event_key, count in team_counts.items()
        }

        # Fill team counts for a bounded set of events so "most teams" is meaningful.
        max_remote_team_count_fetches = (
            int(max(0, team_count_fetch_limit))
            if team_count_fetch_limit is not None
            else int(max(0, SUGGESTED_EVENTS_REMOTE_TEAM_COUNT_FETCH_LIMIT))
        )
        team_count_fetches = 0
        for event in sorted(remote_events, key=lambda row: (-int(row.get("year") or 0), str(row.get("event_key") or ""))):
            if team_count_fetches >= max_remote_team_count_fetches:
                break
            event_key = str(event.get("event_key") or "")
            if not event_key or event_key in team_count_cache:
                continue
            try:
                team_payload = tba.event_teams(event_key)
            except Exception as exc:
                logger.warning("Failed to fetch TBA team count for event %s: %s", event_key, exc)
                continue
            if not isinstance(team_payload, list):
                continue
            team_count_cache[event_key] = len(
                [row for row in team_payload if isinstance(row, dict) and isinstance(row.get("key"), str)]
            )
            team_count_fetches += 1

        def score_event_team_count(event: dict) -> int:
            event_key = str(event.get("event_key") or "")
            hint = event.get("team_count_hint")
            if isinstance(hint, int) and hint >= 0:
                team_count_cache[event_key] = max(team_count_cache.get(event_key, 0), hint)
            count = team_count_cache.get(event_key)
            if count is not None:
                return int(count)
            return 0

        running_events = [
            event
            for event in remote_events
            if isinstance(event.get("start_date"), date)
            and isinstance(event.get("end_date"), date)
            and event["start_date"] <= now_date <= event["end_date"]
        ]
        if prioritize_running and running_events:
            running_events.sort(
                key=lambda event: (
                    -score_event_team_count(event),
                    -int(event.get("year") or 0),
                    str(event.get("event_key") or ""),
                )
            )
            selected = running_events[:capped_limit]
            selected_year = (
                preferred_year
                if any(int(row.get("year") or 0) == preferred_year for row in selected)
                else (int(selected[0].get("year") or 0) if selected else None)
            )
            return {
                "ok": True,
                "preferred_year": preferred_year,
                "fallback_year": fallback_year,
                "selected_year": selected_year,
                "source": "remote_live_now",
                "count": len(selected),
                "events": [
                    {
                        "event_key": str(row["event_key"]),
                        "name": str(row["name"]),
                        "year": int(row["year"]),
                        "city": row.get("city"),
                        "state_prov": row.get("state_prov"),
                        "country": row.get("country"),
                        "start_date": row["start_date"].isoformat() if isinstance(row.get("start_date"), date) else None,
                        "end_date": row["end_date"].isoformat() if isinstance(row.get("end_date"), date) else None,
                        "region": _region_label(
                            row.get("state_prov"),
                            row.get("country"),
                        ),
                    }
                    for row in selected
                ],
            }

        remote_events.sort(
            key=lambda event: (
                -score_event_team_count(event),
                -int(event.get("year") or 0),
                str(event.get("event_key") or ""),
            )
        )
        selected = remote_events[:capped_limit]
        selected_year = (
            preferred_year
            if any(int(row.get("year") or 0) == preferred_year for row in selected)
            else (int(selected[0].get("year") or 0) if selected else None)
        )
        return {
            "ok": True,
            "preferred_year": preferred_year,
            "fallback_year": fallback_year,
            "selected_year": selected_year,
            "source": "remote_most_teams",
            "count": len(selected),
            "events": [
                {
                    "event_key": str(row["event_key"]),
                    "name": str(row["name"]),
                    "year": int(row["year"]),
                    "city": row.get("city"),
                    "state_prov": row.get("state_prov"),
                    "country": row.get("country"),
                    "start_date": row["start_date"].isoformat() if isinstance(row.get("start_date"), date) else None,
                    "end_date": row["end_date"].isoformat() if isinstance(row.get("end_date"), date) else None,
                    "region": _region_label(
                        row.get("state_prov"),
                        row.get("country"),
                    ),
                }
                for row in selected
            ],
        }

    local_events, selected_year, source = _local_events_most_teams(db, years, capped_limit)
    if source == "local_most_teams" and remote_failed:
        source = "local_most_teams_remote_unavailable"
    return {
        "ok": True,
        "preferred_year": preferred_year,
        "fallback_year": fallback_year,
        "selected_year": selected_year,
        "source": source,
        "count": len(local_events),
        "events": local_events,
    }

@router.get("/search")
def search_events(q: str, limit: int = 20, db: Session = Depends(get_db)):
    query = q.strip().lower()
    if not query:
        return {"ok": True, "query": q, "count": 0, "events": []}

    year_query = _year_for_query(query)
    capped_limit = max(1, min(limit, 1000))
    cached = _event_search_cache_get(query, capped_limit)
    if isinstance(cached, dict):
        cached_events = cached.get("events")
        if isinstance(cached_events, list):
            return {
                "ok": True,
                "query": q,
                "count": len(cached_events),
                "events": cached_events,
            }

    pattern = f"%{query}%"
    state_codes = sorted(_state_codes_for_query(query))

    def _query_rows():
        conditions = [
            func.lower(models.Event.event_key).like(pattern),
            func.lower(models.Event.name).like(pattern),
            func.lower(func.coalesce(models.EventProfile.state_prov, "")).like(pattern),
            func.lower(func.coalesce(models.EventProfile.country, "")).like(pattern),
            func.lower(func.coalesce(models.EventProfile.city, "")).like(pattern),
        ]
        if year_query is not None:
            conditions.append(models.Event.year == year_query)
        if state_codes:
            conditions.append(func.upper(func.coalesce(models.EventProfile.state_prov, "")).in_(state_codes))
        return (
            db.query(models.Event, models.EventProfile)
            .outerjoin(models.EventProfile, models.EventProfile.event_key == models.Event.event_key)
            .filter(or_(*conditions))
            .order_by(models.Event.year.desc(), models.Event.event_key.asc())
            .limit(capped_limit)
            .all()
        )

    if year_query is not None:
        local_year_event_count = (
            db.query(func.count(models.Event.event_key))
            .filter(models.Event.year == year_query)
            .scalar()
            or 0
        )
        if (
            EVENT_SEARCH_AUTOBACKFILL_ENABLED
            and local_year_event_count < 40
            and not settings.public_readonly_mode
        ):
            tba = TBAClient()
            _backfill_event_profiles_for_year(db, tba, year_query)

    if state_codes:
        local_state_event_count = (
            db.query(func.count(models.Event.event_key))
            .join(models.EventProfile, models.EventProfile.event_key == models.Event.event_key)
            .filter(
                models.Event.year.in_((CURRENT_SEASON_YEAR, PREVIOUS_SEASON_YEAR)),
                func.upper(func.coalesce(models.EventProfile.state_prov, "")).in_(state_codes),
            )
            .scalar()
            or 0
        )
        if (
            EVENT_SEARCH_AUTOBACKFILL_ENABLED
            and local_state_event_count < 8
            and not settings.public_readonly_mode
        ):
            tba = TBAClient()
            for year in (CURRENT_SEASON_YEAR, PREVIOUS_SEASON_YEAR):
                _backfill_event_profiles_for_year(db, tba, year)

    rows = _query_rows()

    missing_by_year: dict[int, set[str]] = {}
    for event_row, event_profile in rows:
        if _has_profile_data(event_profile):
            continue
        missing_by_year.setdefault(int(event_row.year), set()).add(event_row.event_key)

    if missing_by_year and EVENT_SEARCH_AUTOBACKFILL_ENABLED and not settings.public_readonly_mode:
        tba = TBAClient()
        did_backfill = False
        for year, event_keys in missing_by_year.items():
            did_backfill = _backfill_event_profiles_for_year(db, tba, year, event_keys) or did_backfill
        if did_backfill:
            rows = _query_rows()

    if not rows and state_codes and EVENT_SEARCH_AUTOBACKFILL_ENABLED and not settings.public_readonly_mode:
        tba = TBAClient()
        did_backfill = False
        for year in (CURRENT_SEASON_YEAR, PREVIOUS_SEASON_YEAR):
            did_backfill = _backfill_event_profiles_for_year(db, tba, year) or did_backfill
        if did_backfill:
            rows = _query_rows()

    events = _serialize_events(rows)

    # Year-only searches power calendar views; enrich/expand with TBA dates so events
    # are grouped by actual start month instead of collapsing into "Date TBA".
    if year_query is not None and settings.tba_auth_key.strip():
        try:
            remote_payload = TBAClient().events(year_query)
        except Exception:
            remote_payload = []

        if isinstance(remote_payload, list):
            remote_by_key: dict[str, dict] = {}
            for item in remote_payload:
                if not isinstance(item, dict):
                    continue
                normalized = _normalize_remote_event(item, year_query)
                if normalized is None:
                    continue
                event_key = str(normalized.get("event_key") or "").strip()
                if not event_key:
                    continue
                remote_by_key[event_key] = normalized

            if remote_by_key:
                merged_by_key: dict[str, dict] = {}
                for event in events:
                    event_key = str(event.get("event_key") or "").strip()
                    if not event_key:
                        continue
                    remote = remote_by_key.get(event_key)
                    if remote is None:
                        merged_by_key[event_key] = event
                        continue

                    start_date = remote.get("start_date")
                    end_date = remote.get("end_date")
                    start_date_iso = start_date.isoformat() if isinstance(start_date, date) else None
                    end_date_iso = end_date.isoformat() if isinstance(end_date, date) else start_date_iso
                    city = event.get("city") or remote.get("city")
                    state_prov = event.get("state_prov") or remote.get("state_prov")
                    country = event.get("country") or remote.get("country")

                    merged_by_key[event_key] = {
                        **event,
                        "name": str(event.get("name") or remote.get("name") or event_key),
                        "year": int(event.get("year") or remote.get("year") or year_query),
                        "city": city,
                        "state_prov": state_prov,
                        "country": country,
                        "start_date": event.get("start_date") or start_date_iso,
                        "end_date": event.get("end_date") or end_date_iso,
                        "region": _region_label(state_prov, country),
                    }

                for event_key, remote in remote_by_key.items():
                    if event_key in merged_by_key:
                        continue
                    start_date = remote.get("start_date")
                    end_date = remote.get("end_date")
                    start_date_iso = start_date.isoformat() if isinstance(start_date, date) else None
                    end_date_iso = end_date.isoformat() if isinstance(end_date, date) else start_date_iso
                    city = remote.get("city")
                    state_prov = remote.get("state_prov")
                    country = remote.get("country")
                    merged_by_key[event_key] = {
                        "event_key": event_key,
                        "name": str(remote.get("name") or event_key),
                        "year": int(remote.get("year") or year_query),
                        "city": city,
                        "state_prov": state_prov,
                        "country": country,
                        "start_date": start_date_iso,
                        "end_date": end_date_iso,
                        "region": _region_label(state_prov, country),
                    }

                events = sorted(
                    merged_by_key.values(),
                    key=lambda row: (
                        -int(row.get("year") or 0),
                        str(row.get("event_key") or ""),
                    ),
                )[:capped_limit]

    if not events and EVENT_SEARCH_REMOTE_FALLBACK_ENABLED:
        remote_rows = _remote_event_rows_for_search(
            query=query,
            limit=capped_limit,
            year_query=year_query,
        )
        if remote_rows:
            if not settings.public_readonly_mode:
                _upsert_remote_event_snapshots(db, remote_rows)
            events = _serialize_remote_events(remote_rows)

    payload = {
        "ok": True,
        "query": q,
        "count": len(events),
        "events": events,
    }
    _event_search_cache_set(query, capped_limit, payload)
    return payload

@router.post("/{event_key}/ingest")
def ingest_event(
    event_key: str,
    run_post_compute: bool | None = None,
    db: Session = Depends(get_db),
):
    require_write_access("Event ingest")
    tba = _get_tba_client_or_503()
    from app.services.events.ingest import ingest_event as _ingest_event_service

    return _ingest_event_service(
        event_key=event_key,
        tba=tba,
        db=db,
        run_post_compute=run_post_compute,
    )

@router.get("/{event_key}/stats")
def get_event_team_stats(
    event_key: str,
    request: Request,
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    source = "local"
    upserted = 0
    if refresh:
        require_admin_access(request, "Event stats refresh")
        require_write_access("Event stats refresh")
        tba = _get_tba_client_or_503()
        from app.services.events.ingest import upsert_event_team_stats_from_tba
        upserted, source = upsert_event_team_stats_from_tba(db, tba, event_key)

    rows = (
        db.query(models.EventTeamStat, models.Team)
        .outerjoin(models.Team, models.Team.team_key == models.EventTeamStat.team_key)
        .filter(models.EventTeamStat.event_key == event_key)
        .order_by(models.EventTeamStat.opr.desc().nullslast(), models.EventTeamStat.team_key.asc())
        .all()
    )
    if not refresh and not rows:
        source = "local_missing"

    return {
        "ok": True,
        "event_key": event_key,
        "source": source,
        "upserted": upserted,
        "count": len(rows),
        "stats": [
            {
                "team_key": stat.team_key,
                "team_number": team.team_number if team is not None else None,
                "nickname": team.nickname if team is not None else None,
                "opr": stat.opr,
                "dpr": stat.dpr,
                "ccwm": stat.ccwm,
                "updated_at": stat.updated_at.isoformat() if stat.updated_at else None,
            }
            for stat, team in rows
        ],
    }

@router.get("/{event_key}/schedule-with-synergy", response_model=ScheduleWithSynergyResponse)
def get_event_schedule_with_synergy(
    event_key: str,
    request: Request,
    synergy_model_version: str = Query(default=SYNERGY_MODEL_VERSION, alias="model_version"),
    include_pair_breakdown: bool = Query(default=False),
    include_ml_predictions: bool = Query(default=True),
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")

    precompute = None
    if refresh:
        require_admin_access(request, "Schedule synergy refresh")
        require_write_access("Schedule synergy refresh")
        try:
            precompute = precompute_event_synergy(db, event_key, model_version=synergy_model_version)
        except Exception as exc:
            precompute = {"ok": False, "detail": sanitize_external_error(exc, default="Synergy refresh failed.")}

    matches = (
        db.query(models.Match)
        .filter(models.Match.event_key == event_key)
        .all()
    )
    matches.sort(
        key=lambda row: (
            COMP_LEVEL_ORDER.get(row.comp_level, 99),
            row.set_number,
            row.match_number,
        )
    )

    team_rows = (
        db.query(models.MatchTeam, models.Team)
        .outerjoin(models.Team, models.Team.team_key == models.MatchTeam.team_key)
        .filter(models.MatchTeam.event_key == event_key)
        .all()
    )
    teams_by_match: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"red": [], "blue": []})
    for match_team, team in team_rows:
        alliance = (match_team.alliance or "").lower()
        if alliance not in {"red", "blue"}:
            continue
        teams_by_match[match_team.match_key][alliance].append(
            {
                "team_key": match_team.team_key,
                "team_number": team.team_number if team else None,
                "nickname": team.nickname if team else None,
                "station": match_team.station,
            }
        )

    projection_rows = (
        db.query(models.MatchSynergyProjection)
        .filter(
            models.MatchSynergyProjection.event_key == event_key,
            models.MatchSynergyProjection.model_version == synergy_model_version,
        )
        .all()
    )
    projection_by_key = {
        (row.match_key, row.alliance_color): row
        for row in projection_rows
    }

    rating_rows = (
        db.query(models.EventTeamRating)
        .filter(models.EventTeamRating.event_key == event_key)
        .all()
    )
    rating_by_team = {str(row.team_key or "").strip().lower(): row for row in rating_rows if row.team_key}

    def _team_rating_snapshot(team_key: str) -> tuple[float, float]:
        row = rating_by_team.get(str(team_key or "").strip().lower())
        if row is None:
            return 50.0, 0.0
        rating = _as_float(row.rating_0_100)
        confidence = _as_float(row.confidence_0_1)
        return (
            float(rating) if rating is not None else 50.0,
            float(confidence) if confidence is not None else 0.0,
        )

    # The fuel model needs playing order, not display order: a postponed match played
    # later must not feed the prediction of one listed after it.
    display_index = {match.match_key: index for index, match in enumerate(matches)}
    played_order = sorted(
        matches,
        key=lambda match: (
            int(match.actual_time or match.time or 0) or 10**12,
            display_index[match.match_key],
        ),
    )
    fuel_details = fuel_margin_details(
        db,
        event_key,
        [
            (
                match.match_key,
                [str(item.get("team_key") or "") for item in teams_by_match.get(match.match_key, {}).get("red", [])],
                [str(item.get("team_key") or "") for item in teams_by_match.get(match.match_key, {}).get("blue", [])],
            )
            for match in played_order
        ],
        event_start=min((int(match.time) for match in matches if match.time), default=None),
    )
    fuel_margin_by_match = {key: margin for key, (margin, _seen) in fuel_details.items()}

    ml_feature_rows: list[dict[str, float | str]] = []
    deterministic_prob_by_match: dict[str, float] = {}
    rating_prob_by_match: dict[str, float] = {}
    schedule_rows = []
    for match in matches:
        teams_group = teams_by_match.get(match.match_key, {"red": [], "blue": []})
        red_teams = sorted(teams_group["red"], key=lambda item: _station_sort_key(item.get("station")))
        blue_teams = sorted(teams_group["blue"], key=lambda item: _station_sort_key(item.get("station")))

        def _projection_payload(alliance: str) -> dict:
            row = projection_by_key.get((match.match_key, alliance))
            if row is None:
                return {
                    "available": False,
                    "alliance_synergy_score_0_100": None,
                    "alliance_synergy_points": 0.0,
                    "expected_throughput": None,
                    "projected_throughput": None,
                    "confidence_0_1": 0.0,
                    "source_label": "projection",
                    "pair_breakdown": [],
                }
            return {
                "available": True,
                "alliance_synergy_score_0_100": row.alliance_synergy_score_0_100,
                "alliance_synergy_points": row.alliance_synergy_points,
                "expected_throughput": row.expected_throughput,
                "projected_throughput": row.projected_throughput,
                "confidence_0_1": row.confidence_0_1,
                "source_label": row.source_label,
                "pair_breakdown": (row.pair_breakdown or []) if include_pair_breakdown else [],
                "computed_at": row.computed_at.isoformat() if row.computed_at else None,
            }

        red_projection = _projection_payload("red")
        blue_projection = _projection_payload("blue")
        schedule_rows.append(
            {
                "match_key": match.match_key,
                "comp_level": match.comp_level,
                "set_number": match.set_number,
                "match_number": match.match_number,
                "scheduled_time": match.time,
                "predicted_time": match.predicted_time,
                "actual_time": match.actual_time,
                "red": {
                    "teams": red_teams,
                    "synergy": red_projection,
                },
                "blue": {
                    "teams": blue_teams,
                    "synergy": blue_projection,
                },
                "prediction": {
                    "available": False,
                    "source_label": "ml_shadow_match_outcome",
                    "model_key": MATCH_OUTCOME_MODEL_KEY,
                    "model_version": None,
                    "red_win_prob": None,
                    "blue_win_prob": None,
                    "favored_alliance": None,
                    "edge_confidence_0_1": None,
                },
            }
        )

        red_ratings: list[float] = []
        blue_ratings: list[float] = []
        red_confidences: list[float] = []
        blue_confidences: list[float] = []
        for item in red_teams:
            rating_value, confidence_value = _team_rating_snapshot(str(item.get("team_key") or ""))
            red_ratings.append(rating_value)
            red_confidences.append(confidence_value)
        for item in blue_teams:
            rating_value, confidence_value = _team_rating_snapshot(str(item.get("team_key") or ""))
            blue_ratings.append(rating_value)
            blue_confidences.append(confidence_value)

        if include_ml_predictions and red_ratings and blue_ratings:
            red_rating_mean = sum(red_ratings) / float(len(red_ratings))
            blue_rating_mean = sum(blue_ratings) / float(len(blue_ratings))
            red_conf_mean = sum(red_confidences) / float(len(red_confidences)) if red_confidences else 0.0
            blue_conf_mean = sum(blue_confidences) / float(len(blue_confidences)) if blue_confidences else 0.0
            red_synergy_points = float(_as_float(red_projection.get("alliance_synergy_points")) or 0.0)
            blue_synergy_points = float(_as_float(blue_projection.get("alliance_synergy_points")) or 0.0)
            red_projected = float(_as_float(red_projection.get("projected_throughput")) or 0.0)
            blue_projected = float(_as_float(blue_projection.get("projected_throughput")) or 0.0)
            rating_margin = red_rating_mean - blue_rating_mean
            synergy_margin = red_synergy_points - blue_synergy_points
            projected_margin = red_projected - blue_projected
            ml_feature_rows.append(
                {
                    "event_key": event_key,
                    "match_key": match.match_key,
                    "red_rating_mean": red_rating_mean,
                    "blue_rating_mean": blue_rating_mean,
                    "rating_margin": rating_margin,
                    "red_confidence_mean": red_conf_mean,
                    "blue_confidence_mean": blue_conf_mean,
                    "confidence_margin": red_conf_mean - blue_conf_mean,
                    "red_synergy_points": red_synergy_points,
                    "blue_synergy_points": blue_synergy_points,
                    "synergy_margin": synergy_margin,
                    "red_projected_throughput": red_projected,
                    "blue_projected_throughput": blue_projected,
                    "projected_margin": projected_margin,
                }
            )
            rating_prob_by_match[str(match.match_key or "").strip().lower()] = (
                _deterministic_red_win_prob(rating_margin, synergy_margin, projected_margin)
            )

    # Official fuel rates known before the match decide it; the rating formula covers
    # matches with no fuel data yet (a new event whose teams haven't played before).
    for match in matches:
        match_key = str(match.match_key or "").strip().lower()
        fuel_margin = fuel_margin_by_match.get(match_key)
        if fuel_margin is not None:
            deterministic_prob_by_match[match_key] = fuel_red_win_prob(fuel_margin, matches_seen=fuel_details[match_key][1])
        elif match_key in rating_prob_by_match:
            deterministic_prob_by_match[match_key] = rating_prob_by_match[match_key]

    ml_prediction_by_match: dict[str, float] = {}
    ml_prediction_model_version: str | None = None
    if include_ml_predictions and bool(getattr(settings, "ml_shadow_enabled", False)):
        try:
            ml_payload = infer_match_outcome_shadow_from_rows(db, rows=ml_feature_rows)
        except Exception as exc:
            ml_payload = {"ok": False, "reason": "inference_failed", "detail": str(exc), "predictions": []}
        if bool(ml_payload.get("ok")):
            ml_prediction_model_version = str(ml_payload.get("model_version") or "") or None
            for prediction in ml_payload.get("predictions") or []:
                if not isinstance(prediction, dict):
                    continue
                match_key = str(prediction.get("match_key") or "").strip().lower()
                red_win_prob = _as_float(prediction.get("red_win_prob"))
                if not match_key or red_win_prob is None:
                    continue
                ml_prediction_by_match[match_key] = max(0.0, min(1.0, float(red_win_prob)))

    ml_blend_knob = max(0.0, min(1.0, float(getattr(settings, "ml_match_outcome_blend", 0.0) or 0.0)))
    ml_blended_prediction_count = 0
    for row in schedule_rows:
        match_key = str(row.get("match_key") or "").strip().lower()
        det_prob = deterministic_prob_by_match.get(match_key)
        ml_prob = ml_prediction_by_match.get(match_key)
        if det_prob is None and ml_prob is None:
            continue

        effective_blend = ml_blend_knob if ml_prob is not None else 0.0
        if det_prob is None:
            # ML-only fallback (shouldn't normally happen since deterministic
            # is computed whenever ratings exist, but be defensive).
            final_prob = float(ml_prob) if ml_prob is not None else 0.5
            source_label = "ml_shadow_match_outcome_live"
        elif ml_prob is None or effective_blend <= 0.0:
            final_prob = float(det_prob)
            source_label = "deterministic_fuel_v1" if match_key in fuel_margin_by_match else "deterministic_rating_synergy_v1"
        elif effective_blend >= 1.0:
            final_prob = float(ml_prob)
            source_label = "ml_shadow_match_outcome_live"
        else:
            final_prob = (1.0 - effective_blend) * float(det_prob) + effective_blend * float(ml_prob)
            source_label = "blended_det_ml_v1"

        final_prob = max(0.0, min(1.0, float(final_prob)))
        blue_win_prob = max(0.0, min(1.0, 1.0 - final_prob))
        if final_prob > 0.5:
            favored: str | None = "red"
        elif final_prob < 0.5:
            favored = "blue"
        else:
            favored = None

        if ml_prob is not None and effective_blend > 0.0:
            ml_blended_prediction_count += 1

        row["prediction"] = {
            "available": True,
            "source_label": source_label,
            "model_key": MATCH_OUTCOME_MODEL_KEY,
            "model_version": ml_prediction_model_version if ml_prob is not None else None,
            "red_win_prob": round(final_prob, 4),
            "blue_win_prob": round(blue_win_prob, 4),
            "red_win_prob_deterministic": round(float(det_prob), 4) if det_prob is not None else None,
            "red_win_prob_rating": (
                round(rating_prob_by_match[match_key], 4) if match_key in rating_prob_by_match else None
            ),
            "fuel_margin_per_min": (
                round(fuel_margin_by_match[match_key], 2) if match_key in fuel_margin_by_match else None
            ),
            "red_win_prob_ml": round(float(ml_prob), 4) if ml_prob is not None else None,
            "prediction_blend": round(effective_blend, 4),
            "favored_alliance": favored,
            "edge_confidence_0_1": round(abs(final_prob - 0.5) * 2.0, 4),
        }

    return {
        "ok": True,
        "event_key": event_key,
        "event_name": event.name,
        "model_version": synergy_model_version,
        "projection_count": len(projection_rows),
        "ml_prediction_model_version": ml_prediction_model_version,
        "ml_prediction_count": len(ml_prediction_by_match),
        "ml_blended_prediction_count": ml_blended_prediction_count,
        "deterministic_prediction_count": len(deterministic_prob_by_match),
        "fuel_prediction_count": len(fuel_margin_by_match),
        "prediction_blend": round(ml_blend_knob, 4),
        "count": len(schedule_rows),
        "precompute": precompute,
        "matches": schedule_rows,
    }

@router.get("/{event_key}/rankings")
def get_event_rankings(event_key: str):
    tba = _get_tba_client_or_503()
    try:
        payload = tba.event_rankings(event_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch TBA rankings for {event_key}") from exc
    return {
        "ok": True,
        "event_key": event_key,
        "source": "tba",
        "rankings": payload,
    }

@router.get("/{event_key}/team-statuses")
def get_event_team_statuses(event_key: str):
    tba = _get_tba_client_or_503()
    try:
        payload = tba.event_team_statuses(event_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch TBA team statuses for {event_key}") from exc
    return {
        "ok": True,
        "event_key": event_key,
        "source": "tba",
        "team_statuses": payload,
    }

@router.get("/{event_key}/insights")
def get_event_insights(event_key: str):
    tba = _get_tba_client_or_503()
    try:
        payload = tba.event_insights(event_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch TBA insights for {event_key}") from exc
    return {
        "ok": True,
        "event_key": event_key,
        "source": "tba",
        "insights": payload,
    }

@router.get("/{event_key}/predictions")
def get_event_predictions(event_key: str):
    tba = _get_tba_client_or_503()
    try:
        payload = tba.event_predictions(event_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch TBA predictions for {event_key}") from exc
    return {
        "ok": True,
        "event_key": event_key,
        "source": "tba",
        "predictions": payload,
    }

@router.get("/{event_key}/awards")
def get_event_awards(event_key: str):
    tba = _get_tba_client_or_503()
    try:
        payload = tba.event_awards(event_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch TBA awards for {event_key}") from exc
    awards = payload if isinstance(payload, list) else []
    return {
        "ok": True,
        "event_key": event_key,
        "source": "tba",
        "count": len(awards),
        "awards": awards,
    }
