"""Let component subscores be null for total-only ratings.

A season rated by the state recipe on total score (Tier A, before its component parser
exists) has no measurable throughput or endgame. They are null rather than a made-up 50.

Revision ID: 20261115_0034
Revises: 20261114_0033
"""
from alembic import op
import sqlalchemy as sa

revision = "20261115_0034"
down_revision = "20261114_0033"
branch_labels = None
depends_on = None

COLUMNS = ("throughput", "endgame")


def upgrade():
    for column in COLUMNS:
        op.alter_column("event_team_ratings", column, existing_type=sa.Float(), nullable=True)


def downgrade():
    for column in COLUMNS:
        op.execute(f"UPDATE event_team_ratings SET {column} = 50.0 WHERE {column} IS NULL")
        op.alter_column("event_team_ratings", column, existing_type=sa.Float(), nullable=False)
