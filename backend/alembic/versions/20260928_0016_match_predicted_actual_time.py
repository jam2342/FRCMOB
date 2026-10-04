"""Store TBA's predicted and actual start times next to the scheduled one.

Revision ID: 20260928_0016
Revises: 20260926_0015
"""
from alembic import op
import sqlalchemy as sa

revision = "20260928_0016"
down_revision = "20260926_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("matches", sa.Column("predicted_time", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("actual_time", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("matches", "actual_time")
    op.drop_column("matches", "predicted_time")
