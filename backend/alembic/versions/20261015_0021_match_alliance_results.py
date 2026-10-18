"""Store each alliance's official result per match.

Per-robot findings split alliance fuel between robots at ingest; the ratings roadmap
(docs/RATING_ROADMAP.md) estimates robots from the alliance totals instead, and needs
fouls, ranking points and surrogate/DQ flags too.

Revision ID: 20261015_0021
Revises: 20261014_0020
"""
from alembic import op
import sqlalchemy as sa

revision = "20261015_0021"
down_revision = "20261014_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "match_alliance_results",
        sa.Column("match_key", sa.String(), sa.ForeignKey("matches.match_key"), primary_key=True),
        sa.Column("alliance", sa.String(), primary_key=True),
        sa.Column("event_key", sa.String(), sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("season_year", sa.Integer(), nullable=False),
        sa.Column("team_keys", sa.JSON(), nullable=False),
        sa.Column("surrogate_team_keys", sa.JSON(), nullable=False),
        sa.Column("dq_team_keys", sa.JSON(), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=True),
        sa.Column("score_without_fouls", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column("minor_fouls_committed", sa.Integer(), nullable=True),
        sa.Column("major_fouls_committed", sa.Integer(), nullable=True),
        sa.Column("foul_points_received", sa.Integer(), nullable=True),
        sa.Column("ranking_points", sa.Integer(), nullable=True),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.Column("raw_breakdown", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_match_alliance_results_event_key", "match_alliance_results", ["event_key"])


def downgrade():
    op.drop_index("ix_match_alliance_results_event_key", table_name="match_alliance_results")
    op.drop_table("match_alliance_results")
