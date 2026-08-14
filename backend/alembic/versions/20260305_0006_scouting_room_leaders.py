# add scouting room leaders table
#
# Revision ID: 20260305_0006
# Revises: 20260305_0005
# Create Date: 2026-03-05 00:30:00.000000
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "20260305_0006"
down_revision = "20260305_0005"
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

    if "scouting_room_leaders" not in existing_tables:
        op.create_table(
            "scouting_room_leaders",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("room_key", sa.String(), nullable=False),
            sa.Column("scout_profile", sa.String(), nullable=False),
            sa.Column("scout_profile_norm", sa.String(), nullable=False),
            sa.Column("added_by_scout_profile", sa.String(), nullable=True),
            sa.Column("added_by_scout_profile_norm", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["room_key"], ["scouting_rooms.room_key"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("room_key", "scout_profile_norm", name="uq_scouting_room_leader_profile"),
        )
        existing_tables.add("scouting_room_leaders")

    if not _has_index(inspector, "scouting_room_leaders", "ix_scouting_room_leaders_room_key"):
        op.create_index(
            "ix_scouting_room_leaders_room_key",
            "scouting_room_leaders",
            ["room_key"],
            unique=False,
        )
    if not _has_index(inspector, "scouting_room_leaders", "ix_scouting_room_leaders_scout_profile"):
        op.create_index(
            "ix_scouting_room_leaders_scout_profile",
            "scouting_room_leaders",
            ["scout_profile"],
            unique=False,
        )
    if not _has_index(
        inspector,
        "scouting_room_leaders",
        "ix_scouting_room_leaders_scout_profile_norm",
    ):
        op.create_index(
            "ix_scouting_room_leaders_scout_profile_norm",
            "scouting_room_leaders",
            ["scout_profile_norm"],
            unique=False,
        )
    if not _has_index(
        inspector,
        "scouting_room_leaders",
        "ix_scouting_room_leaders_added_by_scout_profile",
    ):
        op.create_index(
            "ix_scouting_room_leaders_added_by_scout_profile",
            "scouting_room_leaders",
            ["added_by_scout_profile"],
            unique=False,
        )
    if not _has_index(
        inspector,
        "scouting_room_leaders",
        "ix_scouting_room_leaders_added_by_scout_profile_norm",
    ):
        op.create_index(
            "ix_scouting_room_leaders_added_by_scout_profile_norm",
            "scouting_room_leaders",
            ["added_by_scout_profile_norm"],
            unique=False,
        )
    if not _has_index(inspector, "scouting_room_leaders", "ix_scouting_room_leaders_created_at"):
        op.create_index(
            "ix_scouting_room_leaders_created_at",
            "scouting_room_leaders",
            ["created_at"],
            unique=False,
        )
    if not _has_index(inspector, "scouting_room_leaders", "ix_scouting_room_leaders_updated_at"):
        op.create_index(
            "ix_scouting_room_leaders_updated_at",
            "scouting_room_leaders",
            ["updated_at"],
            unique=False,
        )
    if not _has_index(
        inspector,
        "scouting_room_leaders",
        "ix_scouting_room_leaders_room_profile_norm",
    ):
        op.create_index(
            "ix_scouting_room_leaders_room_profile_norm",
            "scouting_room_leaders",
            ["room_key", "scout_profile_norm"],
            unique=False,
        )

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "scouting_room_leaders" not in existing_tables:
        return

    _drop_if_exists(
        inspector,
        "ix_scouting_room_leaders_room_profile_norm",
        "scouting_room_leaders",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_leaders_updated_at",
        "scouting_room_leaders",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_leaders_created_at",
        "scouting_room_leaders",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_leaders_added_by_scout_profile_norm",
        "scouting_room_leaders",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_leaders_added_by_scout_profile",
        "scouting_room_leaders",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_leaders_scout_profile_norm",
        "scouting_room_leaders",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_leaders_scout_profile",
        "scouting_room_leaders",
    )
    _drop_if_exists(
        inspector,
        "ix_scouting_room_leaders_room_key",
        "scouting_room_leaders",
    )
    op.drop_table("scouting_room_leaders")
