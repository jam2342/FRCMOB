"""Strategy boards: a match's plan drawn on the field, private to a workspace.

Revision ID: 20261021_0028
Revises: 20261017_0027
"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_0028"
down_revision = "20261017_0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "strategy_boards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("team_workspaces.id"), nullable=False),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("match_key", sa.String(), nullable=False),
        sa.Column("strokes", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "match_key", name="uq_strategy_board_workspace_match"),
    )
    op.create_index("ix_strategy_boards_workspace_id", "strategy_boards", ["workspace_id"])
    op.create_index("ix_strategy_boards_event_key", "strategy_boards", ["event_key"])


def downgrade():
    op.drop_index("ix_strategy_boards_event_key", table_name="strategy_boards")
    op.drop_index("ix_strategy_boards_workspace_id", table_name="strategy_boards")
    op.drop_table("strategy_boards")
