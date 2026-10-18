"""Let rating columns official results can't measure be null.

rating_v15 rates from official results only: driver skill, an OPR results anchor,
shift productivity and capacity use can't be told apart there, and consistency needs
three matches at the event. They were filled with made-up defaults; now they are null.

Revision ID: 20261014_0020
Revises: 20261006_0019
"""
from alembic import op
import sqlalchemy as sa

revision = "20261014_0020"
down_revision = "20261006_0019"
branch_labels = None
depends_on = None

COLUMNS = ("driver_skill_0_100", "results_anchor", "shift_productivity", "capacity_utilization", "consistency")


def upgrade():
    for column in COLUMNS:
        op.alter_column("event_team_ratings", column, existing_type=sa.Float(), nullable=True)


def downgrade():
    for column in COLUMNS:
        op.execute(f"UPDATE event_team_ratings SET {column} = 50.0 WHERE {column} IS NULL")
        op.alter_column("event_team_ratings", column, existing_type=sa.Float(), nullable=False)
