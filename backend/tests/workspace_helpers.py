# Seeds a team workspace with one member and returns the headers that act as
# that member, for tests of routes scoped to a workspace.
from __future__ import annotations

import secrets

from sqlalchemy.orm import Session

from app.core.security import WORKSPACE_ACCESS_HEADER, issue_workspace_access_token
from app.db import models
from app.services.workspaces import ROLE_LEADER, generate_join_code, hash_join_code


def seed_workspace(
    db: Session,
    *,
    name: str = "Test Team",
    display_name: str = "Lead",
    role: str = ROLE_LEADER,
) -> tuple[models.TeamWorkspace, models.TeamWorkspaceMember, dict[str, str]]:
    workspace = models.TeamWorkspace(name=name, join_code_hash=hash_join_code(generate_join_code()))
    db.add(workspace)
    db.flush()
    member = add_seeded_member(db, workspace, display_name=display_name, role=role)
    db.commit()
    return workspace, member, headers_for(member)


def add_seeded_member(
    db: Session,
    workspace: models.TeamWorkspace,
    *,
    display_name: str,
    role: str = "member",
) -> models.TeamWorkspaceMember:
    member = models.TeamWorkspaceMember(
        workspace_id=workspace.id,
        member_key=secrets.token_hex(16),
        display_name=display_name,
        role=role,
    )
    db.add(member)
    db.flush()
    return member


def headers_for(member: models.TeamWorkspaceMember) -> dict[str, str]:
    token = issue_workspace_access_token(workspace_id=member.workspace_id, member_key=member.member_key)
    return {WORKSPACE_ACCESS_HEADER: token["token"]}
