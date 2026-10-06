from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.services.workspaces import WorkspaceActor, active_members


def _aware(value: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes; everything here is stored in UTC.
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def load_team_room(db: Session, workspace_id: int, event_key: str) -> models.ScoutingRoom:
    room = db.execute(select(models.ScoutingRoom).where(
        models.ScoutingRoom.workspace_id == workspace_id,
        models.ScoutingRoom.team_event_key == event_key,
    )).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Your team has no scouting room for this event yet.")
    return room


def room_assignments(db: Session, room: models.ScoutingRoom) -> list[models.ScoutingRoomAssignment]:
    return list(db.execute(select(models.ScoutingRoomAssignment).where(
        models.ScoutingRoomAssignment.room_key == room.room_key,
    ).order_by(models.ScoutingRoomAssignment.match_key, models.ScoutingRoomAssignment.team_key)).scalars())


def team_snapshot(db: Session, actor: WorkspaceActor, room: models.ScoutingRoom) -> dict:
    def member_payload(member):
        return {"member_id": member.id, "display_name": member.display_name, "role": member.role}

    members = sorted(active_members(db, actor.workspace_id), key=lambda m: (m.display_name.casefold(), m.id))
    current = {member.id: member for member in members}
    rows = room_assignments(db, room)
    # One query covers all slots, including submissions made in other keyed rooms.
    entries = db.execute(select(
        models.ScoutingRoomEntry.match_key, models.ScoutingRoomEntry.team_key,
        models.ScoutingRoomEntry.scout_profile, models.ScoutingRoomEntry.created_at,
    ).join(models.ScoutingRoom, models.ScoutingRoom.room_key == models.ScoutingRoomEntry.room_key).where(
        models.ScoutingRoom.workspace_id == actor.workspace_id,
        models.ScoutingRoomEntry.match_key.in_({row.match_key for row in rows}),
    )).all() if rows else []
    covered = {(match, team) for match, team, _, _ in entries}
    # Entries carry a name, not a member. A removed member can't write any more,
    # so anything under my name from before I joined belongs to whoever used the
    # name before me.
    joined = _aware(actor.member.joined_at)
    mine = {
        (match, team) for match, team, name, created in entries
        if name == actor.member.display_name and (joined is None or (_aware(created) or joined) >= joined)
    }
    assignments = []
    for row in rows:
        member = current.get(row.assigned_member_id)
        slot = (row.match_key, row.team_key)
        assignments.append({
            "match_key": row.match_key, "team_key": row.team_key,
            "assigned_member_id": row.assigned_member_id,
            "assigned_display_name": member.display_name if member else row.assigned_scout_profile,
            "member_active": member is not None,
            "covered": member is not None and slot in covered,
            "covered_by_me": member is not None and slot in mine,
        })
    return {
        "ok": True, "room_key": room.room_key, "event_key": room.event_key,
        "me": member_payload(actor.member), "members": [member_payload(m) for m in members],
        "assignments": assignments,
    }


def apply_assignment_changes(db: Session, actor: WorkspaceActor, room: models.ScoutingRoom, changes) -> None:
    members = {m.id: m for m in active_members(db, actor.workspace_id)}
    matches = set(db.execute(select(models.Match.match_key).where(
        models.Match.event_key == room.event_key,
        models.Match.match_key.in_({c.match_key for c in changes}),
    )).scalars())
    slots = set(db.execute(select(models.MatchTeam.match_key, models.MatchTeam.team_key).where(
        models.MatchTeam.match_key.in_(matches),
    )).all())
    invalid = []
    for change in changes:
        reasons = []
        if change.match_key not in matches:
            reasons.append("Match is not in this event.")
        elif (change.match_key, change.team_key) not in slots:
            reasons.append("Team is not in this match.")
        if change.assigned_member_id is not None and change.assigned_member_id not in members:
            reasons.append("Member is not active in this workspace.")
        if reasons:
            invalid.append({**change.model_dump(), "reasons": reasons})
    if invalid:
        raise HTTPException(status_code=422, detail=invalid)

    existing = {(row.match_key, row.team_key): row for row in room_assignments(db, room)}
    planned = {slot: row.assigned_member_id for slot, row in existing.items()}
    # Duplicate slots in a batch use the last write, just like separate requests.
    final_changes = {(c.match_key, c.team_key): c for c in changes}
    for slot, change in final_changes.items():
        if change.assigned_member_id is None:
            planned.pop(slot, None)
        else:
            planned[slot] = change.assigned_member_id
    bookings = {}
    for (match, team), member_id in planned.items():
        if member_id is not None:
            bookings.setdefault((match, member_id), []).append(team)
    conflicts = [
        {"match_key": match, "team_key": team, "assigned_member_id": member_id,
         "reasons": ["Member cannot scout more than one team in the same match."]}
        for (match, member_id), teams in bookings.items() if len(teams) > 1 for team in teams
    ]
    if conflicts:
        raise HTTPException(status_code=422, detail=conflicts)

    now = datetime.now(timezone.utc)
    for slot, change in final_changes.items():
        row = existing.get(slot)
        if change.assigned_member_id is None:
            if row is not None:
                db.delete(row)
            continue
        member = members[change.assigned_member_id]
        if row is None:
            row = models.ScoutingRoomAssignment(
                room_key=room.room_key, event_key=room.event_key, match_key=slot[0], team_key=slot[1],
            )
            db.add(row)
        row.assigned_member_id = member.id
        row.assigned_scout_profile = member.display_name
        row.assigned_scout_profile_norm = member.display_name.lower()
        row.assigned_by_scout_profile = actor.member.display_name
        row.assigned_by_scout_profile_norm = actor.member.display_name.lower()
        row.updated_at = now
    room.updated_at = room.last_activity_at = now
    db.commit()
