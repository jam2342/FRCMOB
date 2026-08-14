from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import sanitize_external_error
from app.db import models
from app.services.utils import _as_float, _clamp

logger = logging.getLogger(__name__)

CLIMB_AUDIT_LAST_RESULT_KEY = "ops:climb_integrity:last_result"
CLIMB_AUDIT_REDIS_TTL_SEC = 14 * 24 * 60 * 60

def _as_float_0_1(value: object) -> float | None:
    # Parse to float clamped to [0, 1], or ``None``.
    parsed = _as_float(value)
    if parsed is None:
        return None
    return _clamp(parsed, 0.0, 1.0)

def _is_official_source(source: object) -> bool:
    token = str(source or "").strip().lower()
    if not token:
        return False
    return "score_breakdown" in token or token.startswith("tba_scorebreakdown")

def _is_video_source(source: object) -> bool:
    token = str(source or "").strip().lower()
    if not token or _is_official_source(token):
        return False
    if token.startswith("video_"):
        return True
    if token in {"motion_v1", "yolo_bytetrack"}:
        return True
    return ("yolo" in token) or ("bytetrack" in token)

def _finding_source_bucket(finding: models.TeamMatchFinding) -> str:
    source = str(finding.source or "").strip().lower()
    summary = finding.summary if isinstance(finding.summary, dict) else {}
    if _is_official_source(source) or bool(summary.get("official_score_breakdown")):
        return "official_score_breakdown"
    if _is_video_source(source):
        return "video_analyzed"

    analysis_context = summary.get("analysis_context") if isinstance(summary.get("analysis_context"), dict) else {}
    tracking_backend = str(analysis_context.get("tracking_backend") or "").strip().lower()
    if tracking_backend and not _is_official_source(tracking_backend):
        return "video_analyzed"
    return "other"

def _redis_client() -> redis.Redis | None:
    try:
        return redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return None

def persist_last_climb_integrity_result(result: dict[str, Any]) -> None:
    client = _redis_client()
    if client is None:
        return
    try:
        client.set(CLIMB_AUDIT_LAST_RESULT_KEY, json.dumps(result), ex=CLIMB_AUDIT_REDIS_TTL_SEC)
    except Exception as exc:  # pragma: no cover - defensive telemetry path
        logger.warning("Unable to persist climb integrity result: %s", exc)

def load_last_climb_integrity_result() -> dict[str, Any] | None:
    client = _redis_client()
    if client is None:
        return None
    try:
        raw = client.get(CLIMB_AUDIT_LAST_RESULT_KEY)
    except Exception as exc:  # pragma: no cover - defensive telemetry path
        logger.warning("Unable to load climb integrity result: %s", exc)
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None

def run_climb_integrity_audit(
    db: Session,
    *,
    lookback_days: int | None = None,
    sample_limit: int | None = None,
    diff_threshold: float | None = None,
) -> dict[str, Any]:
    days = max(1, int(lookback_days or settings.climb_integrity_audit_lookback_days))
    limit = max(100, int(sample_limit or settings.climb_integrity_audit_sample_limit))
    threshold = float(diff_threshold if diff_threshold is not None else settings.climb_integrity_audit_diff_threshold)
    threshold = max(0.05, min(1.0, threshold))

    now_utc = datetime.now(timezone.utc)
    since = now_utc - timedelta(days=days)

    finding_rows = (
        db.query(models.TeamMatchFinding)
        .filter(models.TeamMatchFinding.created_at >= since)
        .order_by(models.TeamMatchFinding.created_at.desc(), models.TeamMatchFinding.id.desc())
        .limit(limit)
        .all()
    )

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    source_counts: dict[str, int] = defaultdict(int)
    samples_by_source: dict[str, int] = defaultdict(int)

    for finding in finding_rows:
        event_key = str(finding.event_key or "").strip().lower()
        match_key = str(finding.match_key or "").strip().lower()
        team_key = str(finding.team_key or "").strip().lower()
        if not event_key or not match_key or not team_key:
            continue

        source_bucket = _finding_source_bucket(finding)
        source_counts[source_bucket] += 1
        climb_value = _as_float_0_1(finding.climb_success_prob)
        if climb_value is not None:
            samples_by_source[source_bucket] += 1

        key = (event_key, match_key, team_key)
        row = grouped.get(key)
        if row is None:
            row = {
                "event_key": event_key,
                "match_key": match_key,
                "team_key": team_key,
                "official_values": [],
                "video_values": [],
            }
            grouped[key] = row
        if climb_value is None:
            continue
        if source_bucket == "official_score_breakdown":
            row["official_values"].append(climb_value)
        elif source_bucket == "video_analyzed":
            row["video_values"].append(climb_value)

    compared_pairs = 0
    missing_official = 0
    missing_video = 0
    mismatched = 0
    mismatches: list[dict[str, Any]] = []

    for row in grouped.values():
        official_values = row["official_values"]
        video_values = row["video_values"]
        official_avg = (
            sum(float(value) for value in official_values) / float(len(official_values))
            if official_values
            else None
        )
        video_avg = (
            sum(float(value) for value in video_values) / float(len(video_values))
            if video_values
            else None
        )

        if official_avg is None and video_avg is None:
            continue
        if official_avg is None:
            missing_official += 1
            continue
        if video_avg is None:
            missing_video += 1
            continue

        compared_pairs += 1
        delta = abs(float(official_avg) - float(video_avg))
        if delta >= threshold:
            mismatched += 1
            mismatches.append(
                {
                    "event_key": row["event_key"],
                    "match_key": row["match_key"],
                    "team_key": row["team_key"],
                    "official_climb_success_prob": round(float(official_avg), 3),
                    "video_climb_success_prob": round(float(video_avg), 3),
                    "delta": round(float(delta), 3),
                }
            )

    mismatches.sort(key=lambda item: float(item["delta"]), reverse=True)
    mismatch_rate = (mismatched / compared_pairs) if compared_pairs > 0 else None

    severity = "ok"
    if isinstance(mismatch_rate, float) and mismatch_rate >= 0.25:
        severity = "critical"
    elif isinstance(mismatch_rate, float) and mismatch_rate >= 0.12:
        severity = "warning"

    result = {
        "ok": True,
        "generated_at": now_utc.isoformat(),
        "window": {
            "lookback_days": days,
            "since": since.isoformat(),
            "sample_limit": limit,
            "delta_threshold": round(threshold, 3),
        },
        "totals": {
            "rows_scanned": len(finding_rows),
            "grouped_team_matches": len(grouped),
            "compared_pairs": int(compared_pairs),
            "mismatched_pairs": int(mismatched),
            "mismatch_rate": round(mismatch_rate, 4) if isinstance(mismatch_rate, float) else None,
            "missing_official_pairs": int(missing_official),
            "missing_video_pairs": int(missing_video),
        },
        "source_counts": dict(source_counts),
        "non_null_climb_samples_by_source": dict(samples_by_source),
        "severity": severity,
        "top_mismatches": mismatches[:120],
    }
    persist_last_climb_integrity_result(result)
    return result

def safe_run_climb_integrity_audit(
    db: Session,
    *,
    lookback_days: int | None = None,
    sample_limit: int | None = None,
    diff_threshold: float | None = None,
) -> dict[str, Any]:
    try:
        return run_climb_integrity_audit(
            db,
            lookback_days=lookback_days,
            sample_limit=sample_limit,
            diff_threshold=diff_threshold,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": sanitize_external_error(exc, default="climb_integrity_audit_failed"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
