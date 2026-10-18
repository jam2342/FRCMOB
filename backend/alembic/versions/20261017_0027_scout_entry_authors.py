"""Bind scouting reports to immutable workspace memberships.

Existing authors cannot safely be inferred from recyclable display names, so
legacy rows deliberately retain NULL ownership and cannot be overwritten.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261017_0027"
down_revision = "20261017_0026"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scouting_room_entries", sa.Column("author_member_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_scouting_room_entry_author", "scouting_room_entries", "team_workspace_members",
        ["author_member_id"], ["id"],
    )


def downgrade():
    op.drop_constraint("fk_scouting_room_entry_author", "scouting_room_entries", type_="foreignkey")
    op.drop_column("scouting_room_entries", "author_member_id")
