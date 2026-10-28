# A team's matches at an event as an iCalendar feed (RFC 5545), for "Add our matches to my
# calendar". Subscribed calendars re-read it, so a field running late moves the events: each
# match keeps one UID and starts at TBA's predicted time once there is one.
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from app.services.utils import COMP_LEVEL_ORDER

MATCH_MINUTES = 10
_LEVEL_NAMES = {"qm": "Qual", "ef": "Eighths", "qf": "Quarters", "sf": "Semis", "f": "Final"}


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    # Content lines are at most 75 octets; longer ones continue on lines starting with a space.
    out, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            out.append(current)
            current = " " + char
        else:
            current += char
    out.append(current)
    return out


def _stamp(unix: int) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _label(comp_level: str, set_number: int, match_number: int) -> str:
    level = comp_level.lower()
    name = _LEVEL_NAMES.get(level, level.upper())
    return f"{name} {match_number}" if level in ("qm", "f") else f"{name} {set_number}-{match_number}"


def team_calendar(
    *,
    event_key: str,
    event_name: str,
    team_key: str,
    matches: Iterable[tuple[object, str, list[str], list[str]]],
    now: int,
) -> str:
    # `matches`: (Match row, this team's alliance, its partners, its opponents).
    number = team_key.removeprefix("frc")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//FRCMOB//Team matches//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(f'{number} at {event_name}')}",
        "X-PUBLISHED-TTL:PT15M",
        "REFRESH-INTERVAL;VALUE=DURATION:PT15M",
    ]
    rows = sorted(matches, key=lambda row: (COMP_LEVEL_ORDER.get(str(row[0].comp_level).lower(), 9), row[0].set_number, row[0].match_number))
    for match, alliance, partners, opponents in rows:
        start = match.actual_time or match.predicted_time or match.time
        if not start:
            continue
        label = _label(str(match.comp_level), int(match.set_number), int(match.match_number))
        partner_text = ", ".join(t.removeprefix("frc") for t in partners)
        opponent_text = ", ".join(t.removeprefix("frc") for t in opponents)
        summary = f"{number} · {label} · {alliance.capitalize()}"
        description = (
            f"With {partner_text or 'TBD'} vs {opponent_text or 'TBD'}.\n"
            "Times are estimates and move as the field runs early or late."
        )
        lines += [
            "BEGIN:VEVENT",
            f"UID:{match.match_key}-{team_key}@frcmob",
            f"DTSTAMP:{_stamp(now)}",
            f"DTSTART:{_stamp(int(start))}",
            f"DTEND:{_stamp(int(start) + MATCH_MINUTES * 60)}",
            f"SUMMARY:{_escape(summary)}",
            f"DESCRIPTION:{_escape(description)}",
            f"LOCATION:{_escape(event_name)}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(folded for line in lines for folded in _fold(line)) + "\r\n"
