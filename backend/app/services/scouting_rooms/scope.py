from datetime import datetime, timezone

from app.core.config import settings
from app.services.game_config import load_game_config

FRESHNESS_WARNING_DATE_FORMAT = "%Y-%m-%d"


def event_year_from_key(event_key: str | None) -> int | None:
    value = (event_key or "").strip()
    if len(value) < 4 or not value[:4].isdigit():
        return None
    return int(value[:4])


def active_game_season_year() -> int:
    try:
        return int(load_game_config().season_year)
    except Exception:
        return datetime.now(timezone.utc).year


def build_data_freshness_payload(
    *,
    season_scope: dict,
    match_times: list[int | None],
    analyzed_matches: int,
    raw_analyzed_matches: int | None = None,
    quality_gate_enabled: bool | None = None,
    quality_gate_fallback_used: bool = False,
) -> dict:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    latest_match_time = max(
        (int(match_time) for match_time in match_times if isinstance(match_time, int) and match_time > 0),
        default=None,
    )
    latest_match_age_days = (
        max(0.0, (now_ts - latest_match_time) / 86400.0)
        if latest_match_time is not None
        else None
    )

    warnings: list[str] = []
    scoped_year = season_scope.get("season_year")
    active_year = season_scope.get("active_season_year")
    source = str(season_scope.get("source") or "")
    raw_count = int(raw_analyzed_matches) if isinstance(raw_analyzed_matches, int) else None
    accepted_ratio = None
    if raw_count is not None and raw_count > 0:
        accepted_ratio = max(0.0, min(1.0, float(analyzed_matches) / float(raw_count)))

    if isinstance(scoped_year, int) and isinstance(active_year, int) and scoped_year != active_year:
        if source == "latest_available":
            warnings.append(
                f"Using {scoped_year} season data because no analyzed matches are available for {active_year} yet."
            )
        else:
            warnings.append(
                f"Data is scoped to {scoped_year}; current configured season is {active_year}."
            )

    if analyzed_matches <= 0 and isinstance(scoped_year, int):
        warnings.append(f"No analyzed matches available for {scoped_year}.")
        if bool(quality_gate_enabled) and raw_count and raw_count > 0:
            warnings.append(
                f"Quality gate excluded all {raw_count} candidate matches for {scoped_year}; "
                "metrics use low-confidence fallback coverage."
            )

    if quality_gate_fallback_used and analyzed_matches <= 0:
        warnings.append(
            "Fallback metrics are estimated from low-quality analyzed runs and should be treated as provisional."
        )

    if (
        bool(quality_gate_enabled)
        and raw_count is not None
        and raw_count >= 4
        and isinstance(accepted_ratio, float)
        and accepted_ratio < 0.4
    ):
        warnings.append(
            f"Quality gate accepted {analyzed_matches}/{raw_count} matches; coverage may be unstable."
        )

    outdated_days_threshold = max(1, int(settings.scouting_data_outdated_days))
    if latest_match_age_days is not None and latest_match_age_days >= float(outdated_days_threshold):
        played_on = datetime.fromtimestamp(latest_match_time, timezone.utc).strftime(FRESHNESS_WARNING_DATE_FORMAT)
        warnings.append(
            f"Latest analyzed match is {int(round(latest_match_age_days))} days old (played {played_on} UTC)."
        )

    return {
        "is_outdated": len(warnings) > 0,
        "outdated_days_threshold": outdated_days_threshold,
        "latest_match_time": latest_match_time,
        "latest_match_age_days": (
            round(latest_match_age_days, 2) if latest_match_age_days is not None else None
        ),
        "accepted_matches": int(analyzed_matches),
        "raw_matches": raw_count,
        "accepted_ratio_0_1": (round(accepted_ratio, 3) if isinstance(accepted_ratio, float) else None),
        "quality_gate_enabled": bool(quality_gate_enabled) if quality_gate_enabled is not None else None,
        "quality_gate_fallback_used": bool(quality_gate_fallback_used),
        "warnings": warnings,
    }
