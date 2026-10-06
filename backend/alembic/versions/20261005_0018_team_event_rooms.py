from alembic import op
import sqlalchemy as sa

revision = "20261005_0018"
down_revision = "20260929_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scouting_rooms", sa.Column("team_event_key", sa.String(), nullable=True))
    op.create_unique_constraint(
        "uq_scouting_room_team_event", "scouting_rooms", ["workspace_id", "team_event_key"]
    )
    op.add_column("scouting_room_assignments", sa.Column("assigned_member_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_scouting_room_assignment_member", "scouting_room_assignments", "team_workspace_members",
        ["assigned_member_id"], ["id"],
    )


def downgrade():
    op.drop_constraint("fk_scouting_room_assignment_member", "scouting_room_assignments", type_="foreignkey")
    op.drop_column("scouting_room_assignments", "assigned_member_id")
    op.drop_constraint("uq_scouting_room_team_event", "scouting_rooms", type_="unique")
    op.drop_column("scouting_rooms", "team_event_key")
