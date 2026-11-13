# Versioned three-layer settings and immutable prediction history.
from alembic import op
import sqlalchemy as sa

revision = "20261113_0031"
down_revision = "20261112_0030"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("rating_calibrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("recipe", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("fitted_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matches", sa.Integer(), nullable=False),
        sa.Column("fit_metrics", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("code_sha", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("season", "recipe", "version"))
    op.create_index("ix_rating_calibrations_season", "rating_calibrations", ["season"])
    op.create_table("rating_active_bundle",
        sa.Column("season", sa.Integer(), primary_key=True),
        sa.Column("calibration_id", sa.Integer(), sa.ForeignKey("rating_calibrations.id")),
        sa.Column("previous_id", sa.Integer(), sa.ForeignKey("rating_calibrations.id")),
        sa.Column("bundle_version", sa.Integer(), nullable=False),
        sa.Column("frozen", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("rating_gate_scores",
        sa.Column("calibration_id", sa.Integer(), sa.ForeignKey("rating_calibrations.id"), primary_key=True),
        sa.Column("week_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("slice", sa.String(), primary_key=True),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("per_event", sa.JSON(), nullable=False))
    op.create_table("match_prediction_log",
        sa.Column("match_key", sa.String(), primary_key=True),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("first_shown_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first", sa.JSON(), nullable=False),
        sa.Column("final_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("final", sa.JSON(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("bundle_version", sa.Integer(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("backfilled", sa.Boolean(), nullable=False))
    op.create_index("ix_match_prediction_log_event_key", "match_prediction_log", ["event_key"])
    op.create_index("ix_match_prediction_log_season", "match_prediction_log", ["season"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute('''CREATE FUNCTION protect_match_prediction_log() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR OLD.locked_at IS NOT NULL THEN
            RAISE EXCEPTION 'prediction history is immutable';
          END IF;
          IF NEW.first::jsonb IS DISTINCT FROM OLD.first::jsonb OR NEW.first_shown_at <> OLD.first_shown_at
             OR NEW.match_key <> OLD.match_key OR NEW.event_key <> OLD.event_key OR NEW.season <> OLD.season
             OR NEW.final_at < OLD.final_at THEN
            RAISE EXCEPTION 'prediction origin is immutable';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql''')
        op.execute('''CREATE TRIGGER match_prediction_log_immutable BEFORE UPDATE OR DELETE
                      ON match_prediction_log FOR EACH ROW EXECUTE FUNCTION protect_match_prediction_log()''')
    else:
        op.execute('''CREATE TRIGGER match_prediction_log_immutable BEFORE UPDATE ON match_prediction_log
          WHEN OLD.locked_at IS NOT NULL OR NEW.first <> OLD.first OR NEW.first_shown_at <> OLD.first_shown_at
          OR NEW.match_key <> OLD.match_key OR NEW.event_key <> OLD.event_key OR NEW.season <> OLD.season OR NEW.final_at < OLD.final_at
          BEGIN SELECT RAISE(ABORT, 'prediction history is immutable'); END''')
        op.execute('''CREATE TRIGGER match_prediction_log_no_delete BEFORE DELETE ON match_prediction_log
          BEGIN SELECT RAISE(ABORT, 'prediction history is immutable'); END''')

    if op.get_bind().dialect.name == "postgresql":
        op.execute('''CREATE FUNCTION protect_rating_fit() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'calibration version is immutable'; END IF;
          IF NEW.id <> OLD.id OR NEW.params::jsonb IS DISTINCT FROM OLD.params::jsonb OR NEW.season <> OLD.season
             OR NEW.recipe <> OLD.recipe OR NEW.version <> OLD.version OR NEW.fitted_through <> OLD.fitted_through
             OR NEW.matches <> OLD.matches OR NEW.code_sha <> OLD.code_sha OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'calibration version is immutable';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql''')
        op.execute('''CREATE TRIGGER rating_fit_immutable BEFORE UPDATE OR DELETE ON rating_calibrations
                      FOR EACH ROW EXECUTE FUNCTION protect_rating_fit()''')
    else:
        op.execute('''CREATE TRIGGER rating_fit_immutable BEFORE UPDATE ON rating_calibrations
          WHEN NEW.id <> OLD.id OR NEW.params <> OLD.params OR NEW.season <> OLD.season OR NEW.recipe <> OLD.recipe
          OR NEW.version <> OLD.version OR NEW.fitted_through <> OLD.fitted_through OR NEW.matches <> OLD.matches
          OR NEW.code_sha <> OLD.code_sha OR NEW.created_at <> OLD.created_at
          BEGIN SELECT RAISE(ABORT, 'calibration version is immutable'); END''')
        op.execute('''CREATE TRIGGER rating_fit_no_delete BEFORE DELETE ON rating_calibrations
          BEGIN SELECT RAISE(ABORT, 'calibration version is immutable'); END''')


def downgrade():
    op.drop_table("match_prediction_log")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_match_prediction_log()")
    op.drop_table("rating_gate_scores")
    op.drop_table("rating_active_bundle")
    op.drop_table("rating_calibrations")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_rating_fit()")
