"""Give B teams their team number.

B teams (frc2403B) appear only in match alliances, so ingest created them from the key, and
int("2403B") failed into team number 0: they sorted first and read as "team 0". Rows still at
0 or null whose key is a team key with one trailing letter get the key's number. Nothing else
changes; running it again changes nothing.

Revision ID: 20261115_0035
Revises: 20261115_0034
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "20261115_0035"
down_revision = "20261115_0034"
branch_labels = None
depends_on = None

B_TEAM = re.compile(r"^frc(\d{1,5})[A-Za-z]$")


def upgrade():
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT team_key FROM teams WHERE team_number IS NULL OR team_number = 0")).fetchall()
    for (team_key,) in rows:
        match = B_TEAM.match(team_key or "")
        if match:
            conn.execute(sa.text("UPDATE teams SET team_number = :number WHERE team_key = :key"),
                         {"number": int(match.group(1)), "key": team_key})


def downgrade():
    # The old value was a parse failure, not data worth restoring.
    pass
