# API routes for human scouting coverage, quality, and leaderboard insights.
#
# Everything here is computed from scouting room entries + the event match
# schedule — no CV pipeline involvement. Read-only.

import logging
import statistics
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes_scouting_rooms import _serialize_entry
from app.db import models
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scouting/insights", tags=["scouting-insights"])

_COMP_LEVEL_ORDER = {"qm": 0, "ef": 1, "qf": 2, "sf": 3, "f": 4}

def _match_sort_key(match: models.Match) -> tuple[int, int, int]:
    return (
        _COMP_LEVEL_ORDER.get(str(match.comp_level or "qm").lower(), 9),
        int(match.set_number or 0),
        int(match.match_number or 0),
    )

def _match_label(match: models.Match) -> str:
    comp = str(match.comp_level or "qm").lower()
    if comp == "qm":
        return f"Q{match.match_number}"
    if comp == "sf":
        return f"SF{match.set_number}"
    if comp == "f":
        return f"F{match.match_number}"
    return f"{comp.upper()}{match.set_number}-{match.match_number}"

def _entry_total_scored(payload: dict[str, Any]) -> float | None:
    form = payload.get("form")
    if not isinstance(form, dict):
        return None
    total = 0.0
    found = False
    for field in ("auto_scored", "teleop_scored"):
        value = form.get(field)
        if isinstance(value, (int, float)):
            total += float(value)
            found = True
    return total if found else None

@router.get("/entries-export")
def export_event_entries(
    event_key: str = Query(..., max_length=48),
    limit: int = Query(default=5000, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    # All scouting entries for an event across every room, newest first.
    normalized = str(event_key or "").strip().lower()
    rows = db.execute(
        select(models.ScoutingRoomEntry)
        .where(models.ScoutingRoomEntry.event_key == normalized)
        .order_by(models.ScoutingRoomEntry.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return {
        "ok": True,
        "event_key": normalized,
        "count": len(rows),
        "entries": [_serialize_entry(row) for row in rows],
    }

@router.get("/coverage")
def get_event_coverage(
    event_key: str = Query(..., max_length=48),
    db: Session = Depends(get_db),
):
    normalized = str(event_key or "").strip().lower()

    matches = db.execute(
        select(models.Match).where(models.Match.event_key == normalized)
    ).scalars().all()
    matches.sort(key=_match_sort_key)

    match_team_rows = db.execute(
        select(models.MatchTeam).where(models.MatchTeam.event_key == normalized)
    ).scalars().all()
    slots_by_match: dict[str, list[models.MatchTeam]] = defaultdict(list)
    for row in match_team_rows:
        slots_by_match[row.match_key].append(row)

    entries = db.execute(
        select(models.ScoutingRoomEntry).where(
            models.ScoutingRoomEntry.event_key == normalized
        )
    ).scalars().all()

    # (match_key, team_key) -> entry rows
    entries_by_slot: dict[tuple[str, str], list[models.ScoutingRoomEntry]] = defaultdict(list)
    # scout -> stats accumulation
    scout_entries: dict[str, list[models.ScoutingRoomEntry]] = defaultdict(list)
    for entry in entries:
        match_key = str(entry.match_key or "").strip().lower()
        team_key = str(entry.team_key or "").strip().lower()
        if match_key and team_key:
            entries_by_slot[(match_key, team_key)].append(entry)
        profile = str(entry.scout_profile or "").strip()
        if profile:
            scout_entries[profile].append(entry)

    # ── Coverage grid ──────────────────────────────────────────
    grid: list[dict[str, Any]] = []
    covered_slots = 0
    total_slots = 0
    for match in matches:
        slots = sorted(
            slots_by_match.get(match.match_key, []),
            key=lambda slot: (str(slot.alliance or ""), str(slot.station or "")),
        )
        slot_payload = []
        for slot in slots:
            slot_entries = entries_by_slot.get(
                (match.match_key, str(slot.team_key or "").strip().lower()), []
            )
            scouts = sorted({str(e.scout_profile or "").strip() for e in slot_entries if e.scout_profile})
            total_slots += 1
            if slot_entries:
                covered_slots += 1
            slot_payload.append(
                {
                    "team_key": slot.team_key,
                    "alliance": slot.alliance,
                    "station": slot.station,
                    "entry_count": len(slot_entries),
                    "scouts": scouts,
                }
            )
        grid.append(
            {
                "match_key": match.match_key,
                "label": _match_label(match),
                "comp_level": match.comp_level,
                "time": match.time,
                "slots": slot_payload,
            }
        )

    # ── Scout leaderboard ──────────────────────────────────────
    qual_match_order = [
        match.match_key
        for match in matches
        if str(match.comp_level or "").lower() == "qm"
    ]
    qual_index = {match_key: idx for idx, match_key in enumerate(qual_match_order)}

    leaderboard: list[dict[str, Any]] = []
    for profile, profile_entries in scout_entries.items():
        match_keys = {
            str(e.match_key or "").strip().lower() for e in profile_entries if e.match_key
        }
        team_keys = {
            str(e.team_key or "").strip().lower() for e in profile_entries if e.team_key
        }
        covered_qual_indexes = sorted(
            qual_index[mk] for mk in match_keys if mk in qual_index
        )
        # Longest run of consecutive qual matches with at least one entry.
        best_streak = 0
        current = 0
        previous = None
        for idx in covered_qual_indexes:
            current = current + 1 if previous is not None and idx == previous + 1 else 1
            best_streak = max(best_streak, current)
            previous = idx
        last_created = max(
            (e.created_at for e in profile_entries if e.created_at), default=None
        )
        leaderboard.append(
            {
                "scout_profile": profile,
                "entry_count": len(profile_entries),
                "matches_covered": len(match_keys),
                "teams_covered": len(team_keys),
                "best_qual_streak": best_streak,
                "last_entry_at": last_created.isoformat() if last_created else None,
            }
        )
    leaderboard.sort(key=lambda row: (-row["entry_count"], row["scout_profile"]))

    # ── Outlier detection ──────────────────────────────────────
    # 1) Disagreement: multiple entries on the same slot with very different
    #    scored totals. 2) Anomaly: an entry far from that team's own median.
    outliers: list[dict[str, Any]] = []

    team_totals: dict[str, list[float]] = defaultdict(list)
    for (match_key, team_key), slot_entries in entries_by_slot.items():
        for entry in slot_entries:
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            total = _entry_total_scored(payload)
            if total is not None:
                team_totals[team_key].append(total)

    for (match_key, team_key), slot_entries in entries_by_slot.items():
        totals: list[tuple[models.ScoutingRoomEntry, float]] = []
        for entry in slot_entries:
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            total = _entry_total_scored(payload)
            if total is not None:
                totals.append((entry, total))

        if len(totals) >= 2:
            values = [value for _, value in totals]
            spread = max(values) - min(values)
            if spread > max(4.0, 0.5 * max(values)):
                outliers.append(
                    {
                        "kind": "scout_disagreement",
                        "match_key": match_key,
                        "team_key": team_key,
                        "detail": f"Scouts disagree on scored game pieces ({min(values):.0f} vs {max(values):.0f}).",
                        "scouts": sorted(
                            {str(e.scout_profile or "") for e, _ in totals if e.scout_profile}
                        ),
                    }
                )

        history = team_totals.get(team_key, [])
        if len(history) >= 4:
            median = statistics.median(history)
            deviations = [abs(v - median) for v in history]
            mad = statistics.median(deviations) or 1.0
            for entry, value in totals:
                if abs(value - median) > max(5.0, 3.0 * mad):
                    outliers.append(
                        {
                            "kind": "anomalous_entry",
                            "match_key": match_key,
                            "team_key": team_key,
                            "detail": (
                                f"Entry reports {value:.0f} scored, far from this team's median of {median:.0f}."
                            ),
                            "scouts": [str(entry.scout_profile or "")],
                        }
                    )

    return {
        "ok": True,
        "event_key": normalized,
        "summary": {
            "total_slots": total_slots,
            "covered_slots": covered_slots,
            "coverage_pct": round(100.0 * covered_slots / total_slots, 1) if total_slots else 0.0,
            "total_entries": len(entries),
            "scout_count": len(scout_entries),
            "outlier_count": len(outliers),
        },
        "grid": grid,
        "leaderboard": leaderboard,
        "outliers": outliers[:80],
    }
