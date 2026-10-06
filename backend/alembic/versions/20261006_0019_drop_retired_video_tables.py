"""Drop tables and columns nothing reads or writes since the broadcast pipeline was retired.

artifacts (180 broadcast rows), field_calibrations (2 broadcast calibrations) and the
analysis_run_contexts.calibration_id column pointing at them, match_videos (TBA video
links no page shows; refetchable from TBA), team_static_capabilities (never filled)
and analysis_qualities.calibration_quality_score.

Revision ID: 20261006_0019
Revises: 20261005_0018
"""
from alembic import op
import sqlalchemy as sa

revision = "20261006_0019"
down_revision = "20261005_0018"
branch_labels = None
depends_on = None


def upgrade():
    # IF EXISTS: a copy restored without field_calibrations rows has no such constraint.
    op.execute("ALTER TABLE analysis_run_contexts DROP CONSTRAINT IF EXISTS analysis_run_contexts_calibration_id_fkey")
    op.execute("DROP INDEX IF EXISTS ix_analysis_run_contexts_calibration_id")
    op.drop_column("analysis_run_contexts", "calibration_id")
    op.drop_column("analysis_qualities", "calibration_quality_score")
    op.drop_table("artifacts")
    op.drop_table("field_calibrations")
    op.drop_table("match_videos")
    op.drop_table("team_static_capabilities")


def downgrade():
    op.create_table(
        "team_static_capabilities",
        sa.Column("team_key", sa.String(), sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("ball_capacity", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "match_videos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("match_key", sa.String(), sa.ForeignKey("matches.match_key"), nullable=False),
        sa.Column("video_type", sa.String(), nullable=False),
        sa.Column("video_key", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
    )
    op.create_index("ix_match_videos_match_key", "match_videos", ["match_key"])
    op.create_table(
        "field_calibrations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("match_key", sa.String(), sa.ForeignKey("matches.match_key"), nullable=False),
        sa.Column("event_key", sa.String(), sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("frame_time_sec", sa.Float(), nullable=True),
        sa.Column("image_width", sa.Integer(), nullable=False),
        sa.Column("image_height", sa.Integer(), nullable=False),
        sa.Column("image_points", sa.JSON(), nullable=False),
        sa.Column("field_points", sa.JSON(), nullable=False),
        sa.Column("homography", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calibration_meta", sa.JSON(), nullable=True),
    )
    op.create_index("ix_field_calibrations_event_key", "field_calibrations", ["event_key"])
    op.create_index("ix_field_calibrations_match_key", "field_calibrations", ["match_key"], unique=True)
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
    )
    op.create_index("ix_artifacts_analysis_run_id", "artifacts", ["analysis_run_id"])
    op.add_column(
        "analysis_qualities",
        sa.Column("calibration_quality_score", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column("analysis_run_contexts", sa.Column("calibration_id", sa.Integer(), nullable=True))
    op.create_index("ix_analysis_run_contexts_calibration_id", "analysis_run_contexts", ["calibration_id"])
    op.create_foreign_key(
        "analysis_run_contexts_calibration_id_fkey",
        "analysis_run_contexts",
        "field_calibrations",
        ["calibration_id"],
        ["id"],
    )
