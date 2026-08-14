# Scheduled job: notify subscribers before their favorite teams play and
# before their scouting shifts start.
#
# Dedupe: each subscription keeps a `notified` map of alert keys it already
# received, so the tick is safe to run every minute.

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.push.sender import push_configured, send_web_push

logger = logging.getLogger(__name__)

# Never look further out than this, regardless of subscriber preferences.
MAX_LEAD_SEC = 60 * 60
# Keep only this many dedupe keys per subscription.
MAX_NOTIFIED_KEYS = 300

def _match_label(match: models.Match) -> str:
    comp = str(match.comp_level or "qm").lower()
    if comp == "qm":
        return f"Qual {match.match_number}"
    if comp == "sf":
        return f"Playoff M{match.set_number}"
    if comp == "f":
        return f"Final {match.match_number}"
    return f"{comp.upper()} {match.set_number}-{match.match_number}"

def _minutes_until(match_time: int, now_ts: int) -> int:
    return max(0, round((match_time - now_ts) / 60))

def _prune_notified(notified: dict[str, Any]) -> dict[str, Any]:
    if len(notified) <= MAX_NOTIFIED_KEYS:
        return notified
    # Keep the most recently added keys (values are ISO timestamps).
    ordered = sorted(notified.items(), key=lambda item: str(item[1]))
    return dict(ordered[-MAX_NOTIFIED_KEYS:])

def run_match_alert_tick(db: Session) -> dict[str, Any]:
    if not push_configured():
        return {"ok": True, "skipped": "push not configured"}

    now_ts = int(time.time())
    subs = db.execute(
        select(models.PushSubscription).where(
            models.PushSubscription.enabled.is_(True),
            models.PushSubscription.event_key.isnot(None),
        )
    ).scalars().all()
    if not subs:
        return {"ok": True, "subscriptions": 0, "sent": 0}

    subs_by_event: dict[str, list[models.PushSubscription]] = {}
    for sub in subs:
        event_key = str(sub.event_key or "").strip().lower()
        if event_key:
            subs_by_event.setdefault(event_key, []).append(sub)

    sent_count = 0
    for event_key, event_subs in subs_by_event.items():
        horizon_ts = now_ts + MAX_LEAD_SEC
        matches = db.execute(
            select(models.Match).where(
                models.Match.event_key == event_key,
                models.Match.time.isnot(None),
                models.Match.time >= now_ts - 300,
                models.Match.time <= horizon_ts,
            )
        ).scalars().all()
        if not matches:
            continue

        match_keys = [match.match_key for match in matches]
        match_team_rows = db.execute(
            select(models.MatchTeam).where(models.MatchTeam.match_key.in_(match_keys))
        ).scalars().all()
        teams_by_match: dict[str, list[models.MatchTeam]] = {}
        for row in match_team_rows:
            teams_by_match.setdefault(row.match_key, []).append(row)

        for sub in event_subs:
            prefs = sub.prefs if isinstance(sub.prefs, dict) else {}
            try:
                lead_minutes = int(prefs.get("match_lead_minutes") or settings.push_match_lead_minutes_default)
            except (TypeError, ValueError):
                lead_minutes = int(settings.push_match_lead_minutes_default)
            lead_sec = max(60, min(lead_minutes * 60, MAX_LEAD_SEC))
            favorite_teams = {
                str(team).strip().lower()
                for team in (sub.team_keys if isinstance(sub.team_keys, list) else [])
                if str(team).strip()
            }
            notified = dict(sub.notified) if isinstance(sub.notified, dict) else {}
            changed = False

            for match in matches:
                match_time = int(match.time or 0)
                if not (now_ts <= match_time <= now_ts + lead_sec):
                    continue
                label = _match_label(match)
                minutes = _minutes_until(match_time, now_ts)
                slots = teams_by_match.get(match.match_key, [])

                # Favorite-team alerts.
                for slot in slots:
                    team_key = str(slot.team_key or "").strip().lower()
                    if team_key not in favorite_teams:
                        continue
                    dedupe_key = f"team:{match.match_key}:{team_key}"
                    if dedupe_key in notified:
                        continue
                    team_number = team_key.removeprefix("frc")
                    ok = send_web_push(
                        db,
                        sub,
                        {
                            "title": f"Team {team_number} plays soon",
                            "body": f"{label} starts in ~{minutes} min ({slot.alliance} alliance).",
                            "tag": dedupe_key,
                            "url": f"/match-center?event={event_key}",
                        },
                    )
                    notified[dedupe_key] = now_ts
                    changed = True
                    if ok:
                        sent_count += 1

                # Scouting shift alerts.
                shift_alerts = bool(prefs.get("shift_alerts"))
                room_key = str(prefs.get("room_key") or "").strip().lower()
                scout_profile = str(prefs.get("scout_profile") or "").strip().lower()
                if shift_alerts and room_key and scout_profile:
                    dedupe_key = f"shift:{match.match_key}"
                    if dedupe_key not in notified:
                        assignment = db.execute(
                            select(models.ScoutingRoomAssignment).where(
                                models.ScoutingRoomAssignment.room_key == room_key,
                                models.ScoutingRoomAssignment.match_key == match.match_key,
                                models.ScoutingRoomAssignment.assigned_scout_profile_norm == scout_profile,
                            ).limit(1)
                        ).scalar_one_or_none()
                        if assignment is not None:
                            team_number = str(assignment.team_key or "").removeprefix("frc")
                            ok = send_web_push(
                                db,
                                sub,
                                {
                                    "title": "Scouting shift coming up",
                                    "body": f"You scout team {team_number} in {label} (~{minutes} min).",
                                    "tag": dedupe_key,
                                    "url": "/scouting/assignments",
                                },
                            )
                            notified[dedupe_key] = now_ts
                            changed = True
                            if ok:
                                sent_count += 1

            if changed:
                sub.notified = _prune_notified(notified)

    db.commit()
    return {"ok": True, "subscriptions": len(subs), "sent": sent_count}
