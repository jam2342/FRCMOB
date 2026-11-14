# Prospective RP forecasts remain immutable after capture.
from alembic import op
import sqlalchemy as sa

revision = "20261114_0033"
down_revision = "20261113_0032"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("rating_rp_predictions",
        sa.Column("calibration_id",sa.Integer(),sa.ForeignKey("rating_calibrations.id"),primary_key=True),
        sa.Column("match_key",sa.String(),primary_key=True),
        sa.Column("event_key",sa.String(),nullable=False),
        sa.Column("season",sa.Integer(),nullable=False),
        sa.Column("captured_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("expected_start",sa.Integer(),nullable=False),
        sa.Column("prediction",sa.JSON(),nullable=False))
    op.create_index("ix_rating_rp_predictions_event_key","rating_rp_predictions",["event_key"])
    op.create_index("ix_rating_rp_predictions_season","rating_rp_predictions",["season"])
    op.create_table("rating_rp_fit_evidence",
        sa.Column("calibration_id",sa.Integer(),sa.ForeignKey("rating_calibrations.id"),primary_key=True),
        sa.Column("evidence",sa.JSON(),nullable=False))
    if op.get_bind().dialect.name == "postgresql":
        op.execute('''CREATE FUNCTION protect_rating_rp_prediction() RETURNS trigger AS $$
          BEGIN RAISE EXCEPTION 'RP forecast evidence is immutable'; END; $$ LANGUAGE plpgsql''')
        op.execute('''CREATE TRIGGER rating_rp_prediction_immutable BEFORE UPDATE OR DELETE
          ON rating_rp_predictions FOR EACH ROW EXECUTE FUNCTION protect_rating_rp_prediction()''')
        op.execute('''CREATE TRIGGER rating_rp_fit_immutable BEFORE UPDATE OR DELETE
          ON rating_rp_fit_evidence FOR EACH ROW EXECUTE FUNCTION protect_rating_rp_prediction()''')
    else:
        for action in ("UPDATE","DELETE"):
            op.execute(f'''CREATE TRIGGER rating_rp_prediction_no_{action.lower()} BEFORE {action} ON rating_rp_predictions
              BEGIN SELECT RAISE(ABORT, 'RP forecast evidence is immutable'); END''')
            op.execute(f'''CREATE TRIGGER rating_rp_fit_no_{action.lower()} BEFORE {action} ON rating_rp_fit_evidence
              BEGIN SELECT RAISE(ABORT, 'RP fit evidence is immutable'); END''')


def downgrade():
    op.drop_table("rating_rp_fit_evidence")
    op.drop_table("rating_rp_predictions")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_rating_rp_prediction()")
