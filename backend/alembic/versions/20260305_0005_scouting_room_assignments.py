# add scouting room assignment table
#
# Revision ID: 20260305_0005
# Revises: 20260225_0004
# Create Date: 2026-03-05 00:00:00.000000
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "20260305_0005"
down_revision = "20260225_0004"
branch_labels = None
depends_on = None

def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    for index in indexes:
        if str(index.get("name") or "") == index_name:
            return True
    return False

def _drop_if_exists(inspector, name: str, table: str) -> None:
    if _has_index(inspector, table, name):
        op.drop_index(name, table_name=table)

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "scouting_room_assignments" not in existing_tables:
        op.create_table(
            "scouting_room_assignments",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("room_key", sa.String(), nullable=False),
            sa.Column("event_key", sa.String(), nullable=True),
            sa.Column("match_key", sa.String(), nullable=False),
            sa.Column("team_key", sa.String(), nullable=False),
            sa.Column("assigned_scout_profile", sa.String(), nullable=False),
            sa.Column("assigned_scout_profile_norm", sa.String(), nullable=False),
            sa.Column("assigned_by_scout_profile", sa.String(), nullable=True),
            sa.Column("assigned_by_scout_profile_norm", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["room_key"], ["scouting_rooms.room_key"]),
            sa.ForeignKeyConstraint(["event_key"], ["events.event_key"]),
            sa.ForeignKeyConstraint(["match_key"], ["matches.match_key"]),
            sa.ForeignKeyConstraint(["team_key"], ["teams.team_key"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("room_key", "match_key", "team_key", name="uq_scouting_room_assignment_slot"),
        )
        existing_tables.add("scouting_room_assignments")

    if not _has_index(inspector, "scouting_room_assignments", "ix_scouting_room_assignments_room_key"):
        op.create_index(
            "ix_scouting_room_assignments_room_key",
            "scouting_room_assignments",
            ["room_key"],
            unique=False,
        )
    if not _has_index(inspector, "scouting_room_assignments", "ix_scouting_room_assignments_event_key"):
        op.create_index(
            "ix_scouting_room_assignments_event_key",
            "scouting_room_assignments",
            ["event_key"],
            unique=False,
        )
    if not _has_index(inspector, "scouting_room_assignments", "ix_scouting_room_assignments_match_key"):
        op.create_index(
            "ix_scouting_room_assignments_match_key",
            "scouting_room_assignments",
            ["match_key"],
            unique=False,
        )
    if not _has_index(inspector, "scouting_room_assignments", "ix_scouting_room_assignments_team_key"):
        op.create_index(
            "ix_scouting_room_assignments_team_key",
            "scouting_room_assignments",
            ["team_key"],
            unique=False,
        )
    if not _has_index(
        inspector,
        "scouting_room_assignments",
        "ix_scouting_room_assignments_assigned_scout_profile",
    ):
        op.create_index(
            "ix_scouting_room_assignments_assigned_scout_profile",
            "scouting_room_assignments",
            ["assigned_scout_profile"],
            unique=False,
        )
    if not _has_index(
        inspector,
        "scouting_room_assignments",
        "ix_scouting_room_assignments_assigned_scout_profile_norm",
    ):
        op.create_index(
            "ix_scouting_room_assignments_assigned_scout_profile_norm",
            "scouting_room_assignments",
            ["assigned_scout_profile_norm"],
            unique=False,
        )
    if not _has_index(
        inspector,
        "scouting_room_assignments",
        "ix_scouting_room_assignments_assigned_by_scout_profile",
    ):
        op.create_index(
            "ix_scouting_room_assignments_assigned_by_scout_profile",
            "scouting_room_assignments",
            ["assigned_by_scout_profile"],
            unique=False,
        )
    if not _has_index(
        inspector,
        "scouting_room_assignments",
        "ix_scouting_room_assignments_assigned_by_scout_profile_norm",
    ):
        op.create_index(
            "ix_scouting_room_assignments_assigned_by_scout_profile_norm",
            "scouting_room_assignments",
            ["assigned_by_scout_profile_norm"],
            unique=False,
        )
    if not _has_index(inspector, "scouting_room_assignments", "ix_scouting_room_assignments_created_at"):
        op.create_index(
            "ix_scouting_room_assignments_created_at",
            "scouting_room_assignments",
            ["created_at"],
            unique=False,
        )
    if not _has_index(inspector, "scouting_room_assignments", "ix_scouting_room_assignments_updated_at"):
        op.create_index(
            "ix_scouting_room_assignments_updated_at",
            "scouting_room_assignments",
            ["updated_at"],
            unique=False,
        )
    if not _has_index(
        inspector,
        "scouting_room_assignments",
        "ix_scouting_room_assignments_room_event",
    ):
        op.create_index(
            "ix_scouting_room_assignments_room_event",
            "scouting_room_assignments",
            ["room_key", "event_key"],
            unique=False,
        )
    if not _has_index(
        inspector,
        "scouting_room_assignments",
        "ix_scouting_room_assignments_room_assigned_norm",
    ):
        op.create_index(
            "ix_scouting_room_assignments_room_assigned_norm",
            "scouting_room_assignments",
            ["room_key", "assigned_scout_profile_norm"],
            unique=False,
        )

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "scouting_room_assignments" not in existing_tables:
        return

    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_room_assigned_norm",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_room_event",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_updated_at",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_created_at",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_assigned_by_scout_profile_norm",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_assigned_by_scout_profile",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_assigned_scout_profile_norm",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_assigned_scout_profile",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_team_key",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_match_key",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_event_key",
        "scouting_room_assignments",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_assignments_room_key",
        "scouting_room_assignments",
    )
    op.drop_table("scouting_room_assignments")
