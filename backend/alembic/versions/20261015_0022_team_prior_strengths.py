"""Last-season team strengths and rookie years, for the rating's early-season prior.

team_prior_strengths: each team's end-of-season expected points (team model on
alliance totals, game-agnostic) and its z (log ratio to the season's median team), used
as next season's starting belief. teams.rookie_year from TBA.

Revision ID: 20261015_0022
Revises: 20261015_0021
"""
from alembic import op
import sqlalchemy as sa

revision = "20261015_0022"
down_revision = "20261015_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "team_prior_strengths",
        sa.Column("season", sa.Integer(), primary_key=True),
        sa.Column("team_key", sa.String(), primary_key=True),
        sa.Column("points", sa.Float(), nullable=False),
        sa.Column("z", sa.Float(), nullable=False),
        sa.Column("matches", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("teams", sa.Column("rookie_year", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("teams", "rookie_year")
    op.drop_table("team_prior_strengths")
