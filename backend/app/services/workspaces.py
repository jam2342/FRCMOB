# Team workspaces: who may see and edit a team's private scouting data.
#
# A workspace is joined with a short code instead of an account, so a scout at an
# event types one code and a name and is in. Every request re-reads the member
# row, which is what makes removal permanent: a removed member's token still
# decodes but no longer resolves to anyone.
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from app.core.security import parse_workspace_access_token, workspace_access_token_from_request
from app.db import models

ROLE_LEADER = "leader"
ROLE_MEMBER = "member"
ROLES = (ROLE_LEADER, ROLE_MEMBER)
MAX_ACTIVE_MEMBERS = 200

# Crockford base32: no I, L, O or U, so a code read aloud or off a whiteboard
# survives. Ten characters is 50 bits, far beyond online guessing.
_JOIN_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_JOIN_CODE_LENGTH = 10
_JOIN_CODE_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})
_NON_CODE_CHARS = re.compile(r"[^0-9A-Z]")
_WHITESPACE = re.compile(r"\s+")

WORKSPACE_REQUIRED_DETAIL = "Join or create your team's workspace to use this."
WORKSPACE_REVOKED_DETAIL = "Your workspace access is no longer valid. Rejoin with your team's code."


@dataclass(frozen=True)
class WorkspaceActor:
    workspace: models.TeamWorkspace
    member: models.TeamWorkspaceMember

    @property
    def workspace_id(self) -> int:
        return int(self.workspace.id)

    @property
    def is_leader(self) -> bool:
        return self.member.role == ROLE_LEADER


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_join_code() -> str:
    raw = "".join(secrets.choice(_JOIN_CODE_ALPHABET) for _ in range(_JOIN_CODE_LENGTH))
    return f"{raw[:5]}-{raw[5:]}"


def normalize_join_code(raw: str | None) -> str | None:
    token = _NON_CODE_CHARS.sub("", str(raw or "").upper().translate(_JOIN_CODE_LOOKALIKES))
    if len(token) != _JOIN_CODE_LENGTH or any(ch not in _JOIN_CODE_ALPHABET for ch in token):
        return None
    return token


def hash_join_code(code: str) -> str:
    normalized = normalize_join_code(code)
    if normalized is None:
        raise ValueError("invalid join code")
    return hashlib.sha256(f"frcmob-join:{normalized}".encode("ascii")).hexdigest()


def issue_join_code(db: Session, workspace: models.TeamWorkspace) -> str:
    # Retries only guard the unique index; a 50-bit collision is not a real risk.
    for _ in range(5):
        code = generate_join_code()
        digest = hash_join_code(code)
        taken = db.execute(
            select(models.TeamWorkspace.id).where(models.TeamWorkspace.join_code_hash == digest)
        ).first()
        if taken is None:
            workspace.join_code_hash = digest
            workspace.join_code_rotated_at = utc_now()
            return code
    raise HTTPException(status_code=503, detail="Could not issue a join code; try again.")


def normalize_display_name(raw: str | None) -> str:
    name = _WHITESPACE.sub(" ", str(raw or "")).strip()[:40]
    if not name:
        raise HTTPException(status_code=422, detail="Enter your name so your team knows who you are.")
    return name


def normalize_workspace_name(raw: str | None) -> str:
    name = _WHITESPACE.sub(" ", str(raw or "")).strip()[:80]
    if not name:
        raise HTTPException(status_code=422, detail="Give the workspace a name.")
    return name


def normalize_team_number(raw: int | None) -> int | None:
    if raw is None:
        return None
    number = int(raw)
    if not 1 <= number <= 99999:
        raise HTTPException(status_code=422, detail="FRC team numbers run from 1 to 99999.")
    return number


def lock_workspace_row(db: Session, workspace_id: int) -> None:
    # Serializes everything that ties rights to member names (removal, rename,
    # role changes, room-leader promotion) within one workspace, until commit.
    db.execute(select(models.TeamWorkspace.id).where(models.TeamWorkspace.id == workspace_id).with_for_update())


def active_members(db: Session, workspace_id: int) -> list[models.TeamWorkspaceMember]:
    return list(
        db.execute(
            select(models.TeamWorkspaceMember)
            .where(
                models.TeamWorkspaceMember.workspace_id == workspace_id,
                models.TeamWorkspaceMember.removed_at.is_(None),
            )
            .order_by(models.TeamWorkspaceMember.joined_at.asc(), models.TeamWorkspaceMember.id.asc())
        ).scalars()
    )


def count_active_leaders(db: Session, workspace_id: int) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(models.TeamWorkspaceMember)
            .where(
                models.TeamWorkspaceMember.workspace_id == workspace_id,
                models.TeamWorkspaceMember.removed_at.is_(None),
                models.TeamWorkspaceMember.role == ROLE_LEADER,
            )
        ).scalar_one()
    )


def ensure_display_name_free(
    db: Session,
    workspace_id: int,
    display_name: str,
    *,
    except_member_id: int | None = None,
    joining: bool = False,
) -> None:
    # Names are how leaders tell scouts apart in assignments, so they stay unique
    # among current members (case-insensitive). A name isn't handed to whoever asks
    # with the join code, or a teammate could take over someone's assignments; a
    # scout on a new phone gets their name back once a leader removes the old one.
    wanted = display_name.casefold()
    for member in active_members(db, workspace_id):
        if member.id != except_member_id and member.display_name.casefold() == wanted:
            hint = (
                " If that's you on a new phone, ask a team leader to remove your old device on"
                " My Team, then join again with this name."
                if joining
                else " Pick another name."
            )
            raise HTTPException(
                status_code=409,
                detail=f"Someone in this workspace already goes by '{member.display_name}'.{hint}",
            )


def add_member(
    db: Session,
    workspace: models.TeamWorkspace,
    *,
    display_name: str,
    role: str,
) -> models.TeamWorkspaceMember:
    if len(active_members(db, workspace.id)) >= MAX_ACTIVE_MEMBERS:
        raise HTTPException(status_code=409, detail="This workspace is full.")
    ensure_display_name_free(db, workspace.id, display_name, joining=True)
    member = models.TeamWorkspaceMember(
        workspace_id=workspace.id,
        member_key=secrets.token_hex(16),
        display_name=display_name,
        role=role,
        joined_at=utc_now(),
        last_seen_at=utc_now(),
    )
    db.add(member)
    return member


def resolve_workspace_actor(connection: HTTPConnection, db: Session) -> WorkspaceActor | None:
    payload = parse_workspace_access_token(workspace_access_token_from_request(connection))
    if payload is None:
        return None
    member = db.execute(
        select(models.TeamWorkspaceMember).where(
            models.TeamWorkspaceMember.member_key == str(payload["mk"])
        )
    ).scalar_one_or_none()
    if member is None or member.removed_at is not None or int(member.workspace_id) != payload["wid"]:
        return None
    workspace = db.get(models.TeamWorkspace, member.workspace_id)
    if workspace is None:
        return None
    return WorkspaceActor(workspace=workspace, member=member)


def require_workspace_actor(connection: HTTPConnection, db: Session) -> WorkspaceActor:
    # 401 is reserved for workspace credentials, so the app can tell "join a
    # workspace" apart from the admin-key 403s.
    actor = resolve_workspace_actor(connection, db)
    if actor is not None:
        return actor
    presented = bool(workspace_access_token_from_request(connection))
    raise HTTPException(
        status_code=401,
        detail=WORKSPACE_REVOKED_DETAIL if presented else WORKSPACE_REQUIRED_DETAIL,
    )


def lock_workspace_actor(db: Session, actor: WorkspaceActor) -> WorkspaceActor:
    # A request can wait behind another writer after its initial authentication.
    # Refresh membership under the same lock used by removal before saving data.
    lock_workspace_row(db, actor.workspace_id)
    db.refresh(actor.member)
    if actor.member.removed_at is not None:
        raise HTTPException(status_code=401, detail=WORKSPACE_REVOKED_DETAIL)
    return actor


def require_workspace_writer(connection: HTTPConnection, db: Session) -> WorkspaceActor:
    return lock_workspace_actor(db, require_workspace_actor(connection, db))


def require_workspace_leader(connection: HTTPConnection, db: Session) -> WorkspaceActor:
    actor = require_workspace_actor(connection, db)
    if not actor.is_leader:
        raise HTTPException(status_code=403, detail="Only workspace leaders can do that.")
    return actor


def serialize_workspace(workspace: models.TeamWorkspace) -> dict:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "frc_team_number": workspace.frc_team_number,
        "join_code_rotated_at": workspace.join_code_rotated_at.isoformat() if workspace.join_code_rotated_at else None,
        "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
    }


def serialize_member(member: models.TeamWorkspaceMember) -> dict:
    return {
        "id": member.id,
        "display_name": member.display_name,
        "role": member.role,
        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        "last_seen_at": member.last_seen_at.isoformat() if member.last_seen_at else None,
    }
