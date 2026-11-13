# Prospective total-state forecasts are immutable evidence, separate from served history.
from alembic import op
import sqlalchemy as sa

revision = "20261113_0032"
down_revision = "20261113_0031"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("rating_state_predictions",
        sa.Column("calibration_id", sa.Integer(), sa.ForeignKey("rating_calibrations.id"), primary_key=True),
        sa.Column("match_key", sa.String(), primary_key=True),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_start", sa.Integer(), nullable=False),
        sa.Column("prediction", sa.JSON(), nullable=False))
    op.create_index("ix_rating_state_predictions_event_key", "rating_state_predictions", ["event_key"])
    op.create_index("ix_rating_state_predictions_season", "rating_state_predictions", ["season"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute('''CREATE FUNCTION protect_rating_state_prediction() RETURNS trigger AS $$
          BEGIN RAISE EXCEPTION 'state forecast evidence is immutable'; END; $$ LANGUAGE plpgsql''')
        op.execute('''CREATE TRIGGER rating_state_prediction_immutable BEFORE UPDATE OR DELETE
          ON rating_state_predictions FOR EACH ROW EXECUTE FUNCTION protect_rating_state_prediction()''')
    else:
        for action in ("UPDATE", "DELETE"):
            op.execute(f'''CREATE TRIGGER rating_state_prediction_no_{action.lower()} BEFORE {action} ON rating_state_predictions
              BEGIN SELECT RAISE(ABORT, 'state forecast evidence is immutable'); END''')


def downgrade():
    op.drop_table("rating_state_predictions")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_rating_state_prediction()")
