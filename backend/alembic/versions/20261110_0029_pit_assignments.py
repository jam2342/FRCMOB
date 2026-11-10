"""Pit assignments: which workspace member scouts which team's pit.

Revision ID: 20261110_0029
Revises: 20261021_0028
"""
from alembic import op
import sqlalchemy as sa

revision = "20261110_0029"
down_revision = "20261021_0028"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pit_assignments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("team_workspaces.id"), nullable=False),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("team_key", sa.String(), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("team_workspace_members.id"), nullable=True),
        sa.Column("assigned_by_member_id", sa.Integer(), sa.ForeignKey("team_workspace_members.id"), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "event_key", "team_key", name="uq_pit_assignment_workspace_event_team"),
    )
    op.create_index("ix_pit_assignments_workspace_id", "pit_assignments", ["workspace_id"])
    op.create_index("ix_pit_assignments_workspace_event", "pit_assignments", ["workspace_id", "event_key"])


def downgrade():
    op.drop_index("ix_pit_assignments_workspace_event", table_name="pit_assignments")
    op.drop_index("ix_pit_assignments_workspace_id", table_name="pit_assignments")
    op.drop_table("pit_assignments")
