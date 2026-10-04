"""Drop three climb columns no model or query has used (all NULL in production).

Revision ID: 20260929_0017
Revises: 20260928_0016
"""
from alembic import op
import sqlalchemy as sa

revision = "20260929_0017"
down_revision = "20260928_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("team_match_findings", "climb_confidence")
    op.drop_column("team_match_findings", "climb_data_source")
    op.drop_column("team_match_findings", "official_climb_level")


def downgrade():
    op.add_column("team_match_findings", sa.Column("official_climb_level", sa.String(), nullable=True))
    op.add_column("team_match_findings", sa.Column("climb_data_source", sa.String(), nullable=True))
    op.add_column("team_match_findings", sa.Column("climb_confidence", sa.Float(), nullable=True))
