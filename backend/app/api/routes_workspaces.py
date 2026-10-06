# API routes for team workspaces: create, join with a code, manage members.
#
# Writes here are exempt from the admin key (see core/security.py) because this
# is how a team gets credentials in the first place; each route does its own
# workspace checks instead.
from __future__ import annotations

from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes_scouting_rooms import (
    disconnect_member_from_workspace_rooms,
    release_scout_room_roles,
    rename_scout_in_workspace_rooms,
)
from app.core.security import issue_workspace_access_token, require_admin_access, require_write_access
from app.db import models
from app.db.session import get_db
from app.services.workspaces import (
    ROLE_LEADER,
    ROLE_MEMBER,
    ROLES,
    WorkspaceActor,
    active_members,
    add_member,
    count_active_leaders,
    ensure_display_name_free,
    hash_join_code,
    issue_join_code,
    lock_workspace_row,
    normalize_display_name,
    normalize_join_code,
    normalize_team_number,
    normalize_workspace_name,
    require_workspace_actor,
    require_workspace_leader,
    serialize_member,
    serialize_workspace,
    utc_now,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(..., max_length=80)
    frc_team_number: int | None = None
    display_name: str = Field(..., max_length=40)


class WorkspaceJoinRequest(BaseModel):
    join_code: str = Field(..., max_length=32)
    display_name: str = Field(..., max_length=40)


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    frc_team_number: int | None = None
    clear_team_number: bool = False


class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(..., max_length=40)


class MemberRemoveRequest(BaseModel):
    # Removal alone stops the member; a new code also stops them rejoining.
    rotate_join_code: bool = True


class MemberRoleRequest(BaseModel):
    role: str


class LeaveRequest(BaseModel):
    confirm_last_member: bool = False


def _session_payload(actor: WorkspaceActor, db: Session, *, join_code: str | None = None) -> dict:
    members = active_members(db, actor.workspace_id)
    payload = {
        "ok": True,
        "workspace": serialize_workspace(actor.workspace),
        "me": serialize_member(actor.member),
        "members": [serialize_member(member) for member in members],
    }
    if join_code is not None:
        payload["join_code"] = join_code
    return payload


def _with_access(actor: WorkspaceActor, payload: dict) -> dict:
    payload["access"] = issue_workspace_access_token(
        workspace_id=actor.workspace_id,
        member_key=actor.member.member_key,
    )
    return payload


def _lock_workspace(db: Session, actor: WorkspaceActor, *, require_leader: bool) -> None:
    # Serializes membership changes per workspace, so two leaders demoting or
    # removing each other at once can't both see "another leader remains". The
    # actor is re-read under the lock: a request authorized before a competing
    # change committed must not act on stale rights.
    lock_workspace_row(db, actor.workspace_id)
    db.refresh(actor.member)
    if actor.member.removed_at is not None:
        raise HTTPException(status_code=401, detail="You're no longer in this workspace.")
    if require_leader and actor.member.role != ROLE_LEADER:
        raise HTTPException(status_code=403, detail="Only workspace leaders can do that.")


def _load_member_in_workspace(db: Session, actor: WorkspaceActor, member_id: int) -> models.TeamWorkspaceMember:
    member = db.get(models.TeamWorkspaceMember, member_id)
    if member is None or member.workspace_id != actor.workspace_id or member.removed_at is not None:
        raise HTTPException(status_code=404, detail="That member isn't in this workspace.")
    return member


@router.post("")
def create_workspace(body: WorkspaceCreateRequest, db: Session = Depends(get_db)):
    require_write_access("Creating a workspace")
    workspace = models.TeamWorkspace(
        name=normalize_workspace_name(body.name),
        frc_team_number=normalize_team_number(body.frc_team_number),
        join_code_hash="",
    )
    join_code = issue_join_code(db, workspace)
    db.add(workspace)
    db.flush()
    member = add_member(db, workspace, display_name=normalize_display_name(body.display_name), role=ROLE_LEADER)
    db.commit()
    actor = WorkspaceActor(workspace=workspace, member=member)
    return _with_access(actor, _session_payload(actor, db, join_code=join_code))


@router.post("/join")
def join_workspace(body: WorkspaceJoinRequest, db: Session = Depends(get_db)):
    require_write_access("Joining a workspace")
    normalized = normalize_join_code(body.join_code)
    workspace = None
    if normalized is not None:
        workspace = db.execute(
            select(models.TeamWorkspace)
            .where(models.TeamWorkspace.join_code_hash == hash_join_code(normalized))
            .with_for_update()
        ).scalar_one_or_none()
    if workspace is None or workspace.is_locked:
        raise HTTPException(status_code=404, detail="No workspace uses that code. Check it with your team lead.")
    # A workspace left without leaders (after an admin recovery) is claimed by
    # whoever joins next, or nobody could ever manage it again.
    role = ROLE_MEMBER if count_active_leaders(db, workspace.id) else ROLE_LEADER
    member = add_member(db, workspace, display_name=normalize_display_name(body.display_name), role=role)
    db.commit()
    actor = WorkspaceActor(workspace=workspace, member=member)
    return _with_access(actor, _session_payload(actor, db))


@router.get("/me")
def get_my_workspace(request: Request, db: Session = Depends(get_db)):
    actor = require_workspace_actor(request, db)
    # The Team page polls this; a write every poll costs a cross-country commit.
    # "Seen" only needs minute-level freshness.
    last_seen = actor.member.last_seen_at
    if last_seen is not None and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    if last_seen is None or utc_now() - last_seen > timedelta(minutes=5):
        actor.member.last_seen_at = utc_now()
        db.commit()
    return _session_payload(actor, db)


@router.patch("/me")
def update_workspace(body: WorkspaceUpdateRequest, request: Request, db: Session = Depends(get_db)):
    require_write_access("Editing a workspace")
    actor = require_workspace_leader(request, db)
    if body.name is not None:
        actor.workspace.name = normalize_workspace_name(body.name)
    if body.clear_team_number:
        actor.workspace.frc_team_number = None
    elif body.frc_team_number is not None:
        actor.workspace.frc_team_number = normalize_team_number(body.frc_team_number)
    db.commit()
    return _session_payload(actor, db)


@router.patch("/me/profile")
def update_my_profile(body: ProfileUpdateRequest, request: Request, db: Session = Depends(get_db)):
    require_write_access("Renaming yourself")
    actor = require_workspace_actor(request, db)
    display_name = normalize_display_name(body.display_name)
    # Under the lock, so a rename can't slip past a concurrent removal that just
    # released room rights held under a name.
    _lock_workspace(db, actor, require_leader=False)
    ensure_display_name_free(db, actor.workspace_id, display_name, except_member_id=actor.member.id)
    # Rooms name scouts by display name (ownership, assignments, entries), so the
    # rename follows the member through the team's rooms.
    rename_scout_in_workspace_rooms(
        db, actor.workspace_id, actor.member.display_name, display_name, member_id=actor.member.id,
    )
    actor.member.display_name = display_name
    db.commit()
    return _session_payload(actor, db)


@router.post("/me/join-code")
def rotate_join_code(request: Request, db: Session = Depends(get_db)):
    require_write_access("Changing the join code")
    actor = require_workspace_leader(request, db)
    join_code = issue_join_code(db, actor.workspace)
    db.commit()
    return _session_payload(actor, db, join_code=join_code)


@router.post("/me/members/{member_id}/remove")
async def remove_member(member_id: int, body: MemberRemoveRequest, request: Request, db: Session = Depends(get_db)):
    require_write_access("Removing a member")
    actor = require_workspace_leader(request, db)
    if member_id == actor.member.id:
        raise HTTPException(status_code=400, detail="Use Leave workspace to remove yourself.")
    _lock_workspace(db, actor, require_leader=True)
    member = _load_member_in_workspace(db, actor, member_id)
    if member.role == ROLE_LEADER and count_active_leaders(db, actor.workspace_id) <= 1:
        raise HTTPException(status_code=409, detail="A workspace needs at least one leader.")
    member.removed_at = utc_now()
    member.removed_by_member_id = actor.member.id
    release_scout_room_roles(db, actor.workspace_id, member.display_name)
    join_code = issue_join_code(db, actor.workspace) if body.rotate_join_code else None
    db.commit()
    # Open room sockets stop at once, not at the member's next heartbeat.
    await disconnect_member_from_workspace_rooms(db, actor.workspace_id, member.display_name)
    return _session_payload(actor, db, join_code=join_code)


@router.post("/me/members/{member_id}/role")
def set_member_role(member_id: int, body: MemberRoleRequest, request: Request, db: Session = Depends(get_db)):
    require_write_access("Changing a member's role")
    actor = require_workspace_leader(request, db)
    role = str(body.role or "").strip().lower()
    if role not in ROLES:
        raise HTTPException(status_code=422, detail="Role must be leader or member.")
    _lock_workspace(db, actor, require_leader=True)
    member = _load_member_in_workspace(db, actor, member_id)
    if member.role == ROLE_LEADER and role != ROLE_LEADER and count_active_leaders(db, actor.workspace_id) <= 1:
        raise HTTPException(status_code=409, detail="A workspace needs at least one leader.")
    member.role = role
    db.commit()
    return _session_payload(actor, db)


@router.post("/me/leave")
async def leave_workspace(body: LeaveRequest, request: Request, db: Session = Depends(get_db)):
    require_write_access("Leaving a workspace")
    actor = require_workspace_actor(request, db)
    _lock_workspace(db, actor, require_leader=False)
    others = [member for member in active_members(db, actor.workspace_id) if member.id != actor.member.id]
    if not others and not body.confirm_last_member:
        raise HTTPException(
            status_code=409,
            detail="You're the last member. Leaving locks this workspace's picklists, pit notes and rooms for good.",
        )
    if actor.is_leader and others and count_active_leaders(db, actor.workspace_id) <= 1:
        raise HTTPException(status_code=409, detail="Make someone else a leader before you leave.")
    actor.member.removed_at = utc_now()
    actor.member.removed_by_member_id = actor.member.id
    release_scout_room_roles(db, actor.workspace_id, actor.member.display_name)
    if not others:
        # The warning promises this: without it, anyone holding the old code could
        # join and be promoted to leader of every picklist, pit note and room here.
        actor.workspace.is_locked = True
        issue_join_code(db, actor.workspace)
    db.commit()
    await disconnect_member_from_workspace_rooms(db, actor.workspace_id, actor.member.display_name)
    return {"ok": True, "left_workspace_id": actor.workspace_id, "locked": not others}


@router.get("/admin/list")
def admin_list_workspaces(request: Request, db: Session = Depends(get_db)):
    # Names and sizes only: operators can support teams without reading their data.
    require_admin_access(request, "Workspace list")
    counts = dict(
        db.execute(
            select(models.TeamWorkspaceMember.workspace_id, func.count())
            .where(models.TeamWorkspaceMember.removed_at.is_(None))
            .group_by(models.TeamWorkspaceMember.workspace_id)
        ).all()
    )
    rows = db.execute(select(models.TeamWorkspace).order_by(models.TeamWorkspace.created_at.desc())).scalars()
    return {
        "ok": True,
        "workspaces": [serialize_workspace(row) | {"active_members": int(counts.get(row.id, 0))} for row in rows],
    }


@router.post("/admin/{workspace_id}/join-code")
def admin_rotate_join_code(workspace_id: int, request: Request, db: Session = Depends(get_db)):
    # Recovery for a workspace whose leaders are all gone.
    require_admin_access(request, "Workspace join code recovery")
    require_write_access("Workspace join code recovery")
    workspace = db.get(models.TeamWorkspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if workspace.is_locked:
        raise HTTPException(status_code=409, detail="This workspace is locked; its rows must be reassigned in the database.")
    join_code = issue_join_code(db, workspace)
    db.commit()
    return {"ok": True, "workspace": serialize_workspace(workspace), "join_code": join_code}
