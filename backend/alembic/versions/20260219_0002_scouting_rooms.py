# add scouting realtime room tables
#
# Revision ID: 20260219_0002
# Revises: 20260218_0001
# Create Date: 2026-02-19 00:00:00.000000
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "20260219_0002"
down_revision = "20260218_0001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    def has_index(table_name: str, index_name: str) -> bool:
        try:
            indexes = inspector.get_indexes(table_name)
        except Exception:
            return False
        for index in indexes:
            if str(index.get("name") or "") == index_name:
                return True
        return False

    if "scouting_rooms" not in existing_tables:
        op.create_table(
            "scouting_rooms",
            sa.Column("room_key", sa.String(), nullable=False),
            sa.Column("event_key", sa.String(), nullable=True),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("last_activity_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["event_key"], ["events.event_key"]),
            sa.PrimaryKeyConstraint("room_key"),
        )
        existing_tables.add("scouting_rooms")

    if not has_index("scouting_rooms", "ix_scouting_rooms_event_key"):
        op.create_index("ix_scouting_rooms_event_key", "scouting_rooms", ["event_key"], unique=False)
    if not has_index("scouting_rooms", "ix_scouting_rooms_archived"):
        op.create_index("ix_scouting_rooms_archived", "scouting_rooms", ["archived"], unique=False)
    if not has_index("scouting_rooms", "ix_scouting_rooms_created_at"):
        op.create_index("ix_scouting_rooms_created_at", "scouting_rooms", ["created_at"], unique=False)
    if not has_index("scouting_rooms", "ix_scouting_rooms_updated_at"):
        op.create_index("ix_scouting_rooms_updated_at", "scouting_rooms", ["updated_at"], unique=False)
    if not has_index("scouting_rooms", "ix_scouting_rooms_last_activity_at"):
        op.create_index("ix_scouting_rooms_last_activity_at", "scouting_rooms", ["last_activity_at"], unique=False)

    if "scouting_room_entries" not in existing_tables:
        op.create_table(
            "scouting_room_entries",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("room_key", sa.String(), nullable=False),
            sa.Column("event_key", sa.String(), nullable=True),
            sa.Column("match_key", sa.String(), nullable=True),
            sa.Column("team_key", sa.String(), nullable=True),
            sa.Column("scout_profile", sa.String(), nullable=False),
            sa.Column("client_entry_id", sa.String(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("total_points", sa.Float(), nullable=True),
            sa.Column("driver_score_0_100", sa.Float(), nullable=True),
            sa.Column("manual_rating_0_100", sa.Float(), nullable=True),
            sa.Column("scouting_api_rating_0_100", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["event_key"], ["events.event_key"]),
            sa.ForeignKeyConstraint(["match_key"], ["matches.match_key"]),
            sa.ForeignKeyConstraint(["room_key"], ["scouting_rooms.room_key"]),
            sa.ForeignKeyConstraint(["team_key"], ["teams.team_key"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("room_key", "client_entry_id", name="uq_scouting_room_entry_client"),
        )
        existing_tables.add("scouting_room_entries")

    if not has_index("scouting_room_entries", "ix_scouting_room_entries_room_key"):
        op.create_index("ix_scouting_room_entries_room_key", "scouting_room_entries", ["room_key"], unique=False)
    if not has_index("scouting_room_entries", "ix_scouting_room_entries_event_key"):
        op.create_index("ix_scouting_room_entries_event_key", "scouting_room_entries", ["event_key"], unique=False)
    if not has_index("scouting_room_entries", "ix_scouting_room_entries_match_key"):
        op.create_index("ix_scouting_room_entries_match_key", "scouting_room_entries", ["match_key"], unique=False)
    if not has_index("scouting_room_entries", "ix_scouting_room_entries_team_key"):
        op.create_index("ix_scouting_room_entries_team_key", "scouting_room_entries", ["team_key"], unique=False)
    if not has_index("scouting_room_entries", "ix_scouting_room_entries_scout_profile"):
        op.create_index("ix_scouting_room_entries_scout_profile", "scouting_room_entries", ["scout_profile"], unique=False)
    if not has_index("scouting_room_entries", "ix_scouting_room_entries_created_at"):
        op.create_index("ix_scouting_room_entries_created_at", "scouting_room_entries", ["created_at"], unique=False)
    if not has_index("scouting_room_entries", "ix_scouting_room_entries_updated_at"):
        op.create_index("ix_scouting_room_entries_updated_at", "scouting_room_entries", ["updated_at"], unique=False)
    if not has_index("scouting_room_entries", "ix_scouting_room_entries_room_created"):
        op.create_index(
            "ix_scouting_room_entries_room_created",
            "scouting_room_entries",
            ["room_key", "created_at"],
            unique=False,
        )
    if not has_index("scouting_room_entries", "ix_scouting_room_entries_room_team"):
        op.create_index(
            "ix_scouting_room_entries_room_team",
            "scouting_room_entries",
            ["room_key", "team_key"],
            unique=False,
        )

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    def has_index(table_name: str, index_name: str) -> bool:
        try:
            indexes = inspector.get_indexes(table_name)
        except Exception:
            return False
        for index in indexes:
            if str(index.get("name") or "") == index_name:
                return True
        return False

    if "scouting_room_entries" in existing_tables:
        if has_index("scouting_room_entries", "ix_scouting_room_entries_room_team"):
            op.drop_index("ix_scouting_room_entries_room_team", table_name="scouting_room_entries")
        if has_index("scouting_room_entries", "ix_scouting_room_entries_room_created"):
            op.drop_index("ix_scouting_room_entries_room_created", table_name="scouting_room_entries")
        if has_index("scouting_room_entries", "ix_scouting_room_entries_updated_at"):
            op.drop_index("ix_scouting_room_entries_updated_at", table_name="scouting_room_entries")
        if has_index("scouting_room_entries", "ix_scouting_room_entries_created_at"):
            op.drop_index("ix_scouting_room_entries_created_at", table_name="scouting_room_entries")
        if has_index("scouting_room_entries", "ix_scouting_room_entries_scout_profile"):
            op.drop_index("ix_scouting_room_entries_scout_profile", table_name="scouting_room_entries")
        if has_index("scouting_room_entries", "ix_scouting_room_entries_team_key"):
            op.drop_index("ix_scouting_room_entries_team_key", table_name="scouting_room_entries")
        if has_index("scouting_room_entries", "ix_scouting_room_entries_match_key"):
            op.drop_index("ix_scouting_room_entries_match_key", table_name="scouting_room_entries")
        if has_index("scouting_room_entries", "ix_scouting_room_entries_event_key"):
            op.drop_index("ix_scouting_room_entries_event_key", table_name="scouting_room_entries")
        if has_index("scouting_room_entries", "ix_scouting_room_entries_room_key"):
            op.drop_index("ix_scouting_room_entries_room_key", table_name="scouting_room_entries")
        op.drop_table("scouting_room_entries")

    if "scouting_rooms" in existing_tables:
        if has_index("scouting_rooms", "ix_scouting_rooms_last_activity_at"):
            op.drop_index("ix_scouting_rooms_last_activity_at", table_name="scouting_rooms")
        if has_index("scouting_rooms", "ix_scouting_rooms_updated_at"):
            op.drop_index("ix_scouting_rooms_updated_at", table_name="scouting_rooms")
        if has_index("scouting_rooms", "ix_scouting_rooms_created_at"):
            op.drop_index("ix_scouting_rooms_created_at", table_name="scouting_rooms")
        if has_index("scouting_rooms", "ix_scouting_rooms_archived"):
            op.drop_index("ix_scouting_rooms_archived", table_name="scouting_rooms")
        if has_index("scouting_rooms", "ix_scouting_rooms_event_key"):
            op.drop_index("ix_scouting_rooms_event_key", table_name="scouting_rooms")
        op.drop_table("scouting_rooms")
