import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin_access, require_write_access
from app.db import models
from app.db.session import get_db
from app.api.schemas import ScheduleResponse, TeamLiveFormResponse
from app.services.cache import get_cache
from app.services.events.first_events_client import FIRSTEventsClient
from app.services.phase_windows import compute_phase_windows_payload, get_or_compute_match_phase_windows
from app.services.utils import COMP_LEVEL_ORDER
from app.tba.client import TBAClient, TBAClientError

router = APIRouter(prefix="/matches", tags=["matches"])

COMP_LEVEL_LABEL: dict[str, str] = {
    "qm": "QM",
    "ef": "EF",
    "qf": "QF",
    "sf": "SF",
    "f": "F",
}

FIRST_TOURNAMENT_LEVELS = {"Practice", "Qualification", "Playoff", "None"}
SUPPORTED_EMBED_TYPES = {"youtube", "iframe"}
YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
LIVE_MATCH_WINDOW_SEC = 165
FORM_WINDOW_MATCHES = 5
SCHEDULE_KNOCKOUT_TOPUP_MIN_INTERVAL_SEC = 180
KNOCKOUT_COMP_LEVELS = {"ef", "qf", "sf", "f", "playoff", "playoffs", "pf", "elim", "elimination"}
_LIVE_RESULTS_CACHE_PREFIX = "matches:live_results:"
_SCHEDULE_KNOCKOUT_TOPUP_LOCK_PREFIX = "matches:knockout_topup_lock:"


def _cached_live_results_for_event(tba: TBAClient, event_key: str) -> dict[str, dict[str, Any]]:
    cache_key = str(event_key or "").strip().lower()
    if not cache_key:
        return {}
    cache = get_cache()
    redis_key = f"{_LIVE_RESULTS_CACHE_PREFIX}{cache_key}"
    raw = cache.get(redis_key)
    if raw:
        try:
            cached = json.loads(raw)
            if isinstance(cached, dict):
                return cached
        except (ValueError, TypeError):
            pass

    result_by_match_key: dict[str, dict[str, Any]] = {}
    try:
        remote_matches = tba.event_matches(cache_key)
        if isinstance(remote_matches, list):
            for payload in remote_matches:
                if not isinstance(payload, dict):
                    continue
                match_key_raw = payload.get("key")
                if not isinstance(match_key_raw, str) or not match_key_raw.strip():
                    continue
                result_by_match_key[match_key_raw.strip().lower()] = _match_result_snapshot(payload)
    except (TBAClientError, RuntimeError, ValueError, TypeError):
        # Keep schedule endpoint resilient even when live scoring metadata is unavailable.
        result_by_match_key = {}

    ttl_sec = max(1, int(settings.matches_live_results_cache_ttl_sec or 5))
    cache.set(redis_key, json.dumps(result_by_match_key), ttl=ttl_sec)
    return result_by_match_key


def _event_team_nickname_overrides(
    tba: TBAClient,
    event_key: str,
    missing_team_keys: set[str],
) -> dict[str, str]:
    if not missing_team_keys:
        return {}
    try:
        payload = tba.event_teams(event_key)
    except (TBAClientError, RuntimeError, ValueError, TypeError):
        return {}
    if not isinstance(payload, list):
        return {}

    overrides: dict[str, str] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        team_key = str(row.get("key") or "").strip().lower()
        if not team_key:
            team_number = row.get("team_number")
            if isinstance(team_number, int) and team_number > 0:
                team_key = f"frc{team_number}"
        if not team_key or team_key not in missing_team_keys:
            continue
        nickname_raw = row.get("nickname") or row.get("name")
        if isinstance(nickname_raw, str):
            nickname = nickname_raw.strip()
            if nickname:
                overrides[team_key] = nickname
    return overrides


def _get_tba_client_or_503() -> TBAClient:
    if not settings.tba_auth_key.strip():
        raise HTTPException(status_code=503, detail="TBA auth key is not configured.")
    return TBAClient()


def _station_sort_key(station: str | None) -> tuple[str, int]:
    if not station:
        return ("z", 99)
    prefix = station[0].lower()
    suffix = station[1:]
    try:
        number = int(suffix)
    except ValueError:
        number = 99
    return (prefix, number)


def _team_number_from_key(team_key: str) -> int:
    value = team_key.strip().lower()
    if value.startswith("frc"):
        value = value[3:]
    try:
        return int(value)
    except ValueError:
        return 0


def _display_match_name(comp_level: str, set_number: int, match_number: int) -> str:
    label = COMP_LEVEL_LABEL.get(comp_level, comp_level.upper() if comp_level else "MATCH")
    if comp_level == "qm":
        return f"{label} {match_number}"
    return f"{label} {set_number}-{match_number}"


def _youtube_video_id(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if YOUTUBE_VIDEO_ID_RE.fullmatch(raw):
        return raw
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        if "youtu.be" in host:
            candidate = parsed.path.strip("/")
            return candidate if YOUTUBE_VIDEO_ID_RE.fullmatch(candidate) else None
        if "youtube.com" in host:
            query_id = parse_qs(parsed.query).get("v", [None])[0]
            if isinstance(query_id, str) and YOUTUBE_VIDEO_ID_RE.fullmatch(query_id):
                return query_id
            path_parts = [part for part in parsed.path.split("/") if part]
            if len(path_parts) >= 2 and path_parts[0] == "embed":
                candidate = path_parts[1]
                return candidate if YOUTUBE_VIDEO_ID_RE.fullmatch(candidate) else None
    return None


def _webcast_watch_url(webcast_type: str, channel: str) -> str | None:
    stream_type = webcast_type.strip().lower()
    stream_channel = channel.strip()
    if not stream_channel:
        return None
    if stream_type == "youtube":
        video_id = _youtube_video_id(stream_channel)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        if stream_channel.startswith("http://") or stream_channel.startswith("https://"):
            return stream_channel
        return f"https://www.youtube.com/watch?v={stream_channel}"
    if stream_type == "twitch":
        if stream_channel.startswith("http://") or stream_channel.startswith("https://"):
            return stream_channel
        return f"https://www.twitch.tv/{stream_channel}"
    if stream_type == "ustream":
        if stream_channel.startswith("http://") or stream_channel.startswith("https://"):
            return stream_channel
        return f"https://www.ustream.tv/channel/{stream_channel}"
    if stream_type in {"iframe", "livestream"}:
        if stream_channel.startswith("http://") or stream_channel.startswith("https://"):
            return stream_channel
        return None
    if stream_channel.startswith("http://") or stream_channel.startswith("https://"):
        return stream_channel
    return None


def _webcast_embed_url(webcast_type: str, channel: str) -> str | None:
    stream_type = webcast_type.strip().lower()
    stream_channel = channel.strip()
    if not stream_channel:
        return None
    if stream_type == "youtube":
        video_id = _youtube_video_id(stream_channel)
        if video_id:
            return f"https://www.youtube.com/embed/{video_id}"
        return None
    if stream_type == "iframe":
        if stream_channel.startswith("http://") or stream_channel.startswith("https://"):
            return stream_channel
        return None
    return None


def _serialize_webcast_stream(row: dict) -> dict | None:
    stream_type = str(row.get("type") or "").strip().lower()
    channel = str(row.get("channel") or "").strip()
    if not stream_type and not channel:
        return None
    watch_url = _webcast_watch_url(stream_type, channel)
    embed_url = _webcast_embed_url(stream_type, channel)
    supports_embed = stream_type in SUPPORTED_EMBED_TYPES and bool(embed_url)
    return {
        "type": stream_type or "unknown",
        "channel": channel or None,
        "watch_url": watch_url,
        "embed_url": embed_url,
        "supports_embed": supports_embed,
    }


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(float(raw))
        except ValueError:
            return None
    return None


def _normalize_lookup_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _read_record_field_loose(record: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    targets = {_normalize_lookup_token(key) for key in keys if _normalize_lookup_token(key)}
    if not targets:
        return None
    for raw_key, raw_value in record.items():
        if _normalize_lookup_token(str(raw_key)) in targets:
            return raw_value
    return None


def _normalize_team_key(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    if not token:
        return None
    if token.startswith("frc"):
        suffix = token[3:]
        if suffix.isdigit():
            return f"frc{int(suffix)}"
        return None
    if token.isdigit():
        return f"frc{int(token)}"
    return None


def _alliance_team_slot_payload(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None

    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        team_number = _as_int(value)
        team_key = None if team_number is not None else _normalize_team_key(value)
        if team_number is None and team_key:
            team_number = _team_number_from_key(team_key)
        if team_number is None or team_number <= 0:
            return None
        payload: dict[str, Any] = {"teamNumber": int(team_number)}
        if team_key:
            payload["team_key"] = team_key
        return payload

    if not isinstance(value, dict):
        return None

    team_key = _normalize_team_key(
        _read_record_field_loose(value, ["team_key", "teamKey", "key", "team"]),
    )
    team_number = _as_int(
        _read_record_field_loose(
            value,
            ["teamNumber", "team_number", "number", "teamNumberRaw", "teamnum", "team"],
        )
    )
    if team_number is None and team_key:
        team_number = _team_number_from_key(team_key)
    if team_number is None or team_number <= 0:
        return None

    payload = {"teamNumber": int(team_number)}
    if team_key:
        payload["team_key"] = team_key
    raw_name = _read_record_field_loose(value, ["name", "teamName", "team_name", "nickname"])
    if isinstance(raw_name, str) and raw_name.strip():
        payload["name"] = raw_name.strip()
    return payload


def _normalized_alliance_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    raw_number = _read_record_field_loose(row, ["number", "allianceNumber", "alliance_number", "seed"])
    number = _as_int(raw_number) or index
    raw_name = _read_record_field_loose(row, ["name", "allianceName", "alliance_name"])
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else f"Alliance {number}"

    normalized: dict[str, Any] = {
        "number": int(number),
        "name": name,
    }
    slot_fields = {
        "captain": ["captain", "Captain", "captainTeam", "captain_team", "captainTeamNumber", "captain_team_number"],
        "round1": ["round1", "Round1", "round_1", "pick1", "firstPick", "first_pick", "pick1Team", "pick_1_team"],
        "round2": ["round2", "Round2", "round_2", "pick2", "secondPick", "second_pick", "pick2Team", "pick_2_team"],
        "round3": ["round3", "Round3", "round_3", "pick3", "thirdPick", "third_pick", "pick3Team", "pick_3_team"],
    }
    for slot_key, candidate_keys in slot_fields.items():
        slot_payload = _alliance_team_slot_payload(_read_record_field_loose(row, candidate_keys))
        if slot_payload:
            normalized[slot_key] = slot_payload

    picks_raw = _read_record_field_loose(row, ["picks", "Picks", "teamPicks", "team_picks", "teams"])
    picks = picks_raw if isinstance(picks_raw, list) else []
    slot_sequence = ["captain", "round1", "round2", "round3"]
    for idx, slot_key in enumerate(slot_sequence):
        if slot_key in normalized:
            continue
        if idx >= len(picks):
            continue
        slot_payload = _alliance_team_slot_payload(picks[idx])
        if slot_payload:
            normalized[slot_key] = slot_payload

    backup_value = _read_record_field_loose(row, ["backup", "Backup", "alternate", "Alternate", "backupTeam", "backup_team"])
    if isinstance(backup_value, dict):
        backup_value = (
            _read_record_field_loose(backup_value, ["in", "team", "team_key", "teamKey", "teamNumber"])
            or backup_value
        )
    backup_payload = _alliance_team_slot_payload(backup_value)
    if backup_payload:
        used_numbers = {
            int(slot["teamNumber"])
            for key, slot in normalized.items()
            if key in {"captain", "round1", "round2", "round3"} and isinstance(slot, dict) and isinstance(slot.get("teamNumber"), int)
        }
        if int(backup_payload["teamNumber"]) not in used_numbers:
            normalized["backup"] = backup_payload

    return normalized


def _match_start_unix(match_payload: dict[str, Any]) -> int | None:
    for key in ("actual_time", "predicted_time", "time"):
        parsed = _as_int(match_payload.get(key))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _match_alliance_team_keys(match_payload: dict[str, Any], alliance: str) -> list[str]:
    alliances = match_payload.get("alliances")
    if not isinstance(alliances, dict):
        return []
    payload = alliances.get(alliance)
    if not isinstance(payload, dict):
        return []
    values = payload.get("team_keys")
    if not isinstance(values, list):
        return []
    result = []
    for item in values:
        if isinstance(item, str) and item.strip():
            result.append(item.strip().lower())
    return result


def _match_score(match_payload: dict[str, Any], alliance: str) -> int | None:
    alliances = match_payload.get("alliances")
    if not isinstance(alliances, dict):
        return None
    payload = alliances.get(alliance)
    if not isinstance(payload, dict):
        return None
    parsed = _as_int(payload.get("score"))
    if parsed is None:
        return None
    # TBA uses -1 for unresolved live scores; treat as absent to avoid false "real" score rows.
    if parsed < 0:
        return None
    return parsed


def _parse_webcast_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    normalized = raw
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _select_preferred_webcast_stream(
    stream_rows: list[tuple[dict[str, Any], datetime | None]],
) -> dict[str, Any] | None:
    if not stream_rows:
        return None

    ordered = sorted(
        stream_rows,
        key=lambda item: item[1].timestamp() if isinstance(item[1], datetime) else float("-inf"),
        reverse=True,
    )

    for stream, _webcast_date in ordered:
        if bool(stream.get("supports_embed")):
            return stream
    for stream, _webcast_date in ordered:
        if bool(stream.get("watch_url")):
            return stream

    fallback = next((stream for stream, _webcast_date in stream_rows if bool(stream.get("supports_embed"))), None)
    if fallback is not None:
        return fallback
    return stream_rows[0][0]


def _winner_alliance(match_payload: dict[str, Any]) -> str | None:
    winner_raw = match_payload.get("winning_alliance")
    winner = winner_raw.strip().lower() if isinstance(winner_raw, str) else ""
    if winner in {"red", "blue"}:
        return winner

    red_score = _match_score(match_payload, "red")
    blue_score = _match_score(match_payload, "blue")
    if (
        isinstance(red_score, int)
        and isinstance(blue_score, int)
        and red_score >= 0
        and blue_score >= 0
    ):
        if red_score == blue_score:
            return "tie"
        return "red" if red_score > blue_score else "blue"
    return None


def _match_result_snapshot(match_payload: dict[str, Any]) -> dict[str, Any]:
    red_score = _match_score(match_payload, "red")
    blue_score = _match_score(match_payload, "blue")
    winner = _winner_alliance(match_payload)

    winning_score: int | None = None
    losing_score: int | None = None
    if (
        winner in {"red", "blue"}
        and isinstance(red_score, int)
        and isinstance(blue_score, int)
        and red_score >= 0
        and blue_score >= 0
    ):
        if winner == "red":
            winning_score = red_score
            losing_score = blue_score
        else:
            winning_score = blue_score
            losing_score = red_score

    return {
        "red_score": red_score,
        "blue_score": blue_score,
        "winner_alliance": winner,
        "is_completed": _is_match_completed(match_payload),
        "winning_score": winning_score,
        "losing_score": losing_score,
    }


def _is_match_completed(match_payload: dict[str, Any]) -> bool:
    winner = _winner_alliance(match_payload)
    if winner in {"red", "blue"}:
        return True
    red_score = _match_score(match_payload, "red")
    blue_score = _match_score(match_payload, "blue")
    return (
        isinstance(red_score, int)
        and isinstance(blue_score, int)
        and red_score >= 0
        and blue_score >= 0
    )


def _team_result_for_match(match_payload: dict[str, Any], team_key: str) -> str | None:
    normalized_team_key = team_key.strip().lower()
    red_teams = _match_alliance_team_keys(match_payload, "red")
    blue_teams = _match_alliance_team_keys(match_payload, "blue")
    team_alliance = "red" if normalized_team_key in red_teams else ("blue" if normalized_team_key in blue_teams else "")
    if not team_alliance:
        return None

    winner_raw = match_payload.get("winning_alliance")
    winner = winner_raw.strip().lower() if isinstance(winner_raw, str) else ""
    if winner in {"red", "blue"}:
        return "W" if winner == team_alliance else "L"

    red_score = _match_score(match_payload, "red")
    blue_score = _match_score(match_payload, "blue")
    if (
        isinstance(red_score, int)
        and isinstance(blue_score, int)
        and red_score >= 0
        and blue_score >= 0
    ):
        if red_score == blue_score:
            return "T"
        if team_alliance == "red":
            return "W" if red_score > blue_score else "L"
        return "W" if blue_score > red_score else "L"
    return None


def _form_label(results: list[str]) -> str:
    if not results:
        return "insufficient_data"
    points = 0.0
    for item in results:
        if item == "W":
            points += 1.0
        elif item == "T":
            points += 0.5
    ratio = points / float(len(results))
    if ratio >= 0.66:
        return "in_form"
    if ratio <= 0.34:
        return "cold"
    return "mixed"


def _split_event_key(event_key: str) -> tuple[int, str]:
    normalized = event_key.strip().lower()
    match = re.fullmatch(r"(\d{4})([a-z0-9]{3,})", normalized)
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Event key must look like 2026casj (4-digit season + event code).",
        )
    return int(match.group(1)), match.group(2).upper()


def _first_start_time_to_unix(start_time: str | None) -> int | None:
    if not start_time:
        return None
    try:
        dt = datetime.fromisoformat(start_time)
    except ValueError:
        return None

    # FIRST schedule times are local-to-venue without timezone. We keep deterministic
    # ordering by treating naive values as UTC instead of server-local time.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _parse_station_index(station: str | None) -> int | None:
    if not station:
        return None
    m = re.search(r"(\d+)$", station)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _first_level_to_comp(
    tournament_level: str | None,
    description: str | None,
    fallback_match_number: int,
    series_number_hint: int | None = None,
) -> tuple[str, int, int]:
    level = (tournament_level or "").strip().lower()
    description_value = (description or "").strip()
    desc_lower = description_value.lower()
    series_number = max(1, int(series_number_hint)) if isinstance(series_number_hint, int) and series_number_hint > 0 else 1

    if level == "practice":
        return "qm", 0, max(1, fallback_match_number)
    if level == "qualification":
        return "qm", series_number, max(1, fallback_match_number)

    trailing_match = fallback_match_number
    trailing_match_re = re.search(r"(\d+)$", description_value)
    if trailing_match_re:
        try:
            trailing_match = int(trailing_match_re.group(1))
        except ValueError:
            trailing_match = fallback_match_number

    if "quarterfinal" in desc_lower:
        set_number = ((max(1, trailing_match) - 1) // 2) + 1
        match_number = ((max(1, trailing_match) - 1) % 2) + 1
        return "qf", set_number, match_number
    if "semifinal" in desc_lower:
        set_number = ((max(1, trailing_match) - 1) // 2) + 1
        match_number = ((max(1, trailing_match) - 1) % 2) + 1
        return "sf", set_number, match_number
    if desc_lower.startswith("final"):
        return "f", max(1, series_number), max(1, trailing_match)

    if level == "playoff":
        # FIRST can return generic "Playoff X" descriptions that don't include round names.
        # Keep these in knockout buckets instead of misclassifying as qualification.
        return "playoff", max(1, series_number), max(1, trailing_match)

    # Unknown playoff description, preserve ordering with qualification style.
    return "qm", 1, max(1, fallback_match_number)


def _canonical_match_key(
    event_key: str,
    comp_level: str,
    set_number: int,
    match_number: int,
    seen: set[str],
    fallback_index: int,
) -> str:
    if comp_level == "qm":
        base_key = f"{event_key}_qm{match_number}"
    else:
        base_key = f"{event_key}_{comp_level}{set_number}m{match_number}"
    if base_key not in seen:
        seen.add(base_key)
        return base_key

    deduped = f"{base_key}_x{fallback_index}"
    seen.add(deduped)
    return deduped


def _upsert_event_if_missing(
    db: Session,
    event_key: str,
    tba: TBAClient | None = None,
) -> models.Event:
    event = db.get(models.Event, event_key)
    if event is not None:
        return event

    if tba is not None:
        try:
            payload = tba.event(event_key)
            db.merge(
                models.Event(
                    event_key=payload.get("key", event_key),
                    name=payload.get("name") or event_key,
                    year=int(payload.get("year") or 0),
                )
            )
            db.merge(
                models.EventProfile(
                    event_key=payload.get("key", event_key),
                    city=payload.get("city"),
                    state_prov=payload.get("state_prov"),
                    country=payload.get("country"),
                )
            )
            db.commit()
            event = db.get(models.Event, event_key)
            if event is not None:
                return event
        except (TBAClientError, RuntimeError, ValueError, TypeError):
            db.rollback()

    season, _ = _split_event_key(event_key)
    db.merge(
        models.Event(
            event_key=event_key,
            name=event_key.upper(),
            year=season,
        )
    )
    db.merge(
        models.EventProfile(
            event_key=event_key,
            city=None,
            state_prov=None,
            country=None,
        )
    )
    db.commit()
    event = db.get(models.Event, event_key)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_key} not found")
    return event


def _refresh_event_schedule_from_tba(db: Session, tba: TBAClient, event_key: str) -> int:
    try:
        payload = tba.event_matches(event_key)
    except (TBAClientError, RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to refresh schedule from TBA for {event_key}",
        ) from exc

    if not isinstance(payload, list):
        raise HTTPException(status_code=502, detail="Unexpected schedule response from TBA")

    team_keys_to_upsert: set[str] = set()
    match_team_links: list[tuple[str, str, str, int]] = []

    for match in payload:
        match_key = match.get("key")
        if not isinstance(match_key, str) or not match_key:
            continue

        scheduled_time = match.get("time")
        if scheduled_time is None:
            scheduled_time = match.get("predicted_time")

        db.merge(
            models.Match(
                match_key=match_key,
                event_key=event_key,
                comp_level=match.get("comp_level") or "qm",
                set_number=int(match.get("set_number") or 1),
                match_number=int(match.get("match_number") or 1),
                time=int(scheduled_time) if isinstance(scheduled_time, (int, float)) else None,
            )
        )

        for alliance in ("red", "blue"):
            alliance_payload = (match.get("alliances") or {}).get(alliance) or {}
            team_keys = alliance_payload.get("team_keys") or []
            for idx, team_key in enumerate(team_keys, start=1):
                if not isinstance(team_key, str) or not team_key:
                    continue
                team_keys_to_upsert.add(team_key)
                match_team_links.append((match_key, team_key, alliance, idx))

    nickname_overrides = _event_team_nickname_overrides(tba, event_key, team_keys_to_upsert)

    for team_key in sorted(team_keys_to_upsert):
        nickname = nickname_overrides.get(team_key)
        existing_team = db.get(models.Team, team_key)
        if existing_team is None:
            db.add(
                models.Team(
                    team_key=team_key,
                    team_number=_team_number_from_key(team_key),
                    nickname=nickname,
                )
            )
            continue
        if (
            nickname
            and (not isinstance(existing_team.nickname, str) or not existing_team.nickname.strip())
        ):
            existing_team.nickname = nickname
    db.flush()

    for match_key, team_key, alliance, idx in match_team_links:
        db.merge(
            models.MatchTeam(
                match_key=match_key,
                team_key=team_key,
                event_key=event_key,
                alliance=alliance,
                station=f"{alliance[0]}{idx}",
            )
        )

    db.commit()
    return len(payload)


def _refresh_event_schedule_from_first(
    db: Session,
    first_api: FIRSTEventsClient,
    event_key: str,
    *,
    season: int,
    event_code: str,
    tournament_level: str | None = None,
    team_number: int | None = None,
    start: int | None = None,
    end: int | None = None,
    if_modified_since: str | None = None,
    only_modified_since: str | None = None,
) -> tuple[int, str | None, bool]:
    requested_level = str(tournament_level or "").strip()
    normalized_level = requested_level.lower()
    effective_tournament_level = requested_level if requested_level and normalized_level != "none" else None
    # FIRST API now rejects requests that provide neither teamNumber nor tournamentLevel.
    # When callers do an unfiltered schedule refresh, fetch both major phases.
    schedule_levels_to_fetch: list[str | None]
    if effective_tournament_level is None and team_number is None:
        schedule_levels_to_fetch = ["Qualification", "Playoff"]
    else:
        schedule_levels_to_fetch = [effective_tournament_level]

    try:
        combined_schedule: list[dict[str, Any]] = []
        last_modified: str | None = None
        all_not_modified = True

        for level in schedule_levels_to_fetch:
            payload, level_last_modified, not_modified = first_api.event_schedule(
                season,
                event_code,
                tournament_level=level,
                team_number=team_number,
                start=start,
                end=end,
                if_modified_since=if_modified_since,
                only_modified_since=only_modified_since,
            )
            if level_last_modified:
                last_modified = level_last_modified

            if not_modified:
                continue

            all_not_modified = False
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected FIRST API schedule response format.")
            schedule_rows = payload.get("Schedule")
            if not isinstance(schedule_rows, list):
                raise RuntimeError("Unexpected FIRST API schedule payload.")
            for row in schedule_rows:
                if isinstance(row, dict):
                    combined_schedule.append(row)
    except (RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to refresh schedule from FIRST API for {event_key}",
        ) from exc

    if all_not_modified:
        return 0, last_modified, True

    schedule = combined_schedule

    team_keys_to_upsert: set[str] = set()
    event_team_links: set[str] = set()
    match_team_links: list[tuple[str, str, str, str | None]] = []
    seen_match_keys: set[str] = set()

    for idx, row in enumerate(schedule, start=1):
        if not isinstance(row, dict):
            continue
        fallback_match_number_raw = row.get("matchNumber")
        fallback_match_number = int(fallback_match_number_raw) if isinstance(fallback_match_number_raw, int) else idx

        comp_level, set_number, match_number = _first_level_to_comp(
            row.get("tournamentLevel"),
            row.get("description"),
            fallback_match_number,
            _as_int(row.get("series")),
        )
        match_key = _canonical_match_key(
            event_key,
            comp_level,
            set_number,
            match_number,
            seen_match_keys,
            idx,
        )
        scheduled_time = _first_start_time_to_unix(row.get("startTime"))
        db.merge(
            models.Match(
                match_key=match_key,
                event_key=event_key,
                comp_level=comp_level,
                set_number=set_number,
                match_number=match_number,
                time=scheduled_time,
            )
        )

        teams = row.get("teams")
        if not isinstance(teams, list):
            continue

        fallback_station_index = {"red": 1, "blue": 1}
        for team in teams:
            if not isinstance(team, dict):
                continue
            team_number_raw = team.get("teamNumber")
            if not isinstance(team_number_raw, int) or team_number_raw <= 0:
                continue
            team_key = f"frc{team_number_raw}"
            station = team.get("station") if isinstance(team.get("station"), str) else None

            station_lower = (station or "").lower()
            if station_lower.startswith("red"):
                alliance = "red"
            elif station_lower.startswith("blue"):
                alliance = "blue"
            else:
                # FIRST schedule always includes station, but keep a fallback.
                alliance = "red" if fallback_station_index["red"] <= 3 else "blue"

            station_idx = _parse_station_index(station)
            if station_idx is None:
                station_idx = fallback_station_index[alliance]
                fallback_station_index[alliance] += 1
            station_value = station or f"{alliance[0]}{station_idx}"

            team_keys_to_upsert.add(team_key)
            event_team_links.add(team_key)
            match_team_links.append((match_key, team_key, alliance, station_value))

    for team_key in sorted(team_keys_to_upsert):
        if db.get(models.Team, team_key) is None:
            db.add(
                models.Team(
                    team_key=team_key,
                    team_number=_team_number_from_key(team_key),
                    nickname=None,
                )
            )
    db.flush()

    for team_key in event_team_links:
        db.merge(models.EventTeam(event_key=event_key, team_key=team_key))

    for match_key, team_key, alliance, station in match_team_links:
        db.merge(
            models.MatchTeam(
                match_key=match_key,
                team_key=team_key,
                event_key=event_key,
                alliance=alliance,
                station=station,
            )
        )

    db.commit()
    return len(schedule), last_modified, False


@router.get("/{match_key}")
def get_match(match_key: str, db: Session = Depends(get_db)):
    match = db.get(models.Match, match_key)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    videos = (
        db.query(models.MatchVideo)
        .filter(models.MatchVideo.match_key == match_key)
        .all()
    )
    teams = (
        db.query(models.MatchTeam)
        .filter(models.MatchTeam.match_key == match_key)
        .order_by(models.MatchTeam.alliance, models.MatchTeam.station)
        .all()
    )

    return {
        "match_key": match.match_key,
        "event_key": match.event_key,
        "comp_level": match.comp_level,
        "set_number": match.set_number,
        "match_number": match.match_number,
        "time": match.time,
        "videos": [{"type": v.video_type, "key": v.video_key, "url": v.url} for v in videos],
        "teams": [
            {
                "team_key": t.team_key,
                "alliance": t.alliance,
                "station": t.station,
            }
            for t in teams
        ],
    }


@router.get("/{match_key}/phases")
def get_match_phases(
    match_key: str,
    request: Request,
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    match = db.get(models.Match, match_key)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    if settings.public_readonly_mode:
        if refresh:
            require_admin_access(request, "Phase window refresh")
            require_write_access("Phase window refresh")
        phase_data = compute_phase_windows_payload(match.time)
    else:
        if refresh:
            require_admin_access(request, "Phase window refresh")
        phase_data = get_or_compute_match_phase_windows(db, match, refresh=refresh)
    return {
        "ok": True,
        "match_key": match.match_key,
        "event_key": match.event_key,
        "time": match.time,
        **phase_data,
    }


@router.get("/{match_key}/zebra-motionworks")
def get_match_zebra_motionworks(match_key: str):
    tba = _get_tba_client_or_503()
    try:
        payload = tba.match_zebra_motionworks(match_key)
    except (TBAClientError, RuntimeError, ValueError, TypeError) as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code == 404:
            return {
                "ok": True,
                "match_key": match_key,
                "source": "tba",
                "available": False,
                "zebra": None,
            }
        raise HTTPException(
            status_code=502,
            detail=f"Unable to fetch Zebra MotionWorks data from TBA for {match_key}",
        ) from exc

    return {
        "ok": True,
        "match_key": match_key,
        "source": "tba",
        "available": payload is not None and (not isinstance(payload, dict) or len(payload) > 0),
        "zebra": payload,
    }


@router.get("/event/{event_key}")
def list_matches_for_event(
    event_key: str,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    total_count = (
        db.query(func.count(models.Match.match_key))
        .filter(models.Match.event_key == event_key)
        .scalar()
        or 0
    )
    matches = (
        db.query(
            models.Match.match_key,
            models.Match.comp_level,
            models.Match.set_number,
            models.Match.match_number,
            models.Match.time,
        )
        .filter(models.Match.event_key == event_key)
        .order_by(models.Match.comp_level, models.Match.set_number, models.Match.match_number)
    )
    if offset > 0:
        matches = matches.offset(offset)
    if limit is not None:
        matches = matches.limit(limit)
    rows = matches.all()

    return {
        "event_key": event_key,
        "count": len(rows),
        "total_count": int(total_count),
        "limit": int(limit) if isinstance(limit, int) else None,
        "offset": int(offset),
        "matches": [
            {
                "match_key": match_key,
                "comp_level": comp_level,
                "set_number": set_number,
                "match_number": match_number,
                "time": match_time,
            }
            for match_key, comp_level, set_number, match_number, match_time in rows
        ],
    }


@router.get("/event/{event_key}/schedule", response_model=ScheduleResponse)
def get_event_schedule(
    event_key: str,
    request: Request,
    response: Response,
    refresh: bool = Query(default=False),
    include_live_results: bool = Query(default=True, alias="includeLiveResults"),
    include_teams: bool = Query(default=True, alias="includeTeams"),
    include_team_nicknames: bool = Query(default=True, alias="includeTeamNicknames"),
    source: str = Query(default="auto", pattern="^(auto|tba|first)$"),
    tournament_level: str | None = Query(default=None, alias="tournamentLevel"),
    team_number: int | None = Query(default=None, alias="teamNumber"),
    start: int | None = Query(default=None),
    end: int | None = Query(default=None),
    if_modified_since: str | None = Query(default=None, alias="ifModifiedSince"),
    only_modified_since: str | None = Query(default=None, alias="onlyModifiedSince"),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    if tournament_level and tournament_level not in FIRST_TOURNAMENT_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid tournamentLevel. Expected one of: "
                "None, Practice, Qualification, Playoff."
            ),
        )
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=400, detail="start must be less than or equal to end.")
    if if_modified_since and only_modified_since:
        raise HTTPException(
            status_code=400,
            detail="Specify only one of ifModifiedSince or onlyModifiedSince.",
        )

    season, event_code = _split_event_key(event_key)
    tba = TBAClient() if settings.tba_auth_key.strip() else None
    first_api = (
        FIRSTEventsClient()
        if settings.first_frc_api_username.strip() and settings.first_frc_api_auth_key.strip()
        else None
    )
    if settings.public_readonly_mode:
        event = db.get(models.Event, event_key)
        if event is None:
            raise HTTPException(
                status_code=404,
                detail=f"Event {event_key} is not available in local public data cache.",
            )
    else:
        event = _upsert_event_if_missing(db, event_key, tba=tba)

    source_used = "local"
    first_last_modified: str | None = None
    local_match_count = (
        db.query(func.count(models.Match.match_key))
        .filter(models.Match.event_key == event_key)
        .scalar()
        or 0
    )
    local_knockout_count = (
        db.query(func.count(models.Match.match_key))
        .filter(
            models.Match.event_key == event_key,
            func.lower(models.Match.comp_level).in_(tuple(KNOCKOUT_COMP_LEVELS)),
        )
        .scalar()
        or 0
    )
    if refresh and settings.public_readonly_mode:
        require_admin_access(request, "Schedule refresh")
        require_write_access("Schedule refresh")
    if refresh and not settings.public_readonly_mode:
        require_admin_access(request, "Schedule refresh")

    should_attempt_knockout_topup = (
        not refresh
        and not settings.public_readonly_mode
        and local_match_count > 0
        and local_knockout_count == 0
        and tournament_level is None
        and team_number is None
        and start is None
        and end is None
        and if_modified_since is None
        and only_modified_since is None
    )
    if should_attempt_knockout_topup:
        cache_key = str(event_key or "").strip().lower()
        topup_lock_key = f"{_SCHEDULE_KNOCKOUT_TOPUP_LOCK_PREFIX}{cache_key}"
        topup_cache = get_cache()
        if topup_cache.get(topup_lock_key) is None:
            topup_cache.set(topup_lock_key, "1", ttl=SCHEDULE_KNOCKOUT_TOPUP_MIN_INTERVAL_SEC)
            provider_order: list[str]
            if source == "tba":
                provider_order = ["tba"]
            elif source == "first":
                provider_order = ["first"]
            else:
                provider_order = ["tba", "first"]
            for provider in provider_order:
                if provider == "tba" and tba is not None:
                    try:
                        _refresh_event_schedule_from_tba(db, tba, event_key)
                        source_used = "tba_topup"
                        break
                    except HTTPException:
                        pass
                if provider == "first" and first_api is not None:
                    try:
                        _, first_last_modified, _not_modified = _refresh_event_schedule_from_first(
                            db,
                            first_api,
                            event_key,
                            season=season,
                            event_code=event_code,
                            tournament_level="Playoff",
                        )
                        source_used = "first_topup"
                        break
                    except HTTPException:
                        pass
            local_match_count = (
                db.query(func.count(models.Match.match_key))
                .filter(models.Match.event_key == event_key)
                .scalar()
                or 0
            )

    if refresh or (local_match_count == 0 and not settings.public_readonly_mode):
        refreshed = False
        tba_error: str | None = None
        first_error: str | None = None

        prefer_first_in_auto = bool(
            tournament_level
            or team_number is not None
            or start is not None
            or end is not None
            or if_modified_since
            or only_modified_since
        )
        provider_order: list[str]
        if source == "tba":
            provider_order = ["tba"]
        elif source == "first":
            provider_order = ["first"]
        elif prefer_first_in_auto:
            provider_order = ["first", "tba"]
        else:
            provider_order = ["tba", "first"]

        for provider in provider_order:
            if refreshed:
                break
            if provider == "tba":
                if tba is None:
                    continue
                try:
                    _refresh_event_schedule_from_tba(db, tba, event_key)
                    source_used = "tba_refreshed"
                    refreshed = True
                except HTTPException as exc:
                    tba_error = str(exc.detail)
                    if source == "tba":
                        raise
            elif provider == "first":
                if first_api is None:
                    continue
                try:
                    _, first_last_modified, not_modified = _refresh_event_schedule_from_first(
                        db,
                        first_api,
                        event_key,
                        season=season,
                        event_code=event_code,
                        tournament_level=tournament_level,
                        team_number=team_number,
                        start=start,
                        end=end,
                        if_modified_since=if_modified_since,
                        only_modified_since=only_modified_since,
                    )
                    source_used = "first_not_modified" if not_modified else "first_refreshed"
                    refreshed = True
                except HTTPException as exc:
                    first_error = str(exc.detail)
                    if source == "first":
                        raise

        if not refreshed:
            if source == "tba" and tba is None:
                raise HTTPException(
                    status_code=503,
                    detail="TBA schedule refresh requested, but TBA_AUTH_KEY is not configured.",
                )
            if source == "first" and first_api is None:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "FIRST schedule refresh requested, but FIRST_FRC_API_USERNAME and "
                        "FIRST_FRC_API_AUTH_KEY are not configured."
                    ),
                )
            if source == "auto" and local_match_count == 0:
                detail_bits = [
                    "Unable to refresh schedule from remote sources.",
                    f"TBA: {tba_error}" if tba_error else "TBA: unavailable",
                    f"FIRST: {first_error}" if first_error else "FIRST: unavailable",
                ]
                raise HTTPException(status_code=502, detail=" ".join(detail_bits))

    match_rows = (
        db.query(
            models.Match.match_key,
            models.Match.comp_level,
            models.Match.set_number,
            models.Match.match_number,
            models.Match.time,
        )
        .filter(models.Match.event_key == event_key)
        .all()
    )
    match_rows.sort(
        key=lambda row: (
            COMP_LEVEL_ORDER.get(row[1], 99),
            row[2],
            row[3],
        )
    )

    teams_by_match: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"red": [], "blue": []})
    if include_teams:
        if include_team_nicknames:
            team_rows = (
                db.query(
                    models.MatchTeam.match_key,
                    models.MatchTeam.team_key,
                    models.MatchTeam.alliance,
                    models.MatchTeam.station,
                    models.Team.team_number,
                    models.Team.nickname,
                )
                .outerjoin(models.Team, models.Team.team_key == models.MatchTeam.team_key)
                .filter(models.MatchTeam.event_key == event_key)
                .all()
            )
            missing_team_keys: set[str] = set()
            for _match_key, team_key, _alliance_raw, _station, _team_number_raw, nickname in team_rows:
                team_key_value = str(team_key or "").strip().lower()
                has_nickname = isinstance(nickname, str) and bool(nickname.strip())
                if team_key_value and not has_nickname:
                    missing_team_keys.add(team_key_value)
            nickname_overrides = (
                _event_team_nickname_overrides(tba, event_key, missing_team_keys)
                if tba is not None and missing_team_keys
                else {}
            )
            for match_key, team_key, alliance_raw, station, team_number_raw, nickname in team_rows:
                alliance = str(alliance_raw or "").lower()
                if alliance not in {"red", "blue"}:
                    continue
                team_key_value = str(team_key or "")
                if not team_key_value:
                    continue
                normalized_team_key = team_key_value.strip().lower()
                resolved_nickname = (
                    str(nickname).strip()
                    if isinstance(nickname, str) and nickname.strip()
                    else nickname_overrides.get(normalized_team_key)
                )
                team_number = (
                    int(team_number_raw)
                    if isinstance(team_number_raw, int) and team_number_raw > 0
                    else _team_number_from_key(team_key_value)
                )
                teams_by_match[str(match_key)][alliance].append(
                    {
                        "team_key": team_key_value,
                        "team_number": team_number,
                        "nickname": resolved_nickname,
                        "station": station,
                    }
                )
        else:
            team_rows = (
                db.query(
                    models.MatchTeam.match_key,
                    models.MatchTeam.team_key,
                    models.MatchTeam.alliance,
                    models.MatchTeam.station,
                )
                .filter(models.MatchTeam.event_key == event_key)
                .all()
            )
            for match_key, team_key, alliance_raw, station in team_rows:
                alliance = str(alliance_raw or "").lower()
                if alliance not in {"red", "blue"}:
                    continue
                team_key_value = str(team_key or "")
                if not team_key_value:
                    continue
                teams_by_match[str(match_key)][alliance].append(
                    {
                        "team_key": team_key_value,
                        "team_number": _team_number_from_key(team_key_value),
                        "nickname": None,
                        "station": station,
                    }
                )

    result_by_match_key: dict[str, dict[str, Any]] = {}
    if include_live_results and tba is not None:
        result_by_match_key = _cached_live_results_for_event(tba, event_key)

    schedule_rows = []
    for match_key, comp_level, set_number, match_number, scheduled_time in match_rows:
        normalized_set_number = int(set_number) if isinstance(set_number, int) else 0
        normalized_match_number = int(match_number) if isinstance(match_number, int) else 0
        grouped = teams_by_match.get(str(match_key), {"red": [], "blue": []})
        red = sorted(grouped["red"], key=lambda item: _station_sort_key(item.get("station")))
        blue = sorted(grouped["blue"], key=lambda item: _station_sort_key(item.get("station")))
        result_payload = result_by_match_key.get(str(match_key).lower(), {})
        schedule_rows.append(
            {
                "match_key": match_key,
                "display_name": _display_match_name(str(comp_level), normalized_set_number, normalized_match_number),
                "comp_level": comp_level,
                "set_number": normalized_set_number,
                "match_number": normalized_match_number,
                "scheduled_time": scheduled_time,
                "has_time": scheduled_time is not None,
                "red_score": result_payload.get("red_score"),
                "blue_score": result_payload.get("blue_score"),
                "winner_alliance": result_payload.get("winner_alliance"),
                "is_completed": bool(result_payload.get("is_completed")),
                "winning_score": result_payload.get("winning_score"),
                "losing_score": result_payload.get("losing_score"),
                "red": red,
                "blue": blue,
            }
        )

    total_count = len(schedule_rows)
    if offset > 0 or limit is not None:
        start_idx = max(0, int(offset))
        if limit is None:
            schedule_rows = schedule_rows[start_idx:]
        else:
            schedule_rows = schedule_rows[start_idx : start_idx + int(limit)]

    if refresh:
        response.headers["Cache-Control"] = "no-store"
    elif include_live_results:
        live_ttl_sec = max(1, int(getattr(settings, "matches_live_results_cache_ttl_sec", 5) or 5))
        response.headers["Cache-Control"] = (
            f"public, max-age={live_ttl_sec}, stale-while-revalidate={max(15, live_ttl_sec * 3)}"
        )
    else:
        response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=180"

    return {
        "ok": True,
        "event_key": event_key,
        "event_name": event.name,
        "source": source_used,
        "first_last_modified": first_last_modified,
        "published": total_count > 0,
        "times_published": any(row["has_time"] for row in schedule_rows),
        "count": len(schedule_rows),
        "total_count": int(total_count),
        "limit": int(limit) if isinstance(limit, int) else None,
        "offset": int(offset),
        "matches": schedule_rows,
    }


@router.get("/event/{event_key}/team-live-form", response_model=TeamLiveFormResponse)
def get_event_team_live_form(
    event_key: str,
    form_window: int = Query(default=FORM_WINDOW_MATCHES, ge=1, le=10),
    live_window_sec: int = Query(default=LIVE_MATCH_WINDOW_SEC, ge=90, le=300),
    db: Session = Depends(get_db),
):
    if not settings.tba_auth_key.strip():
        return {
            "ok": True,
            "event_key": event_key,
            "source": "tba",
            "available": False,
            "detail": "TBA auth key is not configured.",
            "as_of_unix": int(datetime.now(timezone.utc).timestamp()),
            "form_window": int(form_window),
            "live_window_sec": int(live_window_sec),
            "team_statuses": {},
        }

    known_team_rows = (
        db.query(models.EventTeam.team_key)
        .filter(models.EventTeam.event_key == event_key)
        .all()
    )
    known_team_keys = sorted(
        {
            str(row.team_key).strip().lower()
            for row in known_team_rows
            if isinstance(row.team_key, str) and row.team_key.strip()
        }
    )

    try:
        payload = TBAClient().event_matches(event_key)
    except (TBAClientError, RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch TBA matches for {event_key}") from exc
    if not isinstance(payload, list):
        raise HTTPException(status_code=502, detail="Unexpected event matches response from TBA")

    now_unix = int(datetime.now(timezone.utc).timestamp())
    team_histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    team_statuses: dict[str, dict[str, Any]] = {
        team_key: {
            "team_key": team_key,
            "is_live": False,
            "live_match_key": None,
            "live_match_display": None,
            "live_match_start_unix": None,
            "recent_form": [],
            "recent_form_matches": [],
            "recent_form_winrate_0_1": None,
            "in_form_label": "insufficient_data",
            "completed_matches_considered": 0,
        }
        for team_key in known_team_keys
    }

    for match in payload:
        if not isinstance(match, dict):
            continue
        comp_level = str(match.get("comp_level") or "").lower()
        if comp_level == "ef":
            # Event finals/practice can contain irregular entries; keep data conservative.
            continue

        match_key_raw = match.get("key")
        match_key = str(match_key_raw).strip().lower() if isinstance(match_key_raw, str) else ""
        if not match_key:
            continue

        set_number = _as_int(match.get("set_number")) or 1
        match_number = _as_int(match.get("match_number")) or 1
        display_name = _display_match_name(comp_level, set_number, match_number)
        match_start = _match_start_unix(match)
        completed = _is_match_completed(match)

        teams_in_match = sorted(
            set(_match_alliance_team_keys(match, "red") + _match_alliance_team_keys(match, "blue"))
        )
        for team_key in teams_in_match:
            if team_key not in team_statuses:
                team_statuses[team_key] = {
                    "team_key": team_key,
                    "is_live": False,
                    "live_match_key": None,
                    "live_match_display": None,
                    "live_match_start_unix": None,
                    "recent_form": [],
                    "recent_form_matches": [],
                    "recent_form_winrate_0_1": None,
                    "in_form_label": "insufficient_data",
                    "completed_matches_considered": 0,
                }

            if (
                not completed
                and isinstance(match_start, int)
                and match_start <= now_unix <= (match_start + int(live_window_sec))
            ):
                row = team_statuses[team_key]
                existing_start = _as_int(row.get("live_match_start_unix"))
                if existing_start is None or match_start > existing_start:
                    row["is_live"] = True
                    row["live_match_key"] = match_key
                    row["live_match_display"] = display_name
                    row["live_match_start_unix"] = match_start

            if completed:
                result = _team_result_for_match(match, team_key)
                if result in {"W", "L", "T"}:
                    team_histories[team_key].append(
                        {
                            "match_key": match_key,
                            "match_display": display_name,
                            "time": match_start,
                            "result": result,
                        }
                    )

    for team_key, history in team_histories.items():
        history.sort(
            key=lambda row: (
                int(row.get("time")) if isinstance(row.get("time"), int) else -1,
                str(row.get("match_key") or ""),
            ),
            reverse=True,
        )
        window_rows = history[: int(form_window)]
        results = [str(row.get("result") or "") for row in window_rows if row.get("result") in {"W", "L", "T"}]
        points = 0.0
        for item in results:
            if item == "W":
                points += 1.0
            elif item == "T":
                points += 0.5
        winrate = (points / float(len(results))) if results else None
        row = team_statuses.setdefault(
            team_key,
            {
                "team_key": team_key,
                "is_live": False,
                "live_match_key": None,
                "live_match_display": None,
                "live_match_start_unix": None,
                "recent_form": [],
                "recent_form_matches": [],
                "recent_form_winrate_0_1": None,
                "in_form_label": "insufficient_data",
                "completed_matches_considered": 0,
            },
        )
        row["recent_form"] = results
        row["recent_form_matches"] = window_rows
        row["recent_form_winrate_0_1"] = round(float(winrate), 4) if isinstance(winrate, float) else None
        row["in_form_label"] = _form_label(results)
        row["completed_matches_considered"] = len(history)

    return {
        "ok": True,
        "event_key": event_key,
        "source": "tba",
        "available": True,
        "detail": None,
        "as_of_unix": now_unix,
        "form_window": int(form_window),
        "live_window_sec": int(live_window_sec),
        "team_statuses": dict(sorted(team_statuses.items(), key=lambda item: item[0])),
    }


@router.get("/event/{event_key}/live-stream")
def get_event_live_stream(event_key: str):
    game_day_url = f"https://www.thebluealliance.com/gameday#{event_key}"
    if not settings.tba_auth_key.strip():
        return {
            "ok": True,
            "event_key": event_key,
            "event_name": None,
            "source": "tba",
            "available": False,
            "preferred_stream": None,
            "streams": [],
            "game_day_url": game_day_url,
            "detail": "TBA auth key is not configured.",
        }

    tba = TBAClient()
    try:
        payload = tba.event(event_key)
    except (TBAClientError, RuntimeError, ValueError, TypeError) as exc:
        detail = f"Unable to load event webcast data from TBA for {event_key}"
        raise HTTPException(status_code=502, detail=detail) from exc

    raw_webcasts = payload.get("webcasts")
    stream_rows = raw_webcasts if isinstance(raw_webcasts, list) else []
    serialized_rows: list[tuple[dict[str, Any], datetime | None]] = []
    for row in stream_rows:
        if not isinstance(row, dict):
            continue
        serialized = _serialize_webcast_stream(row)
        if serialized is None:
            continue
        serialized_rows.append((serialized, _parse_webcast_date(row.get("date"))))

    streams = [stream for stream, _webcast_date in serialized_rows]
    preferred_stream = _select_preferred_webcast_stream(serialized_rows)

    return {
        "ok": True,
        "event_key": event_key,
        "event_name": payload.get("name"),
        "source": "tba",
        "available": bool(streams),
        "preferred_stream": preferred_stream,
        "streams": streams,
        "game_day_url": game_day_url,
        "detail": None if streams else "No webcast streams are published for this event yet.",
    }


@router.get("/event/{event_key}/alliances")
def get_event_alliances(
    event_key: str,
    if_modified_since: str | None = Query(default=None, alias="ifModifiedSince"),
    only_modified_since: str | None = Query(default=None, alias="onlyModifiedSince"),
):
    if if_modified_since and only_modified_since:
        raise HTTPException(
            status_code=400,
            detail="Specify only one of ifModifiedSince or onlyModifiedSince.",
        )

    season, event_code = _split_event_key(event_key)
    tba_error: str | None = None
    first_error: str | None = None

    # TBA provides alliance picks in a consistent shape for event breakdown cards.
    if settings.tba_auth_key.strip() and not if_modified_since and not only_modified_since:
        try:
            tba = TBAClient()
            payload = tba.event_alliances(event_key)
            if not isinstance(payload, list):
                raise RuntimeError("Unexpected TBA alliances payload.")
            alliances = [
                _normalized_alliance_row(row, idx)
                for idx, row in enumerate(payload, start=1)
                if isinstance(row, dict)
            ]
            if alliances or not (settings.first_frc_api_username.strip() and settings.first_frc_api_auth_key.strip()):
                return {
                    "ok": True,
                    "event_key": event_key,
                    "season": season,
                    "event_code": event_code,
                    "source": "tba",
                    "not_modified": False,
                    "last_modified": None,
                    "count": len(alliances),
                    "alliances": alliances,
                }
        except (TBAClientError, RuntimeError, ValueError, TypeError) as exc:
            tba_error = str(exc)

    if not settings.first_frc_api_username.strip() or not settings.first_frc_api_auth_key.strip():
        detail = "Alliance selection providers unavailable."
        if tba_error:
            detail = f"{detail} TBA error: {tba_error}"
        raise HTTPException(status_code=503, detail=detail)

    first_api = FIRSTEventsClient()
    try:
        payload, last_modified, not_modified = first_api.event_alliances(
            season,
            event_code,
            if_modified_since=if_modified_since,
            only_modified_since=only_modified_since,
        )
    except (RuntimeError, ValueError, TypeError) as exc:
        first_error = str(exc)
        detail = f"Unable to load alliance selections for {event_key}"
        if tba_error:
            detail = f"{detail}. TBA: {tba_error}"
        if first_error:
            detail = f"{detail}. FIRST: {first_error}"
        raise HTTPException(status_code=502, detail=detail) from exc

    alliances_raw = payload.get("Alliances")
    first_rows = alliances_raw if isinstance(alliances_raw, list) else []
    alliances = [
        _normalized_alliance_row(row, idx)
        for idx, row in enumerate(first_rows, start=1)
        if isinstance(row, dict)
    ]
    return {
        "ok": True,
        "event_key": event_key,
        "season": season,
        "event_code": event_code,
        "source": "first_api",
        "not_modified": not_modified,
        "last_modified": last_modified,
        "count": len(alliances),
        "alliances": alliances,
    }
