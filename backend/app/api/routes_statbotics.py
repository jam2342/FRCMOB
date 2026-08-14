import asyncio
import time

from fastapi import APIRouter, HTTPException, Query

from app.core.security import sanitize_external_error
from app.services.season_config import CURRENT_SEASON_YEAR
from app.services.clients import statbotics as statbotics_client
from app.services.utils import _as_float

router = APIRouter(prefix="/statbotics", tags=["statbotics"])


def _event_epa_sort_value(event_payload: dict) -> float | None:
    epa_payload = event_payload.get("epa")
    if not isinstance(epa_payload, dict):
        return None
    for key in ("top_24", "mean", "max"):
        parsed = _as_float(epa_payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _event_is_upcoming(event_payload: dict) -> bool:
    status_raw = event_payload.get("status")
    status = status_raw.strip().lower() if isinstance(status_raw, str) else ""
    if status in {"completed", "complete", "ongoing", "in progress", "live"}:
        return False
    if status in {"upcoming", "pre-event", "future", "scheduled", "not started"}:
        return True
    event_time = event_payload.get("time")
    if isinstance(event_time, (int, float)):
        return int(event_time) >= int(time.time())
    return True


def _normalize_text(value: object) -> str:
    return str(value or "").strip().lower()


def _to_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _extract_norm_epa(row: dict) -> tuple[float | None, float | None, float | None, float | None]:
    norm_epa = row.get("norm_epa")
    if not isinstance(norm_epa, dict):
        return None, None, None, None
    return (
        _as_float(norm_epa.get("current")),
        _as_float(norm_epa.get("recent")),
        _as_float(norm_epa.get("mean")),
        _as_float(norm_epa.get("max")),
    )


def _extract_record_label(row: dict) -> str | None:
    record = row.get("record")
    if not isinstance(record, dict):
        return None
    wins = int(_as_float(record.get("wins")) or 0)
    losses = int(_as_float(record.get("losses")) or 0)
    ties = int(_as_float(record.get("ties")) or 0)
    return f"{wins}-{losses}-{ties}"


def _extract_team_year_epa(year_payload: dict) -> dict[str, float | None]:
    epa = year_payload.get("epa")
    if not isinstance(epa, dict):
        return {
            "epa_unitless": None,
            "epa_total": None,
            "auto_epa": None,
            "teleop_epa": None,
            "endgame_epa": None,
        }
    breakdown = epa.get("breakdown") if isinstance(epa.get("breakdown"), dict) else {}
    total_points = epa.get("total_points") if isinstance(epa.get("total_points"), dict) else {}
    total_from_total_points = _as_float(total_points.get("mean")) if isinstance(total_points, dict) else None
    total_from_breakdown = _as_float(breakdown.get("total_points")) if isinstance(breakdown, dict) else None
    return {
        "epa_unitless": _as_float(epa.get("unitless")),
        "epa_total": total_from_total_points if total_from_total_points is not None else total_from_breakdown,
        "auto_epa": _as_float(breakdown.get("auto_points")) if isinstance(breakdown, dict) else None,
        "teleop_epa": _as_float(breakdown.get("teleop_points")) if isinstance(breakdown, dict) else None,
        "endgame_epa": _as_float(breakdown.get("endgame_points")) if isinstance(breakdown, dict) else None,
    }


@router.get("/team/{team_number}")
async def get_team(team_number: int):
    try:
        team = await statbotics_client.get_team(team_number)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=sanitize_external_error(exc, default="Unable to load team profile from Statbotics."),
        ) from exc

    return {
        "ok": True,
        "team_number": team_number,
        "team": team,
    }


@router.get("/team/{team_number}/event/{event_key}")
async def get_team_event(team_number: int, event_key: str):
    try:
        team_event = await statbotics_client.get_team_event(team_number, event_key)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=sanitize_external_error(exc, default="Unable to load team event profile from Statbotics."),
        ) from exc

    return {
        "ok": True,
        "team_number": team_number,
        "event_key": event_key,
        "team_event": team_event,
    }


@router.get("/team/{team_number}/bundle")
async def get_team_bundle(
    team_number: int,
    event_key: str | None = Query(default=None),
    year: int | None = Query(default=None),
):
    try:
        team = await statbotics_client.get_team(team_number)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=sanitize_external_error(exc, default="Unable to load team profile from Statbotics."),
        ) from exc

    warnings: list[str] = []
    team_event = None
    team_year = None
    resolved_year = year

    if event_key:
        try:
            team_event = await statbotics_client.get_team_event(team_number, event_key)
        except RuntimeError as exc:
            warnings.append(
                f"team_event unavailable: {sanitize_external_error(exc, default='request_failed')}"
            )

    if year:
        year_candidates: list[int] = []
        for candidate in (year, year - 1, year - 2):
            if candidate >= 1992 and candidate not in year_candidates:
                year_candidates.append(candidate)

        last_year_error: str | None = None
        for candidate in year_candidates:
            try:
                team_year = await statbotics_client.get_team_year(team_number, candidate)
                resolved_year = candidate
                if candidate != year:
                    warnings.append(
                        f"team_year for {year} unavailable; using {candidate}."
                    )
                break
            except RuntimeError as exc:
                last_year_error = sanitize_external_error(exc, default="request_failed")

        if team_year is None and last_year_error:
            warnings.append(f"team_year unavailable: {last_year_error}")

    return {
        "ok": True,
        "team_number": team_number,
        "event_key": event_key,
        "year": resolved_year,
        "resolved_year": resolved_year,  # kept for backward compat
        "team": team,
        "team_event": team_event,
        "team_year": team_year,
        "warnings": warnings,
    }


@router.get("/events/epa")
async def get_events_epa(
    event_keys: str = Query(default=""),
    limit: int = Query(default=5, ge=1, le=20),
    upcoming_only: bool = Query(default=True),
):
    parsed_keys = []
    seen = set()
    for raw in event_keys.split(","):
        normalized = raw.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parsed_keys.append(normalized)

    if not parsed_keys:
        return {
            "ok": True,
            "count": 0,
            "events": [],
            "errors": [],
        }

    semaphore = asyncio.Semaphore(8)

    async def _load_one(event_key: str):
        async with semaphore:
            try:
                payload = await statbotics_client.get_event(event_key)
                if not isinstance(payload, dict):
                    return event_key, None, f"{event_key}: invalid_payload"
                return event_key, payload, None
            except Exception as exc:
                return event_key, None, f"{event_key}: {sanitize_external_error(exc, default='request_failed')}"

    responses = await asyncio.gather(*[_load_one(key) for key in parsed_keys])
    rows = []
    errors = []
    key_order = {event_key: idx for idx, event_key in enumerate(parsed_keys)}
    for requested_key, payload, error in responses:
        if error:
            errors.append(error)
        if payload is None:
            rows.append(
                {
                    "event_key": requested_key,
                    "name": requested_key,
                    "time": None,
                    "status": "unknown",
                    "status_str": "Statbotics pending",
                    "num_teams": None,
                    "epa": {"top_24": None, "mean": None, "max": None},
                    "epa_sort": None,
                    "is_upcoming": True,
                }
            )
            continue

        sort_epa = _event_epa_sort_value(payload)
        if upcoming_only and not _event_is_upcoming(payload):
            continue

        epa_payload = payload.get("epa") if isinstance(payload.get("epa"), dict) else {}
        event_key = str(payload.get("key") or requested_key).strip().lower()
        rows.append(
            {
                "event_key": event_key,
                "name": payload.get("name") or event_key,
                "time": payload.get("time"),
                "status": payload.get("status"),
                "status_str": payload.get("status_str"),
                "num_teams": payload.get("num_teams"),
                "epa": {
                    "top_24": _as_float(epa_payload.get("top_24")),
                    "mean": _as_float(epa_payload.get("mean")),
                    "max": _as_float(epa_payload.get("max")),
                },
                "epa_sort": sort_epa,
                "is_upcoming": _event_is_upcoming(payload),
            }
        )

    rows.sort(
        key=lambda row: (
            -(row.get("epa_sort") if isinstance(row.get("epa_sort"), (int, float)) else -1.0),
            row.get("time") if isinstance(row.get("time"), (int, float)) else float("inf"),
            key_order.get(str(row.get("event_key") or ""), 9999),
            str(row.get("event_key") or ""),
        )
    )
    trimmed = rows[:limit]
    return {
        "ok": True,
        "count": len(trimmed),
        "events": trimmed,
        "errors": errors[:20],
    }


@router.get("/teams/insights")
async def get_teams_insights(
    year: int = Query(default=CURRENT_SEASON_YEAR, ge=1992, le=2100),
    page: int = Query(default=1, ge=1, le=500),
    per_page: int = Query(default=25, ge=5, le=200),
    country: str | None = Query(default=None),
    state: str | None = Query(default=None),
    district: str | None = Query(default=None),
    competing: str = Query(default="all", pattern="^(all|yes|no)$"),
    q: str | None = Query(default=None),
    include_breakdown: bool = Query(default=True),
):
    try:
        raw_rows = await statbotics_client.get_all_teams(
            include_inactive=True,
            page_limit=1000,
            max_pages=40,
            metric="norm_epa",
            year=year,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=sanitize_external_error(exc, default="Unable to load Statbotics teams insights."),
        ) from exc

    base_rows: list[dict] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        team_number_float = _as_float(raw.get("team"))
        if team_number_float is None:
            continue
        team_number = int(team_number_float)
        norm_current, norm_recent, norm_mean, norm_max = _extract_norm_epa(raw)
        team_key = f"frc{team_number}"
        base_rows.append(
            {
                "team_number": team_number,
                "team_key": team_key,
                "name": raw.get("name"),
                "country": raw.get("country"),
                "state": raw.get("state"),
                "district": raw.get("district"),
                "competing": _to_bool(raw.get("active")),
                "record": _extract_record_label(raw),
                "norm_epa_current": norm_current,
                "norm_epa_recent": norm_recent,
                "norm_epa_mean": norm_mean,
                "norm_epa_max": norm_max,
                "epa_unitless": None,
                "epa_total": None,
                "auto_epa": None,
                "teleop_epa": None,
                "endgame_epa": None,
                "resolved_year": None,
                "uses_year_fallback": False,
                "next_event": None,
            }
        )

    countries = sorted({str(row.get("country")).strip() for row in base_rows if str(row.get("country") or "").strip()})
    states = sorted({str(row.get("state")).strip() for row in base_rows if str(row.get("state") or "").strip()})
    districts = sorted({str(row.get("district")).strip() for row in base_rows if str(row.get("district") or "").strip()})

    country_filter = _normalize_text(country)
    state_filter = _normalize_text(state)
    district_filter = _normalize_text(district)
    query_filter = _normalize_text(q)

    filtered_rows = []
    for row in base_rows:
        if country_filter and _normalize_text(row.get("country")) != country_filter:
            continue
        if state_filter and _normalize_text(row.get("state")) != state_filter:
            continue
        if district_filter and _normalize_text(row.get("district")) != district_filter:
            continue
        competing_value = row.get("competing")
        if competing == "yes" and competing_value is not True:
            continue
        if competing == "no" and competing_value is not False:
            continue
        if query_filter:
            team_number_text = str(row.get("team_number") or "")
            name_text = _normalize_text(row.get("name"))
            team_key_text = _normalize_text(row.get("team_key"))
            if (
                query_filter not in team_number_text
                and query_filter not in name_text
                and query_filter not in team_key_text
            ):
                continue
        filtered_rows.append(row)

    filtered_rows.sort(
        key=lambda row: (
            -(row.get("norm_epa_current") if isinstance(row.get("norm_epa_current"), (int, float)) else -1.0),
            int(row.get("team_number") or 0),
        )
    )

    total_count = len(filtered_rows)
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    safe_page = min(page, total_pages)
    start_idx = max(0, (safe_page - 1) * per_page)
    end_idx = start_idx + per_page
    page_rows = [dict(row) for row in filtered_rows[start_idx:end_idx]]

    warnings: list[str] = []
    if include_breakdown and page_rows:
        semaphore = asyncio.Semaphore(10)

        async def _load_team_year(team_number: int) -> tuple[dict | None, int | None, str | None]:
            year_candidates = [year, year - 1, year - 2]
            seen = set()
            unique_candidates: list[int] = []
            for candidate in year_candidates:
                if candidate >= 1992 and candidate not in seen:
                    seen.add(candidate)
                    unique_candidates.append(candidate)

            last_error: str | None = None
            async with semaphore:
                for candidate in unique_candidates:
                    try:
                        payload = await statbotics_client.get_team_year(team_number, candidate)
                    except RuntimeError as exc:
                        last_error = sanitize_external_error(exc, default="request_failed")
                        continue
                    if isinstance(payload, dict) and isinstance(payload.get("epa"), dict):
                        return payload, candidate, None
                    if isinstance(payload, dict) and payload:
                        return payload, candidate, None
                return None, None, last_error

        enriched_results = await asyncio.gather(
            *[_load_team_year(int(row["team_number"])) for row in page_rows]
        )

        fallback_count = 0
        for row, (team_year_payload, resolved_year, team_year_error) in zip(page_rows, enriched_results):
            if team_year_error:
                warnings.append(f"{row['team_key']}: {team_year_error}")
            if not isinstance(team_year_payload, dict):
                continue
            extracted = _extract_team_year_epa(team_year_payload)
            row["epa_unitless"] = extracted["epa_unitless"]
            row["epa_total"] = extracted["epa_total"]
            row["auto_epa"] = extracted["auto_epa"]
            row["teleop_epa"] = extracted["teleop_epa"]
            row["endgame_epa"] = extracted["endgame_epa"]
            row["resolved_year"] = resolved_year
            row["uses_year_fallback"] = bool(resolved_year is not None and resolved_year != year)
            if row["uses_year_fallback"]:
                fallback_count += 1
        if fallback_count > 0:
            warnings.append(
                f"{fallback_count} team(s) are using fallback year EPA because {year} is sparse."
            )

    for idx, row in enumerate(page_rows, start=start_idx + 1):
        row["epa_rank"] = idx

    return {
        "ok": True,
        "year": year,
        "page": safe_page,
        "per_page": per_page,
        "total_count": total_count,
        "total_pages": total_pages,
        "count": len(page_rows),
        "rows": page_rows,
        "filter_options": {
            "countries": countries,
            "states": states,
            "districts": districts,
        },
        "warnings": warnings[:30],
    }
