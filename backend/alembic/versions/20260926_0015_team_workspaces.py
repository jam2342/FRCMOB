"""Team workspaces: picklists, pit scouting and scouting rooms become private to a team.

Revision ID: 20260926_0015
Revises: 20260924_0014
"""
import hashlib
import secrets
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "20260926_0015"
down_revision = "20260924_0014"
branch_labels = None
depends_on = None

_SCOPED_TABLES = ("scouting_rooms", "event_picklists", "pit_scouting_entries")


def upgrade():
    op.create_table(
        "team_workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("frc_team_number", sa.Integer(), nullable=True),
        sa.Column("join_code_hash", sa.String(), nullable=False),
        sa.Column("join_code_rotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_team_workspaces_join_code_hash", "team_workspaces", ["join_code_hash"], unique=True)
    op.create_index("ix_team_workspaces_frc_team_number", "team_workspaces", ["frc_team_number"])
    op.create_index("ix_team_workspaces_created_at", "team_workspaces", ["created_at"])

    op.create_table(
        "team_workspace_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("team_workspaces.id"), nullable=False),
        sa.Column("member_key", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_by_member_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_team_workspace_members_workspace_id", "team_workspace_members", ["workspace_id"])
    op.create_index("ix_team_workspace_members_member_key", "team_workspace_members", ["member_key"], unique=True)
    op.create_index("ix_team_workspace_members_removed_at", "team_workspace_members", ["removed_at"])
    op.create_index(
        "ix_team_workspace_members_workspace_active",
        "team_workspace_members",
        ["workspace_id", "removed_at"],
    )

    for table in _SCOPED_TABLES:
        op.add_column(table, sa.Column("workspace_id", sa.Integer(), nullable=True))

    # Rows from before workspaces mix every team's data, so they go into one locked
    # "Legacy data" workspace that can never be joined or recovered through the
    # API; moving any of it to a real team is a deliberate database operation.
    bind = op.get_bind()
    orphaned = sum(
        int(bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE workspace_id IS NULL")).scalar() or 0)
        for table in _SCOPED_TABLES
    )
    if orphaned:
        now = datetime.now(timezone.utc)
        unknown_code_hash = hashlib.sha256(f"frcmob-legacy:{secrets.token_hex(32)}".encode()).hexdigest()
        legacy_id = bind.execute(
            sa.text(
                "INSERT INTO team_workspaces (name, join_code_hash, join_code_rotated_at, is_locked, created_at, updated_at) "
                "VALUES ('Legacy data', :hash, :now, true, :now, :now) RETURNING id"
            ),
            {"hash": unknown_code_hash, "now": now},
        ).scalar_one()
        for table in _SCOPED_TABLES:
            bind.execute(
                sa.text(f"UPDATE {table} SET workspace_id = :wid WHERE workspace_id IS NULL"),
                {"wid": legacy_id},
            )

    for table in _SCOPED_TABLES:
        op.alter_column(table, "workspace_id", existing_type=sa.Integer(), nullable=False)
        op.create_foreign_key(f"fk_{table}_workspace_id", table, "team_workspaces", ["workspace_id"], ["id"])
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])

    op.drop_index("ix_event_picklists_event_archived", table_name="event_picklists")
    op.create_index(
        "ix_event_picklists_workspace_event_archived",
        "event_picklists",
        ["workspace_id", "event_key", "archived"],
    )
    op.add_column("on_device_sessions", sa.Column("workspace_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_on_device_sessions_workspace_id", "on_device_sessions", "team_workspaces", ["workspace_id"], ["id"]
    )
    op.create_index("ix_on_device_sessions_workspace_id", "on_device_sessions", ["workspace_id"])

    op.drop_constraint("uq_pit_scouting_event_team", "pit_scouting_entries", type_="unique")
    op.create_unique_constraint(
        "uq_pit_scouting_workspace_event_team",
        "pit_scouting_entries",
        ["workspace_id", "event_key", "team_key"],
    )


def downgrade():
    # Two workspaces may both hold pit notes for one robot, which the old
    # one-per-(event, team) constraint can't represent; refuse rather than drop data.
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT event_key, team_key FROM pit_scouting_entries "
            "GROUP BY event_key, team_key HAVING count(*) > 1) d"
        )
    ).scalar()
    if duplicates:
        raise RuntimeError("Several workspaces hold pit notes for the same robot; resolve before downgrading.")
    op.drop_index("ix_on_device_sessions_workspace_id", table_name="on_device_sessions")
    op.drop_constraint("fk_on_device_sessions_workspace_id", "on_device_sessions", type_="foreignkey")
    op.drop_column("on_device_sessions", "workspace_id")
    op.drop_constraint("uq_pit_scouting_workspace_event_team", "pit_scouting_entries", type_="unique")
    op.create_unique_constraint("uq_pit_scouting_event_team", "pit_scouting_entries", ["event_key", "team_key"])
    op.drop_index("ix_event_picklists_workspace_event_archived", table_name="event_picklists")
    op.create_index("ix_event_picklists_event_archived", "event_picklists", ["event_key", "archived"])
    for table in _SCOPED_TABLES:
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_constraint(f"fk_{table}_workspace_id", table, type_="foreignkey")
        op.drop_column(table, "workspace_id")
    op.drop_table("team_workspace_members")
    op.drop_table("team_workspaces")
