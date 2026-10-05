from collections import OrderedDict
import logging
import threading
import time
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException

import httpx

from app.core.config import settings

BASE = "https://www.thebluealliance.com/api/v3"
TBA_TIMEOUT_SEC = 8
TBA_MAX_RETRIES = 2
TBA_INITIAL_BACKOFF_SEC = 0.08
TBA_BACKOFF_FACTOR = 1.75
TBA_CACHE_TTL_SEC = 300.0
# Scores, picks, rankings and statuses change during an event. At 300 s the "live"
# views (5 s cache above this) showed results up to five minutes old, and each
# uvicorn worker held its own copy, so refreshes could even go backwards.
TBA_LIVE_CACHE_TTL_SEC = 15.0
TBA_CACHE_MAX_ENTRIES = 2048
TBA_HTTP_POOL_CONNECTIONS = 16
TBA_HTTP_POOL_MAXSIZE = 32

logger = logging.getLogger(__name__)
_TBA_CACHE: OrderedDict[str, tuple[float, dict | list]] = OrderedDict()
_TBA_THREAD_LOCAL = threading.local()
# Route handlers run on a thread pool; the shared OrderedDict isn't safe to mutate unguarded.
_TBA_CACHE_LOCK = threading.Lock()

def _thread_local_tba_session() -> requests.Session:
    # Reuse keep-alive HTTP connections per worker thread.
    session = getattr(_TBA_THREAD_LOCAL, "session", None)
    if isinstance(session, requests.Session):
        return session

    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=TBA_HTTP_POOL_CONNECTIONS,
        pool_maxsize=TBA_HTTP_POOL_MAXSIZE,
        max_retries=0,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    _TBA_THREAD_LOCAL.session = session
    return session

def _prune_tba_cache(now: float | None = None) -> None:
    current = now if isinstance(now, float) else time.monotonic()
    stale_keys = [
        cache_key
        for cache_key, (expires_at, _payload) in _TBA_CACHE.items()
        if current >= float(expires_at)
    ]
    for cache_key in stale_keys:
        _TBA_CACHE.pop(cache_key, None)
    max_entries = max(128, int(getattr(settings, "tba_cache_max_entries", TBA_CACHE_MAX_ENTRIES) or TBA_CACHE_MAX_ENTRIES))
    while len(_TBA_CACHE) > max_entries:
        _TBA_CACHE.popitem(last=False)

def _remember_tba_payload(cache_key: str, payload: dict | list, ttl_sec: float = TBA_CACHE_TTL_SEC) -> None:
    with _TBA_CACHE_LOCK:
        _TBA_CACHE[cache_key] = (time.monotonic() + ttl_sec, payload)
        _TBA_CACHE.move_to_end(cache_key, last=True)
        _prune_tba_cache()

def _cached_tba_payload(cache_key: str) -> dict | list | None:
    with _TBA_CACHE_LOCK:
        _prune_tba_cache()
        cached = _TBA_CACHE.get(cache_key)
        if cached is None:
            return None
        expires_at, payload = cached
        if time.monotonic() < expires_at:
            _TBA_CACHE.move_to_end(cache_key, last=True)
            return payload
        _TBA_CACHE.pop(cache_key, None)
        return None

class TBAClientError(Exception):
    # status_code is set when TBA answered with a client error (e.g. 404 for an
    # event or team that doesn't exist).
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code

class TBAClient:
    def __init__(self):
        self.s = _thread_local_tba_session()
        self._headers = {"X-TBA-Auth-Key": settings.tba_auth_key}

    def _request(self, method: str, url: str, *, ttl_sec: float = TBA_CACHE_TTL_SEC) -> dict | list:
        # Make HTTP request with retry logic and timeout.
        cache_key = f"{method}:{url}"
        cached = _cached_tba_payload(cache_key)
        if cached is not None:
            return cached

        backoff = TBA_INITIAL_BACKOFF_SEC
        last_error = None

        for attempt in range(TBA_MAX_RETRIES):
            try:
                logger.debug(f"TBA request attempt {attempt + 1}/{TBA_MAX_RETRIES}: {method} {url}")
                r = self.s.request(
                    method,
                    url,
                    timeout=TBA_TIMEOUT_SEC,
                    headers=self._headers,
                )

                # Retry on transient errors
                if r.status_code in (429, 500, 502, 503, 504):
                    if attempt < TBA_MAX_RETRIES - 1:
                        logger.warning(
                            f"TBA returned {r.status_code}, retrying after {backoff:.1f}s"
                        )
                        time.sleep(backoff)
                        backoff *= TBA_BACKOFF_FACTOR
                        continue
                    r.raise_for_status()

                # A missing key won't appear on retry: fail at once instead of backing off.
                if 400 <= r.status_code < 500:
                    raise TBAClientError(f"TBA returned {r.status_code} for {url}", status_code=r.status_code)
                r.raise_for_status()
                logger.debug(f"TBA request successful: {url}")
                payload = r.json()
                _remember_tba_payload(cache_key, payload, ttl_sec)
                return payload

            except requests.Timeout as e:
                last_error = e
                if attempt < TBA_MAX_RETRIES - 1:
                    logger.warning(f"TBA timeout, retrying after {backoff:.1f}s")
                    time.sleep(backoff)
                    backoff *= TBA_BACKOFF_FACTOR
                else:
                    raise TBAClientError(f"TBA timeout after {TBA_MAX_RETRIES} attempts") from e

            except RequestException as e:
                last_error = e
                if attempt < TBA_MAX_RETRIES - 1:
                    logger.warning(f"TBA request error: {e}, retrying after {backoff:.1f}s")
                    time.sleep(backoff)
                    backoff *= TBA_BACKOFF_FACTOR
                else:
                    raise TBAClientError(f"TBA request failed after {TBA_MAX_RETRIES} attempts") from e

        raise TBAClientError("TBA request exhausted retries") from last_error

    def events(self, year: int):
        return self._request("GET", f"{BASE}/events/{year}")

    def event(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}")

    def event_matches(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}/matches", ttl_sec=TBA_LIVE_CACHE_TTL_SEC)

    def event_teams(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}/teams")

    def event_alliances(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}/alliances", ttl_sec=TBA_LIVE_CACHE_TTL_SEC)

    def event_oprs(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}/oprs")

    def event_coprs(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}/coprs")

    def event_rankings(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}/rankings", ttl_sec=TBA_LIVE_CACHE_TTL_SEC)

    def event_team_statuses(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}/teams/statuses", ttl_sec=TBA_LIVE_CACHE_TTL_SEC)

    def event_insights(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}/insights")

    def event_predictions(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}/predictions")

    def event_awards(self, event_key: str):
        return self._request("GET", f"{BASE}/event/{event_key}/awards")

    def team_events(self, team_key: str, year: int):
        return self._request("GET", f"{BASE}/team/{team_key}/events/{year}")

    def team_event_status(self, team_key: str, event_key: str):
        return self._request("GET", f"{BASE}/team/{team_key}/event/{event_key}/status", ttl_sec=TBA_LIVE_CACHE_TTL_SEC)

    def team_events_statuses(self, team_key: str, year: int):
        return self._request("GET", f"{BASE}/team/{team_key}/events/{year}/statuses", ttl_sec=TBA_LIVE_CACHE_TTL_SEC)

    def team_awards_year(self, team_key: str, year: int):
        return self._request("GET", f"{BASE}/team/{team_key}/awards/{year}")

    def team_media(self, team_key: str, year: int):
        return self._request("GET", f"{BASE}/team/{team_key}/media/{year}")

    def match(self, match_key: str):
        return self._request("GET", f"{BASE}/match/{match_key}", ttl_sec=TBA_LIVE_CACHE_TTL_SEC)

    def match_zebra_motionworks(self, match_key: str):
        return self._request("GET", f"{BASE}/match/{match_key}/zebra_motionworks")

# ---------------------------------------------------------------------------
# Async convenience helpers (for use in FastAPI async endpoints)
# ---------------------------------------------------------------------------

async def async_get_event(event_key: str) -> dict:
    # Fetch a single TBA event asynchronously via httpx.
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE}/event/{event_key}",
            headers={"X-TBA-Auth-Key": settings.tba_auth_key},
        )
    response.raise_for_status()
    return response.json()
