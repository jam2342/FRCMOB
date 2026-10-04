"""Persist camera view and independent floor-calibration evidence.

Revision ID: 20260924_0014
Revises: 20260903_0013
"""
from alembic import op
import sqlalchemy as sa

revision = "20260924_0014"
down_revision = "20260903_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("field_calibrations", sa.Column("calibration_meta", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("field_calibrations", "calibration_meta")
