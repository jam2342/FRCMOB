"""Frozen seasons and their archived match predictions.

frozen_seasons: finished seasons kept read-only (scripts/freeze_season.py).
archived_match_predictions: each frozen-season match's prediction as served before the
freeze, so past events keep their "called it" history without rebuilding the season.

Revision ID: 20261016_0023
Revises: 20261015_0022
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0023"
down_revision = "20261015_0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "frozen_seasons",
        sa.Column("season", sa.Integer(), primary_key=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
    )
    op.create_table(
        "archived_match_predictions",
        sa.Column("match_key", sa.String(), primary_key=True),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("prediction", sa.JSON(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_archived_match_predictions_event_key", "archived_match_predictions", ["event_key"])
    op.create_index("ix_archived_match_predictions_season", "archived_match_predictions", ["season"])


def downgrade():
    op.drop_index("ix_archived_match_predictions_season", table_name="archived_match_predictions")
    op.drop_index("ix_archived_match_predictions_event_key", table_name="archived_match_predictions")
    op.drop_table("archived_match_predictions")
    op.drop_table("frozen_seasons")
