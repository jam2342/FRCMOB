# add event picklists, pit scouting entries, push subscriptions
#
# Revision ID: 20260612_0011
# Revises: 20260422_0010
# Create Date: 2026-06-12 12:00:00.000000
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260612_0011"
down_revision = "20260422_0010"
branch_labels = None
depends_on = None

def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    return any(str(index.get("name") or "") == index_name for index in indexes)

def _drop_if_exists(inspector, name: str, table: str) -> None:
    if _has_index(inspector, table, name):
        op.drop_index(name, table_name=table)

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "event_picklists" not in existing_tables:
        op.create_table(
            "event_picklists",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_key", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False, server_default=sa.text("'Picklist'")),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("slots", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("live_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("event_picklists")

    if "pit_scouting_entries" not in existing_tables:
        op.create_table(
            "pit_scouting_entries",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_key", sa.String(), nullable=False),
            sa.Column("team_key", sa.String(), nullable=False),
            sa.Column("scout_profile", sa.String(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("photos", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_key", "team_key", name="uq_pit_scouting_event_team"),
        )
        existing_tables.add("pit_scouting_entries")

    if "push_subscriptions" not in existing_tables:
        op.create_table(
            "push_subscriptions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("endpoint", sa.String(), nullable=False),
            sa.Column("keys", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("event_key", sa.String(), nullable=True),
            sa.Column("team_keys", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("prefs", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("notified", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
        )
        existing_tables.add("push_subscriptions")

    inspector = inspect(bind)
    picklist_indexes = [
        ("ix_event_picklists_event_key", ["event_key"]),
        ("ix_event_picklists_archived", ["archived"]),
        ("ix_event_picklists_created_at", ["created_at"]),
        ("ix_event_picklists_updated_at", ["updated_at"]),
        ("ix_event_picklists_event_archived", ["event_key", "archived"]),
    ]
    for index_name, columns in picklist_indexes:
        if not _has_index(inspector, "event_picklists", index_name):
            op.create_index(index_name, "event_picklists", columns, unique=False)

    pit_indexes = [
        ("ix_pit_scouting_entries_event_key", ["event_key"]),
        ("ix_pit_scouting_entries_team_key", ["team_key"]),
        ("ix_pit_scouting_entries_scout_profile", ["scout_profile"]),
        ("ix_pit_scouting_entries_created_at", ["created_at"]),
        ("ix_pit_scouting_entries_updated_at", ["updated_at"]),
    ]
    for index_name, columns in pit_indexes:
        if not _has_index(inspector, "pit_scouting_entries", index_name):
            op.create_index(index_name, "pit_scouting_entries", columns, unique=False)

    push_indexes = [
        ("ix_push_subscriptions_endpoint", ["endpoint"]),
        ("ix_push_subscriptions_event_key", ["event_key"]),
        ("ix_push_subscriptions_enabled", ["enabled"]),
        ("ix_push_subscriptions_created_at", ["created_at"]),
        ("ix_push_subscriptions_updated_at", ["updated_at"]),
        ("ix_push_subscriptions_event_enabled", ["event_key", "enabled"]),
    ]
    for index_name, columns in push_indexes:
        if not _has_index(inspector, "push_subscriptions", index_name):
            op.create_index(index_name, "push_subscriptions", columns, unique=False)

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "push_subscriptions" in existing_tables:
        for index_name in (
            "ix_push_subscriptions_event_enabled",
            "ix_push_subscriptions_updated_at",
            "ix_push_subscriptions_created_at",
            "ix_push_subscriptions_enabled",
            "ix_push_subscriptions_event_key",
            "ix_push_subscriptions_endpoint",
        ):
            _drop_if_exists(inspector, index_name, "push_subscriptions")
        op.drop_table("push_subscriptions")

    if "pit_scouting_entries" in existing_tables:
        for index_name in (
            "ix_pit_scouting_entries_updated_at",
            "ix_pit_scouting_entries_created_at",
            "ix_pit_scouting_entries_scout_profile",
            "ix_pit_scouting_entries_team_key",
            "ix_pit_scouting_entries_event_key",
        ):
            _drop_if_exists(inspector, index_name, "pit_scouting_entries")
        op.drop_table("pit_scouting_entries")

    if "event_picklists" in existing_tables:
        for index_name in (
            "ix_event_picklists_event_archived",
            "ix_event_picklists_updated_at",
            "ix_event_picklists_created_at",
            "ix_event_picklists_archived",
            "ix_event_picklists_event_key",
        ):
            _drop_if_exists(inspector, index_name, "event_picklists")
        op.drop_table("event_picklists")
