from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.db.session import SessionLocal
from app.services.game_config import load_game_config
from app.services.utils import _as_float, _clamp

DEFAULT_TELEOP_INTERCEPT = 6.0
DEFAULT_TELEOP_SLOPE = 1.08
DEFAULT_AUTO_INTERCEPT = 0.0
DEFAULT_AUTO_SLOPE = 0.27

_CALIBRATION_CACHE: dict[str, Any] | None = None
_CALIBRATION_CACHE_AT_TS = 0.0


def _default_calibration_payload(
    *,
    disabled: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False if disabled or error else True,
        "disabled": bool(disabled),
        "error": str(error) if error else None,
        "model_version": "fuel_phase_cal_v1",
        "phase_weights": _phase_weights(),
        "teleop": {
            "intercept": DEFAULT_TELEOP_INTERCEPT,
            "slope": DEFAULT_TELEOP_SLOPE,
            "sample_count": 0,
            "r2": None,
            "calibrated": False,
        },
        "auto": {
            "intercept": DEFAULT_AUTO_INTERCEPT,
            "slope": DEFAULT_AUTO_SLOPE,
            "sample_count": 0,
            "r2": None,
            "calibrated": False,
        },
    }


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()





def _phase_weights() -> dict[str, float]:
    config = load_game_config()
    phases = getattr(config, "phases", None)
    auto_sec = _as_float(getattr(phases, "auto_sec", 20.0)) or 20.0
    teleop_sec = _as_float(getattr(phases, "teleop_sec", 120.0)) or 120.0
    auto_sec = max(1.0, float(auto_sec))
    teleop_sec = max(1.0, float(teleop_sec))
    total = auto_sec + teleop_sec
    return {
        "auto_sec": float(auto_sec),
        "teleop_sec": float(teleop_sec),
        "auto_weight": float(auto_sec / total),
        "teleop_weight": float(teleop_sec / total),
    }


def _fit_linear(samples: list[tuple[float, float]]) -> dict[str, float] | None:
    if len(samples) < 2:
        return None
    xs = [sample[0] for sample in samples]
    ys = [sample[1] for sample in samples]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    var_x = sum((value - mean_x) ** 2 for value in xs)
    if var_x <= 1e-6:
        return None
    cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in samples)
    slope = cov_xy / var_x
    intercept = mean_y - slope * mean_x
    ss_tot = sum((value - mean_y) ** 2 for value in ys)
    ss_res = sum((y - (intercept + (slope * x))) ** 2 for x, y in samples)
    r2 = 0.0 if ss_tot <= 1e-6 else (1.0 - (ss_res / ss_tot))
    return {
        "intercept": float(intercept),
        "slope": float(slope),
        "r2": float(_clamp(r2, -1.0, 1.0)),
        "sample_count": float(len(samples)),
    }


def _build_calibration(db: Session) -> dict[str, Any]:
    min_samples = max(10, int(settings.fuel_rate_calibration_min_samples))
    max_rows = max(200, int(settings.fuel_rate_calibration_max_rows))
    weights = _phase_weights()
    auto_sec = float(weights["auto_sec"])

    rows = (
        db.query(models.TeamMatchFinding, models.EventTeamRating)
        .join(
            models.EventTeamRating,
            (models.EventTeamRating.event_key == models.TeamMatchFinding.event_key)
            & (models.EventTeamRating.team_key == models.TeamMatchFinding.team_key),
        )
        .filter(models.TeamMatchFinding.source.ilike("%score_breakdown%"))
        .order_by(models.TeamMatchFinding.id.desc())
        .limit(max_rows)
        .all()
    )

    teleop_samples: list[tuple[float, float]] = []
    auto_samples: list[tuple[float, float]] = []

    for finding, rating in rows:
        throughput_score = _as_float(getattr(rating, "throughput", None))
        if throughput_score is not None:
            teleop_rate_actual = _as_float(getattr(finding, "fuel_scoring_rate", None))
            if teleop_rate_actual is not None and teleop_rate_actual >= 0.0:
                teleop_samples.append((throughput_score, teleop_rate_actual))

        details = getattr(rating, "details_json", {}) if isinstance(getattr(rating, "details_json", {}), dict) else {}
        subscores = details.get("subscores") if isinstance(details.get("subscores"), dict) else {}
        auto_score = _as_float(subscores.get("auto_contribution"))
        if auto_score is None:
            continue
        auto_points_actual = _as_float(getattr(finding, "auto_contribution", None))
        if auto_points_actual is None or auto_points_actual < 0.0:
            continue
        auto_rate_actual = (auto_points_actual / max(1.0, auto_sec)) * 60.0
        auto_samples.append((auto_score, auto_rate_actual))

    teleop_fit = _fit_linear(teleop_samples) if len(teleop_samples) >= min_samples else None
    auto_fit = _fit_linear(auto_samples) if len(auto_samples) >= min_samples else None

    teleop_intercept = (
        _clamp(float(teleop_fit["intercept"]), -8.0, 40.0)
        if isinstance(teleop_fit, dict)
        else DEFAULT_TELEOP_INTERCEPT
    )
    teleop_slope = (
        _clamp(float(teleop_fit["slope"]), 0.2, 2.2)
        if isinstance(teleop_fit, dict)
        else DEFAULT_TELEOP_SLOPE
    )
    auto_intercept = (
        _clamp(float(auto_fit["intercept"]), -8.0, 35.0)
        if isinstance(auto_fit, dict)
        else DEFAULT_AUTO_INTERCEPT
    )
    auto_slope = (
        _clamp(float(auto_fit["slope"]), 0.02, 0.9)
        if isinstance(auto_fit, dict)
        else DEFAULT_AUTO_SLOPE
    )

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": "fuel_phase_cal_v1",
        "phase_weights": weights,
        "teleop": {
            "intercept": round(teleop_intercept, 6),
            "slope": round(teleop_slope, 6),
            "sample_count": len(teleop_samples),
            "r2": round(float(teleop_fit["r2"]), 6) if isinstance(teleop_fit, dict) else None,
            "calibrated": bool(isinstance(teleop_fit, dict)),
        },
        "auto": {
            "intercept": round(auto_intercept, 6),
            "slope": round(auto_slope, 6),
            "sample_count": len(auto_samples),
            "r2": round(float(auto_fit["r2"]), 6) if isinstance(auto_fit, dict) else None,
            "calibrated": bool(isinstance(auto_fit, dict)),
        },
    }


def get_fuel_phase_calibration(force_refresh: bool = False) -> dict[str, Any]:
    global _CALIBRATION_CACHE, _CALIBRATION_CACHE_AT_TS

    if not bool(settings.fuel_rate_calibration_enabled):
        return _default_calibration_payload(disabled=True)

    now = _now_ts()
    ttl_sec = max(60, int(settings.fuel_rate_calibration_ttl_sec))
    if (
        not force_refresh
        and isinstance(_CALIBRATION_CACHE, dict)
        and (now - float(_CALIBRATION_CACHE_AT_TS)) <= float(ttl_sec)
    ):
        return _CALIBRATION_CACHE

    db = SessionLocal()
    try:
        try:
            calibration = _build_calibration(db)
        except Exception:
            # Keep endpoint behavior stable when external DB credentials are absent
            # in local/test environments.
            calibration = _default_calibration_payload(error="build_failed")
        _CALIBRATION_CACHE = calibration
        _CALIBRATION_CACHE_AT_TS = now
        return calibration
    finally:
        db.close()


def estimate_fuel_rate_per_min(
    *,
    throughput_score_0_100: float | None,
    auto_score_0_100: float | None,
) -> dict[str, Any]:
    calibration = get_fuel_phase_calibration()
    teleop_cfg = calibration.get("teleop") if isinstance(calibration.get("teleop"), dict) else {}
    auto_cfg = calibration.get("auto") if isinstance(calibration.get("auto"), dict) else {}
    weights = calibration.get("phase_weights") if isinstance(calibration.get("phase_weights"), dict) else {}
    teleop_weight = _as_float(weights.get("teleop_weight")) or (120.0 / 140.0)
    auto_weight = _as_float(weights.get("auto_weight")) or (20.0 / 140.0)

    teleop_rate = None
    if isinstance(throughput_score_0_100, (int, float)):
        teleop_rate = (
            _as_float(teleop_cfg.get("intercept")) or DEFAULT_TELEOP_INTERCEPT
        ) + (
            (_as_float(teleop_cfg.get("slope")) or DEFAULT_TELEOP_SLOPE)
            * float(throughput_score_0_100)
        )

    auto_rate = None
    if isinstance(auto_score_0_100, (int, float)):
        auto_rate = (
            _as_float(auto_cfg.get("intercept")) or DEFAULT_AUTO_INTERCEPT
        ) + (
            (_as_float(auto_cfg.get("slope")) or DEFAULT_AUTO_SLOPE)
            * float(auto_score_0_100)
        )

    combined = None
    if isinstance(teleop_rate, (int, float)) and isinstance(auto_rate, (int, float)):
        combined = (float(teleop_rate) * teleop_weight) + (float(auto_rate) * auto_weight)
    elif isinstance(teleop_rate, (int, float)):
        combined = float(teleop_rate)
    elif isinstance(auto_rate, (int, float)):
        combined = float(auto_rate)

    return {
        "fuel_rate_per_min": (
            round(_clamp(float(combined), 0.0, 180.0), 3)
            if isinstance(combined, (int, float))
            else None
        ),
        "teleop_rate_per_min": round(float(teleop_rate), 3) if isinstance(teleop_rate, (int, float)) else None,
        "auto_rate_per_min": round(float(auto_rate), 3) if isinstance(auto_rate, (int, float)) else None,
        "calibration": calibration,
    }
