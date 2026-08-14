from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db import models
from app.services.game_config import load_game_config
from app.services.hash_utils import stable_payload_hash
from app.services.season_config import (
    REBUILT_ALLIANCE_SHIFT_COUNT,
    REBUILT_ALLIANCE_SHIFT_SEC,
    REBUILT_ENDGAME_WINDOW_SEC,
    REBUILT_SEASON_YEAR,
    REBUILT_TRANSITION_SHIFT_SEC,
    shift_active_alliance,
)

PHASE_MODEL_VERSION = "phase_v1"

def _window(start_sec: int, end_sec: int, match_time: int | None) -> dict[str, Any]:
    return {
        "start_sec": int(start_sec),
        "end_sec": int(end_sec),
        "duration_sec": max(0, int(end_sec) - int(start_sec)),
        "start_unix": (int(match_time) + int(start_sec)) if match_time is not None else None,
        "end_unix": (int(match_time) + int(end_sec)) if match_time is not None else None,
    }

def _windowf(start_sec: float, end_sec: float, match_time: int | None) -> dict[str, Any]:
    # Like ``_window`` but accepts floats (for sub-second shift boundaries).
    s = int(round(start_sec))
    e = int(round(end_sec))
    return _window(s, e, match_time)

# ── REBUILT 2026 shift-aware payload ───────────────────────────────────────────

def compute_rebuilt_phase_windows_payload(
    match_time: int | None,
    config: object | None = None,
) -> dict[str, Any]:
    # Compute phase windows for the 2026 REBUILT game.
    #
    # Returns the standard backward-compatible keys (auto, teleop,
    # teleop_scoring, endgame) **plus** REBUILT-specific keys: transition,
    # shift_1 .. shift_4.  Each shift window also carries an
    # ``active_alliance_hint`` (None when the first-shift alliance is unknown).
    if config is None:
        config = load_game_config()

    total_sec = int(config.phases.total_sec)
    auto_end_sec = int(config.phases.auto_sec)
    endgame_sec = int(config.phases.endgame_sec)
    endgame_start_sec = max(auto_end_sec, total_sec - endgame_sec)

    # -- Backward-compatible windows (always present) --
    windows: dict[str, Any] = {
        "auto": _window(0, auto_end_sec, match_time),
        "teleop": _window(auto_end_sec, total_sec, match_time),
        "teleop_scoring": _window(auto_end_sec, endgame_start_sec, match_time),
        "endgame": _window(endgame_start_sec, total_sec, match_time),
    }

    # -- REBUILT-specific windows --
    transition_end = min(
        endgame_start_sec,
        auto_end_sec + REBUILT_TRANSITION_SHIFT_SEC,
    )
    windows["transition"] = _windowf(auto_end_sec, transition_end, match_time)

    shift_cursor = transition_end
    for shift_idx in range(1, REBUILT_ALLIANCE_SHIFT_COUNT + 1):
        shift_end = min(shift_cursor + REBUILT_ALLIANCE_SHIFT_SEC, endgame_start_sec)
        win = _windowf(shift_cursor, shift_end, match_time)
        win["shift_index"] = shift_idx
        # Hint is None at this layer; callers with match-specific data can
        # resolve the actual first-shift alliance later.
        win["active_alliance_hint"] = shift_active_alliance(shift_idx, None)
        windows[f"shift_{shift_idx}"] = win
        shift_cursor = shift_end

    # -- Timing metadata for hash stability --
    params_payload = {
        "season_year": int(config.season_year),
        "version": str(config.version),
        "timing": {
            "total_sec": total_sec,
            "auto_sec": auto_end_sec,
            "teleop_sec": int(config.phases.teleop_sec),
            "endgame_sec": endgame_sec,
            "transition_sec": REBUILT_TRANSITION_SHIFT_SEC,
            "alliance_shift_sec": REBUILT_ALLIANCE_SHIFT_SEC,
            "alliance_shift_count": REBUILT_ALLIANCE_SHIFT_COUNT,
            "endgame_window_sec": REBUILT_ENDGAME_WINDOW_SEC,
        },
        "window_keys": sorted(windows.keys()),
    }

    return {
        "source": "game_config",
        "model_version": PHASE_MODEL_VERSION,
        "config_season": int(config.season_year),
        "config_version": str(config.version),
        "total_sec": total_sec,
        "windows": windows,
        "params_hash": stable_payload_hash(params_payload),
    }

# ── Generic (season-detecting) payload ─────────────────────────────────────────

def compute_phase_windows_payload(match_time: int | None) -> dict[str, Any]:
    config = load_game_config()

    # Delegate to the REBUILT-specific builder for the 2026 season.
    if int(config.season_year) >= REBUILT_SEASON_YEAR:
        return compute_rebuilt_phase_windows_payload(match_time, config=config)

    total_sec = int(config.phases.total_sec)
    auto_end_sec = int(config.phases.auto_sec)
    endgame_start_sec = max(auto_end_sec, total_sec - int(config.phases.endgame_sec))
    windows = {
        "auto": _window(0, auto_end_sec, match_time),
        "teleop": _window(auto_end_sec, total_sec, match_time),
        "teleop_scoring": _window(auto_end_sec, endgame_start_sec, match_time),
        "endgame": _window(endgame_start_sec, total_sec, match_time),
    }
    params_payload = {
        "season_year": int(config.season_year),
        "version": str(config.version),
        "timing": {
            "total_sec": total_sec,
            "auto_sec": auto_end_sec,
            "teleop_sec": int(config.phases.teleop_sec),
            "endgame_sec": int(config.phases.endgame_sec),
        },
        "window_keys": sorted(windows.keys()),
    }
    return {
        "source": "game_config",
        "model_version": PHASE_MODEL_VERSION,
        "config_season": int(config.season_year),
        "config_version": str(config.version),
        "total_sec": total_sec,
        "windows": windows,
        "params_hash": stable_payload_hash(params_payload),
    }

def upsert_match_phase_windows(
    db: Session,
    match: models.Match,
    *,
    source: str = "game_config",
    confidence_0_1: float = 0.9,
    force: bool = False,
) -> dict[str, Any]:
    payload = compute_phase_windows_payload(match.time)
    if source != payload["source"]:
        payload["source"] = source

    params_hash = str(payload["params_hash"])
    if not force:
        existing_rows = (
            db.query(models.MatchPhaseWindow)
            .filter(
                models.MatchPhaseWindow.match_key == match.match_key,
                models.MatchPhaseWindow.model_version == PHASE_MODEL_VERSION,
            )
            .all()
        )
        if existing_rows and all((row.params_hash or "") == params_hash for row in existing_rows):
            return payload

    now = datetime.now(timezone.utc)
    for phase_key, window in payload["windows"].items():
        db.merge(
            models.MatchPhaseWindow(
                match_key=match.match_key,
                phase_key=str(phase_key),
                model_version=PHASE_MODEL_VERSION,
                event_key=match.event_key,
                start_sec=float(window["start_sec"]),
                end_sec=float(window["end_sec"]),
                duration_sec=float(window["duration_sec"]),
                start_unix=int(window["start_unix"]) if window["start_unix"] is not None else None,
                end_unix=int(window["end_unix"]) if window["end_unix"] is not None else None,
                source=str(payload["source"]),
                confidence_0_1=max(0.0, min(1.0, float(confidence_0_1))),
                params_hash=params_hash,
                computed_at=now,
            )
        )
    db.commit()
    return payload

def get_or_compute_match_phase_windows(
    db: Session,
    match: models.Match,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    if refresh:
        return upsert_match_phase_windows(db, match, force=True)

    rows = (
        db.query(models.MatchPhaseWindow)
        .filter(
            models.MatchPhaseWindow.match_key == match.match_key,
            models.MatchPhaseWindow.model_version == PHASE_MODEL_VERSION,
        )
        .all()
    )
    if not rows:
        return upsert_match_phase_windows(db, match, force=True)

    by_phase: dict[str, dict[str, Any]] = {}
    latest = rows[0]
    for row in rows:
        by_phase[row.phase_key] = {
            "start_sec": int(row.start_sec),
            "end_sec": int(row.end_sec),
            "duration_sec": int(row.duration_sec),
            "start_unix": row.start_unix,
            "end_unix": row.end_unix,
            "confidence_0_1": row.confidence_0_1,
        }
        if row.computed_at and latest.computed_at and row.computed_at > latest.computed_at:
            latest = row

    total_sec = by_phase.get("teleop", {}).get("end_sec")
    if not isinstance(total_sec, int):
        total_sec = int(load_game_config().phases.total_sec)

    return {
        "source": latest.source,
        "model_version": PHASE_MODEL_VERSION,
        "config_season": int(load_game_config().season_year),
        "config_version": str(load_game_config().version),
        "total_sec": total_sec,
        "windows": by_phase,
        "params_hash": latest.params_hash,
    }
