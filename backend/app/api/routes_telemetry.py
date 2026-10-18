# Anonymous page-speed timings from real visitors (web vitals).
#
# Only counts are kept: per day, a histogram bucket per (route pattern, metric,
# device class, connection, display mode). No IPs, keys, workspaces or anything
# else that points at a person; the per-minute rate limit holds a salted hash of
# the address for 60 s and nothing more.

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from app.core.config import settings
from app.core.security import require_admin_access
from app.services.cache import get_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

MAX_BODY_BYTES = 16_000
MAX_SAMPLES_PER_BEACON = 50
MAX_BEACONS_PER_MINUTE = 30
# Distinct (route, metric, device, ...) cells a day may hold. Real traffic fills a few
# hundred; the cap keeps made-up routes from growing Redis (noeviction) without end.
MAX_FIELDS_PER_DAY = 5000
RETENTION_DAYS = 35
KEY_PREFIX = "vitals:"

# Bucket upper edges. A sample lands in the first bucket whose edge is >= its value.
MS_EDGES = (100, 200, 300, 500, 800, 1000, 1500, 2000, 2500, 3000, 4000, 5000, 7000, 10000, 15000, 20000, 30000, 60000)
CLS_EDGES = (0.01, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0, 2.0)
METRICS = {
    "LCP": MS_EDGES,
    "FCP": MS_EDGES,
    "TTFB": MS_EDGES,
    "INP": MS_EDGES,
    # Time to first data: route opened -> its first API answer arrived.
    "TTFD": MS_EDGES,
    "CLS": CLS_EDGES,
}
DEVICES = {"phone", "tablet", "desktop"}
CONNECTIONS = {"slow-2g", "2g", "3g", "4g", "unknown"}
DISPLAYS = {"browser", "standalone", "native"}
SOURCES = {"network", "saved", "unknown"}
# Route patterns as the app reports them: /team-center, /match-center/predictions.
_ROUTE_RE = re.compile(r"^/[a-z0-9-]{0,40}(?:/[a-z0-9-]{1,40}){0,2}$")


def _bucket(value: float, edges: tuple[float, ...]) -> str:
    for edge in edges:
        if value <= edge:
            return f"{edge:g}"
    return "inf"


def _client_fingerprint(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    address = forwarded or (request.client.host if request.client else "")
    salt = str(getattr(settings, "admin_session_token_secret", "") or "frcmob-vitals")
    return hashlib.sha256(f"{salt}|{address}".encode("utf-8")).hexdigest()[:24]


def _clean_sample(raw: Any) -> tuple[str, str] | None:
    if not isinstance(raw, dict):
        return None
    metric = str(raw.get("metric") or "")
    edges = METRICS.get(metric)
    route = str(raw.get("route") or "")
    device = str(raw.get("device") or "")
    connection = str(raw.get("connection") or "unknown")
    display = str(raw.get("display") or "browser")
    source = str(raw.get("source") or "unknown")
    try:
        value = float(raw.get("value"))
    except (TypeError, ValueError):
        return None
    if edges is None or not _ROUTE_RE.match(route) or device not in DEVICES:
        return None
    if connection not in CONNECTIONS or display not in DISPLAYS or source not in SOURCES:
        return None
    upper = 10.0 if metric == "CLS" else 120_000.0
    if not math.isfinite(value) or value < 0 or value > upper:
        return None
    field = "|".join((route, metric, device, connection, display, source))
    return field, _bucket(value, edges)


@router.post("/vitals", status_code=204)
async def record_vitals(request: Request) -> Response:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Too much data.")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Send JSON.")
    samples = payload.get("samples") if isinstance(payload, dict) else None
    if not isinstance(samples, list) or not samples:
        raise HTTPException(status_code=400, detail="No samples.")

    client = get_cache().redis_client
    if client is None:
        return Response(status_code=204)
    try:
        limit_key = f"{KEY_PREFIX}rl:{_client_fingerprint(request)}"
        count = client.incr(limit_key)
        if count == 1:
            client.expire(limit_key, 60)
        if count > MAX_BEACONS_PER_MINUTE:
            return Response(status_code=204)
        day_key = f"{KEY_PREFIX}{datetime.now(timezone.utc):%Y%m%d}"
        fields = []
        for raw in samples[:MAX_SAMPLES_PER_BEACON]:
            cleaned = _clean_sample(raw)
            if cleaned is not None:
                fields.append(f"{cleaned[0]}|{cleaned[1]}")
        if fields and int(client.hlen(day_key) or 0) >= MAX_FIELDS_PER_DAY:
            # Full for today: keep counting cells that exist, add no new ones.
            check = client.pipeline()
            for field in fields:
                check.hexists(day_key, field)
            fields = [field for field, exists in zip(fields, check.execute()) if exists]
        pipe = client.pipeline()
        accepted = 0
        for field in fields:
            pipe.hincrby(day_key, field, 1)
            accepted += 1
        if accepted:
            pipe.expire(day_key, RETENTION_DAYS * 86400)
            pipe.execute()
    except Exception as exc:
        logger.warning("telemetry.vitals write failed: %s", exc)
    return Response(status_code=204)


def _percentile(histogram: dict[str, int], edges: tuple[float, ...], fraction: float) -> float | None:
    total = sum(histogram.values())
    if total <= 0:
        return None
    target = total * fraction
    running = 0
    for label in [f"{edge:g}" for edge in edges] + ["inf"]:
        running += histogram.get(label, 0)
        if running >= target:
            return math.inf if label == "inf" else float(label)
    return None


def summarize_vitals(raw_days: list[dict[str, str]]) -> list[dict[str, Any]]:
    # Merges daily hashes into one histogram per (route, metric, device) and reads
    # p50/p75/p95 off it. A percentile is the bucket's upper edge, so "<= 2500 ms".
    merged: dict[tuple[str, str, str], dict[str, int]] = {}
    for day in raw_days:
        for field, count in day.items():
            parts = str(field).split("|")
            if len(parts) != 7:
                continue
            route, metric, device, _connection, _display, _source, bucket = parts
            key = (route, metric, device)
            histogram = merged.setdefault(key, {})
            histogram[bucket] = histogram.get(bucket, 0) + int(count)
    rows: list[dict[str, Any]] = []
    for (route, metric, device), histogram in sorted(merged.items()):
        edges = METRICS.get(metric)
        if edges is None:
            continue
        rows.append(
            {
                "route": route,
                "metric": metric,
                "device": device,
                "samples": sum(histogram.values()),
                "p50": _percentile(histogram, edges, 0.50),
                "p75": _percentile(histogram, edges, 0.75),
                "p95": _percentile(histogram, edges, 0.95),
            }
        )
    return rows


@router.get("/vitals/summary")
def vitals_summary(request: Request, days: int = 7) -> dict[str, Any]:
    require_admin_access(request, "Speed telemetry")
    span = max(1, min(int(days), RETENTION_DAYS))
    client = get_cache().redis_client
    if client is None:
        return {"ok": True, "days": span, "rows": []}
    today = datetime.now(timezone.utc).date()
    raw_days = []
    for offset in range(span):
        day = today - timedelta(days=offset)
        raw_days.append(client.hgetall(f"{KEY_PREFIX}{day:%Y%m%d}") or {})
    rows = summarize_vitals(raw_days)
    # JSON has no infinity: an "inf" percentile means slower than the last edge.
    for row in rows:
        for name in ("p50", "p75", "p95"):
            if row[name] == math.inf:
                row[name] = "over 60000"
    return {"ok": True, "days": span, "rows": rows}


# ── Content-Security-Policy violations ───────────────────────────
#
# The CSP is a <meta> tag (see ScoutingApp/build/contentSecurityPolicy.ts), and a
# meta policy can't send reports itself, so the page reports what the browser
# blocked here. Kept as counts per (page, directive, blocked origin) and day; no
# paths, query strings or anything about the visitor. A non-zero count after a
# deploy usually means a new outside service that the policy doesn't list yet.

CSP_KEY_PREFIX = "csp:"
CSP_RETENTION_DAYS = 14
MAX_CSP_REPORTS_PER_BEACON = 10
MAX_CSP_BEACONS_PER_MINUTE = 10
MAX_CSP_FIELDS_PER_DAY = 2000
CSP_DIRECTIVES = {
    "default-src", "script-src", "script-src-elem", "script-src-attr", "style-src", "style-src-elem",
    "style-src-attr", "img-src", "font-src", "connect-src", "media-src", "frame-src", "child-src",
    "worker-src", "manifest-src", "object-src", "base-uri", "form-action",
}
_CSP_KEYWORDS = {"inline", "eval", "wasm-eval", "data", "blob", "self"}
_CSP_ORIGIN_RE = re.compile(r"^(?:https?|wss?)://[a-z0-9.-]{1,80}(?::\d{1,5})?$")


def _clean_csp_report(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    directive = str(raw.get("directive") or "").strip().lower()
    blocked = str(raw.get("blocked") or "").strip().lower()
    page = str(raw.get("page") or "")
    if directive not in CSP_DIRECTIVES:
        return None
    if blocked not in _CSP_KEYWORDS and not _CSP_ORIGIN_RE.match(blocked):
        blocked = "other"
    if not _ROUTE_RE.match(page):
        page = "other"
    return "|".join((page, directive, blocked))


@router.post("/csp", status_code=204)
async def record_csp_violations(request: Request) -> Response:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Too much data.")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Send JSON.")
    reports = payload.get("violations") if isinstance(payload, dict) else None
    if not isinstance(reports, list) or not reports:
        raise HTTPException(status_code=400, detail="No violations.")
    client = get_cache().redis_client
    if client is None:
        return Response(status_code=204)
    try:
        limit_key = f"{CSP_KEY_PREFIX}rl:{_client_fingerprint(request)}"
        count = client.incr(limit_key)
        if count == 1:
            client.expire(limit_key, 60)
        if count > MAX_CSP_BEACONS_PER_MINUTE:
            return Response(status_code=204)
        fields = [f for f in (_clean_csp_report(raw) for raw in reports[:MAX_CSP_REPORTS_PER_BEACON]) if f]
        day_key = f"{CSP_KEY_PREFIX}{datetime.now(timezone.utc):%Y%m%d}"
        if fields and int(client.hlen(day_key) or 0) >= MAX_CSP_FIELDS_PER_DAY:
            check = client.pipeline()
            for field in fields:
                check.hexists(day_key, field)
            fields = [field for field, known in zip(fields, check.execute()) if known]
        if fields:
            pipe = client.pipeline()
            for field in fields:
                pipe.hincrby(day_key, field, 1)
            pipe.expire(day_key, CSP_RETENTION_DAYS * 86400)
            pipe.execute()
    except Exception as exc:
        logger.warning("telemetry.csp write failed: %s", exc)
    return Response(status_code=204)


def csp_violation_rows(days: int = 1) -> list[dict[str, Any]]:
    client = get_cache().redis_client
    if client is None:
        return []
    span = max(1, min(int(days), CSP_RETENTION_DAYS))
    today = datetime.now(timezone.utc).date()
    merged: dict[str, int] = {}
    for offset in range(span):
        day = client.hgetall(f"{CSP_KEY_PREFIX}{today - timedelta(days=offset):%Y%m%d}") or {}
        for field, count in day.items():
            key = field.decode() if isinstance(field, bytes) else str(field)
            merged[key] = merged.get(key, 0) + int(count)
    rows = []
    for field, count in merged.items():
        parts = field.split("|")
        if len(parts) == 3:
            rows.append({"page": parts[0], "directive": parts[1], "blocked": parts[2], "count": count})
    return sorted(rows, key=lambda row: (-row["count"], row["directive"], row["blocked"]))


@router.get("/csp/summary")
def csp_summary(request: Request, days: int = 7) -> dict[str, Any]:
    require_admin_access(request, "CSP violation reports")
    span = max(1, min(int(days), CSP_RETENTION_DAYS))
    return {"ok": True, "days": span, "rows": csp_violation_rows(span)}
