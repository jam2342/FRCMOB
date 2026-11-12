"""Pit entries keep answers two scouts changed differently.

Revision ID: 20261112_0030
Revises: 20261110_0029
"""
from alembic import op
import sqlalchemy as sa

revision = "20261112_0030"
down_revision = "20261110_0029"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "pit_scouting_entries",
        sa.Column("conflicts", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade():
    op.drop_column("pit_scouting_entries", "conflicts")
