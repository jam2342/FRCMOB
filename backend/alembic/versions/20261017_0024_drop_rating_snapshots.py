"""Drop rating_snapshots.

Nothing writes or reads it since 2026-10-15: the live rating trend comes from each
rating's own history (details.rating_history). The off-site Neon copy needs the same
drop, or the hourly pg_restore --clean there fails (docs/BACKEND_HOST.md).

Revision ID: 20261017_0024
Revises: 20261016_0023
"""
from alembic import op
import sqlalchemy as sa

revision = "20261017_0024"
down_revision = "20261016_0023"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("rating_snapshots")


def downgrade():
    op.create_table(
        "rating_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_key", sa.String(), sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("team_key", sa.String(), sa.ForeignKey("teams.team_key"), nullable=False),
        sa.Column("rating_0_100", sa.Float(), nullable=False),
        sa.Column("confidence_0_1", sa.Float(), nullable=False),
        sa.Column("findings_count", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rating_snapshot_event_team_captured", "rating_snapshots", ["event_key", "team_key", "captured_at"])
    op.create_index("ix_rating_snapshot_event_captured", "rating_snapshots", ["event_key", "captured_at"])
