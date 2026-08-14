# add rating_snapshots time series for live rating momentum
#
# Revision ID: 20260612_0012
# Revises: 20260612_0011
# Create Date: 2026-06-12 18:30:00.000000
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260612_0012"
down_revision = "20260612_0011"
branch_labels = None
depends_on = None

def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    return any(str(index.get("name") or "") == index_name for index in indexes)

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "rating_snapshots" not in existing_tables:
        op.create_table(
            "rating_snapshots",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_key", sa.String(), nullable=False),
            sa.Column("team_key", sa.String(), nullable=False),
            sa.Column("rating_0_100", sa.Float(), nullable=False),
            sa.Column("confidence_0_1", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.Column("findings_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("model_version", sa.String(), nullable=False, server_default=sa.text("'rating_v1'")),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["event_key"], ["events.event_key"]),
            sa.ForeignKeyConstraint(["team_key"], ["teams.team_key"]),
            sa.PrimaryKeyConstraint("id"),
        )

    for name, cols in (
        ("ix_rating_snapshots_event_key", ["event_key"]),
        ("ix_rating_snapshots_team_key", ["team_key"]),
        ("ix_rating_snapshots_captured_at", ["captured_at"]),
        ("ix_rating_snapshot_event_team_captured", ["event_key", "team_key", "captured_at"]),
        ("ix_rating_snapshot_event_captured", ["event_key", "captured_at"]),
    ):
        if not _has_index(inspector, "rating_snapshots", name):
            op.create_index(name, "rating_snapshots", cols)

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for name in (
        "ix_rating_snapshot_event_captured",
        "ix_rating_snapshot_event_team_captured",
        "ix_rating_snapshots_captured_at",
        "ix_rating_snapshots_team_key",
        "ix_rating_snapshots_event_key",
    ):
        if _has_index(inspector, "rating_snapshots", name):
            op.drop_index(name, table_name="rating_snapshots")
    if "rating_snapshots" in set(inspector.get_table_names()):
        op.drop_table("rating_snapshots")
