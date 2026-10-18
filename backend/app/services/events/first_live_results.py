from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.services.cache import get_cache
from app.services.events.first_events_client import FIRSTEventsClient

logger = logging.getLogger(__name__)

# The field posts a result ~30 s after the buzzer. FRC Events has it 6-14 s later, TBA
# 71-162 s later (measured live on 2026-10-18 across 4 events). So while TBA hasn't got a
# result yet, take it from FRC Events. TBA's own result always wins once it arrives.

# Only ask FRC Events while some match should be finishing: started up to this long ago
# (playoff breaks and slow result reviews included) and still without a result on TBA.
_WAITING_SINCE_SEC = 45 * 60
# Matches often run a few minutes ahead of TBA's predicted time.
_WAITING_AHEAD_SEC = 10 * 60
# A stored FRC answer younger than this is used as is (the live-results cache rebuilds every 5 s).
_FRESH_SEC = 4
# Fetches run in the background, so they can wait out FRC Events' slow moments.
_FETCH_TIMEOUT_SEC = 15
_EVENT_META_PREFIX = "first:event_meta:v1:"
_EVENT_META_TTL_SEC = 86400
_RESULTS_PREFIX = "first:live_results:v1:"
_RESULTS_TTL_SEC = 3600
_LEVEL_RANK = {"qm": 0, "ef": 1, "qf": 2, "sf": 3, "f": 4}

_client: FIRSTEventsClient | None = None


def _first_client() -> FIRSTEventsClient | None:
    # One client per process so its circuit breaker sees every failure.
    global _client
    if _client is None:
        try:
            _client = FIRSTEventsClient()
        except RuntimeError:
            return None
    return _client


def _tba_level(match: dict[str, Any]) -> str:
    return "Qualification" if str(match.get("comp_level") or "qm").lower() == "qm" else "Playoff"


def _start_time(match: dict[str, Any]) -> int | None:
    for field in ("actual_time", "predicted_time", "time"):
        value = match.get(field)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return None


def _levels_waiting(tba_matches: list[dict[str, Any]], results: dict[str, dict[str, Any]], now: int) -> list[str]:
    levels: set[str] = set()
    for match in tba_matches:
        key = str(match.get("key") or "").lower()
        if not key or (results.get(key) or {}).get("is_completed"):
            continue
        start = _start_time(match)
        if start is not None and now - _WAITING_SINCE_SEC <= start <= now + _WAITING_AHEAD_SEC:
            levels.add(_tba_level(match))
    return sorted(levels)


def _event_meta(tba: Any, event_key: str) -> dict[str, Any] | None:
    # FRC Events' code for this event and its timezone, from TBA's event record.
    cache = get_cache()
    redis_key = f"{_EVENT_META_PREFIX}{event_key}"
    raw = cache.get(redis_key)
    if raw:
        try:
            meta = json.loads(raw)
            return meta if meta.get("code") else None
        except (ValueError, TypeError, AttributeError):
            pass
    try:
        event = tba.event(event_key)
    except Exception:  # noqa: BLE001 - a TBA hiccup just means no FRC overlay this time
        return None
    if not isinstance(event, dict):
        return None
    code = str(event.get("first_event_code") or "").strip()
    meta = {"code": code or None, "timezone": event.get("timezone"), "year": event.get("year")}
    # Remember "no code" too, so an event FRC Events doesn't know isn't looked up every 5 s.
    cache.set(redis_key, json.dumps(meta), ttl=_EVENT_META_TTL_SEC)
    return meta if code else None


def _played_matches(season: int, code: str, level: str) -> list[dict[str, Any]]:
    # Answers from the last stored copy and refreshes it in the background: FRC Events is
    # usually 0.2 s but took over 4 s now and then on 2026-10-18, and a page must not wait.
    redis_key = f"{_RESULTS_PREFIX}{season}:{code.lower()}:{level}"
    stored = _load(redis_key)
    if time.time() - float(stored.get("checked_at") or 0) >= _FRESH_SEC:
        _refresh_in_background(season, code, level, redis_key)
        stored = _load(redis_key)
    return list(stored.get("matches") or [])


def _load(redis_key: str) -> dict[str, Any]:
    raw = get_cache().get(redis_key)
    if not raw:
        return {}
    try:
        stored = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _refresh_in_background(season: int, code: str, level: str, redis_key: str) -> None:
    cache = get_cache()
    # One fetch at a time per event and level, across both workers.
    client = getattr(cache, "redis_client", None)
    if client is not None:
        try:
            if not client.set(f"{redis_key}:refreshing", "1", nx=True, ex=_FETCH_TIMEOUT_SEC * 2):
                return
        except Exception:  # noqa: BLE001 - no lock, no fetch; the stored copy still serves
            return

    def _run() -> None:
        try:
            _fetch(season, code, level, redis_key)
        finally:
            cache.delete(f"{redis_key}:refreshing")

    _spawn(_run, f"first-live-{code.lower()}-{level.lower()}")


def _spawn(fn: Any, name: str) -> None:
    threading.Thread(target=fn, name=name, daemon=True).start()


def _fetch(season: int, code: str, level: str, redis_key: str) -> None:
    first = _first_client()
    if first is None:
        return
    stored = _load(redis_key)
    try:
        # If-Modified-Since makes the repeat checks a cheap 304.
        payload, last_modified, not_modified = first.event_matches(
            season,
            code,
            tournament_level=level,
            if_modified_since=stored.get("last_modified"),
            timeout=_FETCH_TIMEOUT_SEC,
            max_retries=1,
        )
    except (RuntimeError, ValueError, TypeError) as exc:
        # The client wraps network errors; the cause says whether it was a timeout or a reset.
        logger.info("first_live_results.fetch_failed code=%s level=%s: %s (%r)", code, level, exc, exc.__cause__)
        return
    matches = stored.get("matches") or [] if not_modified else [
        m for m in (payload.get("Matches") or []) if isinstance(m, dict)
    ]
    get_cache().set(
        redis_key,
        json.dumps({"last_modified": last_modified or stored.get("last_modified"), "checked_at": time.time(), "matches": matches}),
        ttl=_RESULTS_TTL_SEC,
    )


def _first_alliances(match: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]] | None:
    red: set[str] = set()
    blue: set[str] = set()
    for team in match.get("teams") or []:
        if not isinstance(team, dict):
            continue
        number = team.get("teamNumber")
        station = str(team.get("station") or "").lower()
        if not isinstance(number, int) or number <= 0:
            continue
        (red if station.startswith("red") else blue if station.startswith("blue") else set()).add(f"frc{number}")
    return (frozenset(red), frozenset(blue)) if red and blue else None


def _tba_alliances(match: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]] | None:
    alliances = match.get("alliances") or {}
    sides = tuple(
        frozenset(str(t).lower() for t in ((alliances.get(color) or {}).get("team_keys") or []))
        for color in ("red", "blue")
    )
    return sides if sides[0] and sides[1] else None


def _same_lineup(tba_match: dict[str, Any], first: dict[str, Any], level: str) -> bool:
    # Same alliances, allowing for what TBA can't know before the result: in playoffs an
    # alliance's fourth robot rotates in and robots change stations (2026catt SF8: TBA listed
    # 3925 until the result showed 599 had played), and off-season "B" teams are frc973B on
    # TBA but the field's placeholder number (9973) on FRC Events. So: B robots match anyone,
    # at least half the robots match outright with one on each side, at most two differ in
    # playoffs (none in qualifications, whose lineups are fixed), and no robot is on the
    # other colour.
    alliances = tba_match.get("alliances") or {}
    first_side: dict[str, set[int]] = {"red": set(), "blue": set()}
    for team in first.get("teams") or []:
        if not isinstance(team, dict) or not isinstance(team.get("teamNumber"), int):
            continue
        station = str(team.get("station") or "").lower()
        color = "red" if station.startswith("red") else "blue" if station.startswith("blue") else None
        if color:
            first_side[color].add(team["teamNumber"])
    tba_side: dict[str, set[int]] = {}
    for color in ("red", "blue"):
        keys = [str(k).lower() for k in ((alliances.get(color) or {}).get("team_keys") or [])]
        if not keys or not first_side[color]:
            return False
        tba_side[color] = {int(k[3:]) for k in keys if k[3:].isdigit()}
    if first_side["red"] & tba_side["blue"] or first_side["blue"] & tba_side["red"]:
        return False
    matched = {color: len(first_side[color] & tba_side[color]) for color in ("red", "blue")}
    differing = sum(len(tba_side[color] - first_side[color]) for color in ("red", "blue"))
    allowed = 2 if level == "Playoff" else 0
    return min(matched.values()) >= 1 and sum(matched.values()) >= 3 and differing <= allowed


def _tba_order(match: dict[str, Any]) -> tuple[int, int, int]:
    return (
        _LEVEL_RANK.get(str(match.get("comp_level") or "qm").lower(), 9),
        int(match.get("set_number") or 0),
        int(match.get("match_number") or 0),
    )


def _expected_tba_key(event_key: str, level: str, number: int) -> str:
    # FRC Events numbers playoffs 1..n. TBA's 8-alliance double elimination is sf1m1..sf13m1
    # then f1m1, f1m2, f1m3.
    if level == "Qualification":
        return f"{event_key}_qm{number}"
    return f"{event_key}_sf{number}m1" if number <= 13 else f"{event_key}_f1m{number - 13}"


def _local_to_unix(value: Any, tz_name: Any) -> int | None:
    # FRC Events times are the event's local time with no offset.
    if not isinstance(value, str) or not value or not isinstance(tz_name, str) or not tz_name:
        return None
    try:
        return int(datetime.fromisoformat(value[:19]).replace(tzinfo=ZoneInfo(tz_name)).timestamp())
    except (ValueError, ZoneInfoNotFoundError):
        return None


def _as_tba_payload(first: dict[str, Any], tba_match: dict[str, Any], tz_name: Any) -> dict[str, Any]:
    red, blue = first["scoreRedFinal"], first["scoreBlueFinal"]
    winner = "red" if red > blue else "blue" if blue > red else ""
    actual = _local_to_unix(first.get("actualStartTime"), tz_name)
    return {
        "key": tba_match.get("key"),
        "alliances": {"red": {"score": red}, "blue": {"score": blue}},
        "winning_alliance": winner,
        "actual_time": actual if actual is not None else tba_match.get("actual_time"),
        "predicted_time": tba_match.get("predicted_time"),
        "post_result_time": _local_to_unix(first.get("postResultTime"), tz_name),
    }


def first_results_for_waiting_matches(
    tba: Any,
    event_key: str,
    tba_matches: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    *,
    now: int | None = None,
) -> dict[str, dict[str, Any]]:
    # TBA-shaped match payloads (key -> payload) for matches TBA has no result for but FRC
    # Events does. The usual numbering pairs them if the alliances agree (see _same_lineup);
    # other brackets pair same-alliance matches in playing order (exact teams only).
    if not settings.first_live_results_enabled:
        return {}
    now = int(time.time()) if now is None else now
    levels = _levels_waiting(tba_matches, results, now)
    if not levels:
        return {}
    meta = _event_meta(tba, event_key)
    if meta is None:
        return {}
    season = int(meta.get("year") or str(event_key)[:4])

    tba_by_key = {str(m.get("key") or "").lower(): m for m in tba_matches}
    tba_by_alliances: dict[tuple[str, frozenset[str], frozenset[str]], list[dict[str, Any]]] = defaultdict(list)
    for match in sorted(tba_matches, key=_tba_order):
        sides = _tba_alliances(match)
        if sides:
            tba_by_alliances[(_tba_level(match), *sides)].append(match)

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for level in levels:
        # One row per match number (a replay keeps the latest), in playing order.
        by_number = {int(m.get("matchNumber") or 0): m for m in _played_matches(season, meta["code"], level)}
        leftovers: dict[tuple[str, frozenset[str], frozenset[str]], list[dict[str, Any]]] = defaultdict(list)
        for number, first in sorted(by_number.items()):
            if not isinstance(first.get("scoreRedFinal"), int) or not isinstance(first.get("scoreBlueFinal"), int):
                continue
            sides = _first_alliances(first)
            if not sides:
                continue
            # The usual numbering first, accepted only if all six teams agree.
            expected = tba_by_key.get(_expected_tba_key(event_key, level, number))
            if expected is not None and _same_lineup(expected, first, level):
                pairs.append((first, expected))
            else:
                leftovers[(level, *sides)].append(first)
        # Other bracket formats: pair same-alliance matches in playing order.
        for group, firsts in leftovers.items():
            taken = {id(t) for _, t in pairs}
            candidates = [m for m in tba_by_alliances.get(group, []) if id(m) not in taken]
            pairs.extend(zip(firsts, candidates))

    filled: dict[str, dict[str, Any]] = {}
    for first, tba_match in pairs:
        key = str(tba_match.get("key") or "").lower()
        if key and not (results.get(key) or {}).get("is_completed"):
            filled[key] = _as_tba_payload(first, tba_match, meta.get("timezone"))
    if filled:
        logger.info("first_live_results.filled event=%s matches=%s", event_key, ",".join(sorted(filled)))
    return filled
