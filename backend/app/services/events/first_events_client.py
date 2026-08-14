from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

# Retry / circuit-breaker defaults
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE_SEC = 1.0
_CB_FAILURE_THRESHOLD = 5
_CB_RECOVERY_SEC = 60


class FIRSTEventsClient:
    def __init__(self) -> None:
        username = settings.first_frc_api_username.strip()
        auth_key = settings.first_frc_api_auth_key.strip()
        if not username or not auth_key:
            raise RuntimeError(
                "FIRST FRC Events API credentials are not configured. "
                "Set FIRST_FRC_API_USERNAME and FIRST_FRC_API_AUTH_KEY."
            )

        self.base = settings.first_frc_api_base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, auth_key)
        self.session.headers.update({"Accept": "application/json"})

        # Circuit-breaker state
        self._consecutive_failures = 0
        self._circuit_open_until: float = 0.0

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        if_modified_since: str | None = None,
        only_modified_since: str | None = None,
    ) -> tuple[dict[str, Any], str | None, bool]:
        if if_modified_since and only_modified_since:
            raise RuntimeError(
                "FIRST API call rejected: do not send If-Modified-Since and "
                "FMS-OnlyModifiedSince together."
            )

        # Circuit-breaker check
        if self._consecutive_failures >= _CB_FAILURE_THRESHOLD:
            if time.monotonic() < self._circuit_open_until:
                raise RuntimeError(
                    f"FIRST API circuit open — {self._consecutive_failures} consecutive "
                    f"failures. Retry after {int(self._circuit_open_until - time.monotonic())}s."
                )
            logger.info("FIRST API circuit half-open — attempting recovery request")

        headers: dict[str, str] = {}
        if if_modified_since:
            headers["If-Modified-Since"] = if_modified_since
        if only_modified_since:
            headers["FMS-OnlyModifiedSince"] = only_modified_since

        url = f"{self.base}{path}"
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    url, params=params or {}, headers=headers, timeout=20,
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                    logger.warning(
                        "FIRST API request failed for %s (attempt %d/%d), retrying in %.1fs: %s",
                        path, attempt, _MAX_RETRIES, wait, exc,
                    )
                    time.sleep(wait)
                    continue
                self._record_failure()
                raise RuntimeError(f"FIRST API request failed for {path}") from exc

            if response.status_code == 304:
                self._record_success()
                return {}, response.headers.get("Last-Modified"), True

            if response.status_code >= 500 and attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                logger.warning(
                    "FIRST API returned %d for %s (attempt %d/%d), retrying in %.1fs",
                    response.status_code, path, attempt, _MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue

            if response.status_code >= 400:
                self._record_failure()
                snippet = response.text.strip().replace("\n", " ")
                if len(snippet) > 180:
                    snippet = f"{snippet[:177]}..."
                raise RuntimeError(
                    f"FIRST API returned {response.status_code} for {path}: {snippet}"
                )

            # Success
            self._record_success()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected FIRST API response format")
            return payload, response.headers.get("Last-Modified"), False

        # Exhausted retries
        self._record_failure()
        raise RuntimeError(f"FIRST API request failed for {path} after {_MAX_RETRIES} attempts") from last_exc

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _CB_FAILURE_THRESHOLD:
            self._circuit_open_until = time.monotonic() + _CB_RECOVERY_SEC
            logger.error(
                "FIRST API circuit breaker OPEN after %d failures — blocking for %ds",
                self._consecutive_failures, _CB_RECOVERY_SEC,
            )

    def _record_success(self) -> None:
        if self._consecutive_failures > 0:
            logger.info("FIRST API circuit recovered after %d failures", self._consecutive_failures)
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def event_schedule(
        self,
        season: int,
        event_code: str,
        *,
        tournament_level: str | None = None,
        team_number: int | None = None,
        start: int | None = None,
        end: int | None = None,
        if_modified_since: str | None = None,
        only_modified_since: str | None = None,
    ) -> tuple[dict[str, Any], str | None, bool]:
        params: dict[str, Any] = {}
        if tournament_level and tournament_level.lower() != "none":
            params["tournamentLevel"] = tournament_level
        if team_number:
            params["teamNumber"] = team_number
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        return self._get(
            f"/{season}/schedule/{event_code.upper()}",
            params=params,
            if_modified_since=if_modified_since,
            only_modified_since=only_modified_since,
        )

    def event_alliances(
        self,
        season: int,
        event_code: str,
        *,
        if_modified_since: str | None = None,
        only_modified_since: str | None = None,
    ) -> tuple[dict[str, Any], str | None, bool]:
        return self._get(
            f"/{season}/alliances/{event_code.upper()}",
            if_modified_since=if_modified_since,
            only_modified_since=only_modified_since,
        )
