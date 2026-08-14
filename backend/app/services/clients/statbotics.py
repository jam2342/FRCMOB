from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# HTTP client configuration
DEFAULT_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
DEFAULT_TTL_SEC = 300
MAX_TEAMS_PAGE_LIMIT = 1000
MAX_RETRIES = 2
RETRY_BACKOFF_MS = 200
MAX_CACHE_ENTRIES = 3000
DEFAULT_STALE_TTL_SEC = 900

# Persistent async client with connection pooling
_CLIENT: httpx.AsyncClient | None = None
_CLIENT_LOCK: asyncio.Lock | None = None

# Response cache with TTL (fresh + stale)
_CACHE: dict[str, tuple[float, float, float, Any]] = {}

# Endpoint-scoped circuit breaker state.
_CIRCUIT: dict[str, dict[str, Any]] = {}

class _NonRetryableStatboticsError(RuntimeError):
    # Represents an upstream error that should fail fast without retries.
    pass

def _cache_ttls() -> tuple[int, int]:
    fresh_ttl = max(5, int(getattr(settings, "statbotics_cache_ttl_sec", DEFAULT_TTL_SEC)))
    stale_ttl = max(
        fresh_ttl,
        int(getattr(settings, "statbotics_stale_cache_ttl_sec", DEFAULT_STALE_TTL_SEC)),
    )
    return fresh_ttl, stale_ttl

def _cache_get(key: str) -> Any | None:
    # Get cached fresh response.
    now = time.time()
    entry = _CACHE.get(key)
    if entry is None:
        return None
    expires_at_fresh, expires_at_stale, _, payload = entry
    if now < expires_at_fresh:
        return payload
    if now >= expires_at_stale:
        _CACHE.pop(key, None)
    return None

def _cache_get_stale(key: str) -> Any | None:
    # Get cached response including stale entries used for upstream outage fallback.
    now = time.time()
    entry = _CACHE.get(key)
    if entry is None:
        return None
    _, expires_at_stale, _, payload = entry
    if now >= expires_at_stale:
        _CACHE.pop(key, None)
        return None
    return payload

def _cache_put(
    key: str,
    payload: Any,
    ttl_sec: int = DEFAULT_TTL_SEC,
    stale_ttl_sec: int = DEFAULT_STALE_TTL_SEC,
) -> None:
    # Store response in cache with fresh/stale windows.
    now = time.time()
    ttl = max(5, int(ttl_sec))
    stale_ttl = max(ttl, int(stale_ttl_sec))
    _CACHE[key] = (now + ttl, now + stale_ttl, now, payload)
    _cache_prune(MAX_CACHE_ENTRIES)

def _cache_prune(max_entries: int) -> None:
    if max_entries <= 0:
        _CACHE.clear()
        return

    now = time.time()
    expired_keys = [
        cache_key
        for cache_key, (_, expires_at_stale, _, _) in _CACHE.items()
        if now >= expires_at_stale
    ]
    for cache_key in expired_keys:
        _CACHE.pop(cache_key, None)

    if len(_CACHE) <= max_entries:
        return

    overflow = len(_CACHE) - max_entries
    for cache_key, _ in sorted(_CACHE.items(), key=lambda item: item[1][1])[:overflow]:
        _CACHE.pop(cache_key, None)

def _endpoint_key(path: str) -> str:
    token = str(path or "").split("?", 1)[0].strip().lower()
    parts = [part for part in token.split("/") if part]
    if not parts:
        return "unknown"
    return parts[0]

def _circuit_threshold() -> int:
    return max(2, int(getattr(settings, "statbotics_circuit_failure_threshold", 4)))

def _circuit_cooldown_sec() -> int:
    return max(10, int(getattr(settings, "statbotics_circuit_cooldown_sec", 90)))

def _circuit_enabled() -> bool:
    return bool(getattr(settings, "statbotics_circuit_breaker_enabled", True))

def _circuit_record_success(endpoint: str) -> None:
    if not endpoint:
        return
    state = _CIRCUIT.get(endpoint)
    if state is None:
        return
    state["failures"] = 0
    state["open_until_ts"] = 0.0
    state["last_success_ts"] = time.time()

def _circuit_record_failure(endpoint: str, *, error: str) -> None:
    if not endpoint:
        return
    state = _CIRCUIT.setdefault(
        endpoint,
        {
            "failures": 0,
            "open_until_ts": 0.0,
            "last_failure_ts": None,
            "last_success_ts": None,
            "last_error": "",
        },
    )
    now = time.time()
    failures = int(state.get("failures") or 0) + 1
    state["failures"] = failures
    state["last_failure_ts"] = now
    state["last_error"] = str(error or "").strip()
    if failures >= _circuit_threshold():
        open_until = now + float(_circuit_cooldown_sec())
        state["open_until_ts"] = open_until
        logger.warning(
            "statbotics circuit opened endpoint=%s failures=%s cooldown_sec=%s",
            endpoint,
            failures,
            _circuit_cooldown_sec(),
        )

def _circuit_open(endpoint: str) -> tuple[bool, float]:
    if not _circuit_enabled():
        return False, 0.0
    state = _CIRCUIT.get(endpoint)
    if not isinstance(state, dict):
        return False, 0.0
    open_until = float(state.get("open_until_ts") or 0.0)
    now = time.time()
    if open_until <= now:
        return False, 0.0
    return True, max(0.0, open_until - now)

def runtime_metrics() -> dict[str, Any]:
    now = time.time()
    open_circuits: list[dict[str, Any]] = []
    for endpoint, state in _CIRCUIT.items():
        open_until = float(state.get("open_until_ts") or 0.0)
        if open_until > now:
            open_circuits.append(
                {
                    "endpoint": endpoint,
                    "failures": int(state.get("failures") or 0),
                    "cooldown_remaining_sec": round(open_until - now, 3),
                    "last_error": str(state.get("last_error") or ""),
                }
            )
    return {
        "cache_entries": len(_CACHE),
        "circuits": {
            "tracked_endpoints": len(_CIRCUIT),
            "open_count": len(open_circuits),
            "open": open_circuits,
        },
    }

def _build_url(path: str) -> str:
    # Build full URL from path.
    return f"{settings.statbotics_base_url.rstrip('/')}{path}"

async def _get_client() -> httpx.AsyncClient:
    # Get or create persistent async HTTP client with connection pooling.
    global _CLIENT, _CLIENT_LOCK

    # Lazily initialize the lock
    if _CLIENT_LOCK is None:
        _CLIENT_LOCK = asyncio.Lock()

    if _CLIENT is None:
        async with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = httpx.AsyncClient(
                    timeout=DEFAULT_TIMEOUT,
                    limits=httpx.Limits(
                        max_connections=10,
                        max_keepalive_connections=5,
                    ),
                )
    return _CLIENT

async def close_client() -> None:
    # Close the persistent HTTP client.
    global _CLIENT, _CLIENT_LOCK
    if _CLIENT is not None:
        if _CLIENT_LOCK is None:
            _CLIENT_LOCK = asyncio.Lock()
        async with _CLIENT_LOCK:
            if _CLIENT is not None:
                await _CLIENT.aclose()
                _CLIENT = None

async def _fetch_json_with_retry(path: str, max_retries: int = MAX_RETRIES) -> Any:
    # Fetch and cache JSON response with retry logic.
    cached = _cache_get(path)
    if cached is not None:
        return cached

    endpoint = _endpoint_key(path)
    open_now, remaining_sec = _circuit_open(endpoint)
    if open_now:
        stale_payload = _cache_get_stale(path)
        if stale_payload is not None and bool(getattr(settings, "statbotics_stale_serve_on_error", True)):
            logger.info(
                "statbotics stale-cache served path=%s endpoint=%s cooldown_remaining=%.2fs",
                path,
                endpoint,
                remaining_sec,
            )
            return stale_payload
        raise RuntimeError(
            f"Statbotics circuit open for {endpoint}; retry in ~{int(max(1.0, remaining_sec))}s."
        )

    url = _build_url(path)
    client = await _get_client()

    fresh_ttl, stale_ttl = _cache_ttls()
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            response = await client.get(url, headers={"Accept": "application/json"})

            if response.status_code >= 400:
                snippet = response.text.strip().replace("\n", " ")
                if len(snippet) > 180:
                    snippet = f"{snippet[:177]}..."
                error_msg = f"Statbotics API returned {response.status_code} for {path}: {snippet}"

                # 5xx errors are retryable; 4xx errors are not
                if response.status_code >= 500:
                    _circuit_record_failure(endpoint, error=error_msg)
                    last_error = RuntimeError(error_msg)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(RETRY_BACKOFF_MS * (2 ** attempt) / 1000)
                        continue
                    stale_payload = _cache_get_stale(path)
                    if stale_payload is not None and bool(getattr(settings, "statbotics_stale_serve_on_error", True)):
                        logger.info(
                            "statbotics stale-cache served after 5xx path=%s endpoint=%s",
                            path,
                            endpoint,
                        )
                        return stale_payload
                    raise last_error

                # 4xx errors do not count toward circuit state and should fail fast.
                raise _NonRetryableStatboticsError(error_msg)

            payload = response.json()
            _cache_put(path, payload, ttl_sec=fresh_ttl, stale_ttl_sec=stale_ttl)
            _circuit_record_success(endpoint)
            return payload

        except _NonRetryableStatboticsError as exc:
            last_error = RuntimeError(str(exc))
            break
        except httpx.RequestError as exc:
            err_msg = f"Statbotics request failed for {url}"
            _circuit_record_failure(endpoint, error=f"{err_msg}: {exc}")
            last_error = RuntimeError(err_msg)
            if attempt < max_retries - 1:
                await asyncio.sleep(RETRY_BACKOFF_MS * (2 ** attempt) / 1000)
                continue
            stale_payload = _cache_get_stale(path)
            if stale_payload is not None and bool(getattr(settings, "statbotics_stale_serve_on_error", True)):
                logger.info(
                    "statbotics stale-cache served after request error path=%s endpoint=%s",
                    path,
                    endpoint,
                )
                return stale_payload
            break
        except Exception as exc:
            last_error = exc if isinstance(exc, RuntimeError) else RuntimeError(str(exc))
            if attempt < max_retries - 1:
                await asyncio.sleep(RETRY_BACKOFF_MS * (2 ** attempt) / 1000)
                continue
            break

    if last_error:
        raise RuntimeError(last_error.args[0] if last_error.args else str(last_error)) from last_error

    raise RuntimeError(f"Failed to fetch {path} after {max_retries} attempts")

async def _fetch_json(path: str) -> Any:
    # Fetch JSON response with caching.
    return await _fetch_json_with_retry(path)

# Public API Functions

async def get_team(team_number: int) -> dict[str, Any]:
    # Fetch team profile by team number.
    payload = await _fetch_json(f"/team/{team_number}")
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Statbotics team response format")
    return payload

async def get_team_event(team_number: int, event_key: str) -> dict[str, Any]:
    # Fetch team event profile.
    payload = await _fetch_json(f"/team_event/{team_number}/{event_key}")
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Statbotics team_event response format")
    return payload

async def get_event(event_key: str) -> dict[str, Any]:
    # Fetch event profile.
    payload = await _fetch_json(f"/event/{event_key}")
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Statbotics event response format")
    return payload

async def get_team_year(team_number: int, year: int) -> dict[str, Any]:
    # Fetch team year profile.
    payload = await _fetch_json(f"/team_year/{team_number}/{year}")
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Statbotics team_year response format")
    return payload

async def get_team_events(
    *,
    event_key: str | None = None,
    team_number: int | None = None,
    year: int | None = None,
    limit: int = MAX_TEAMS_PAGE_LIMIT,
    offset: int = 0,
    metric: str = "team",
) -> list[dict[str, Any]]:
    # Fetch team_event rows with optional filters.
    #
    # Useful for event-level EPA pulls in one request:
    # `/team_events?event=<event_key>`.
    params: dict[str, str | int] = {
        "limit": max(1, min(limit, MAX_TEAMS_PAGE_LIMIT)),
        "offset": max(0, int(offset)),
        "metric": str(metric or "team"),
    }
    if event_key:
        params["event"] = str(event_key).strip().lower()
    if team_number is not None:
        params["team"] = int(team_number)
    if year is not None:
        params["year"] = int(year)

    payload = await _fetch_json(f"/team_events?{urlencode(params)}")
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected Statbotics team_events response format")
    return payload

async def get_teams(
    limit: int = MAX_TEAMS_PAGE_LIMIT,
    offset: int = 0,
    active: bool = True,
    *,
    metric: str = "team",
    year: int | None = None,
) -> list[dict[str, Any]]:
    # Fetch paginated teams list.
    params = {
        "limit": max(1, min(limit, MAX_TEAMS_PAGE_LIMIT)),
        "offset": max(0, offset),
        "active": str(active).lower(),
        "metric": str(metric or "team"),
    }
    if year is not None:
        params["year"] = int(year)
    payload = await _fetch_json(f"/teams?{urlencode(params)}")
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected Statbotics teams response format")
    return payload

async def get_all_teams(
    include_inactive: bool = True,
    page_limit: int = MAX_TEAMS_PAGE_LIMIT,
    max_pages: int = 30,
    *,
    metric: str = "team",
    year: int | None = None,
) -> list[dict[str, Any]]:
    # Fetch all teams across all pages.
    capped_page_limit = max(1, min(page_limit, MAX_TEAMS_PAGE_LIMIT))
    active_modes = [True, False] if include_inactive else [True]
    merged_by_number: dict[int, dict[str, Any]] = {}

    for active in active_modes:
        offset = 0
        for _ in range(max_pages):
            page = await get_teams(
                limit=capped_page_limit,
                offset=offset,
                active=active,
                metric=metric,
                year=year,
            )
            if not page:
                break

            for team in page:
                team_number_raw = team.get("team")
                if not isinstance(team_number_raw, int):
                    continue

                existing = merged_by_number.get(team_number_raw)
                # Prefer "active=true" records if both exist.
                if existing is None or active:
                    merged_by_number[team_number_raw] = team

            if len(page) < capped_page_limit:
                break
            offset += capped_page_limit

    return sorted(
        merged_by_number.values(),
        key=lambda item: int(item.get("team") or 0),
    )
