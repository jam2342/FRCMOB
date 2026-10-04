"""add explicit provenance to analysis runs

Revision ID: 20260903_0013
Revises: 20260612_0012
Create Date: 2026-09-03 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260903_0013"
down_revision = "20260612_0012"
branch_labels = None
depends_on = None


def _column_names(inspector, table_name: str) -> set[str]:
    return {str(column.get("name") or "") for column in inspector.get_columns(table_name)}


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(
        str(index.get("name") or "") == index_name
        for index in inspector.get_indexes(table_name)
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "run_kind" not in _column_names(inspector, "analysis_runs"):
        op.add_column(
            "analysis_runs",
            sa.Column("run_kind", sa.String(), nullable=False, server_default="video"),
        )

    op.execute(
        sa.text(
            """
            UPDATE analysis_runs
            SET run_kind = CASE
                WHEN version = 'on_device_pwa_v1' THEN 'on_device'
                WHEN version IN ('tba_score_breakdown_v1', 'tba_scorebreakdown_climb_backfill_v1')
                    THEN 'official_truth'
                ELSE 'video'
            END
            """
        )
    )

    inspector = inspect(bind)
    if not _has_index(inspector, "analysis_runs", "ix_analysis_runs_run_kind"):
        op.create_index("ix_analysis_runs_run_kind", "analysis_runs", ["run_kind"])

    # PostgreSQL enforces the enum-like domain. SQLite test databases get the same
    # constraint from ORM metadata and cannot add named CHECK constraints in place.
    if bind.dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_analysis_runs_run_kind",
            "analysis_runs",
            "run_kind IN ('video', 'official_truth', 'on_device')",
        )

    inspector = inspect(bind)
    if "on_device_sessions" not in set(inspector.get_table_names()):
        op.create_table(
            "on_device_sessions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("analysis_run_id", sa.Integer(), nullable=False),
            sa.Column("match_key", sa.String(), nullable=False),
            sa.Column("event_key", sa.String(), nullable=False),
            sa.Column("room_key", sa.String(), nullable=True),
            sa.Column("principal_hash", sa.String(), nullable=False),
            sa.Column("client_session_id_hash", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False, server_default="on_device_session_v2"),
            sa.Column("model_version", sa.String(), nullable=True),
            sa.Column("calibration_version", sa.String(), nullable=True),
            sa.Column("calibration_rmse_m", sa.Float(), nullable=True),
            sa.Column("pose_fallback_ratio", sa.Float(), nullable=True),
            sa.Column("identity_confidence", sa.Float(), nullable=True),
            sa.Column("identity_source", sa.String(), nullable=False, server_default="unknown"),
            sa.Column("timing_source", sa.String(), nullable=False, server_default="unknown"),
            sa.Column("capture_to_match_offset_sec", sa.Float(), nullable=True),
            sa.Column("shift1_active_alliance", sa.String(), nullable=True),
            sa.Column("shift1_source", sa.String(), nullable=True),
            sa.Column("quality_score", sa.Float(), nullable=False, server_default=sa.text("0")),
            sa.Column("quality_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("status", sa.String(), nullable=False, server_default="provisional"),
            sa.Column("review_note", sa.String(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.CheckConstraint(
                "status IN ('provisional', 'accepted', 'rejected')",
                name="ck_on_device_sessions_status",
            ),
            sa.CheckConstraint(
                "quality_score >= 0 AND quality_score <= 1",
                name="ck_on_device_sessions_quality_score",
            ),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
            sa.ForeignKeyConstraint(["match_key"], ["matches.match_key"]),
            sa.ForeignKeyConstraint(["event_key"], ["events.event_key"]),
            sa.ForeignKeyConstraint(["room_key"], ["scouting_rooms.room_key"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("analysis_run_id"),
            sa.UniqueConstraint(
                "principal_hash",
                "client_session_id_hash",
                name="uq_on_device_session_principal_client",
            ),
        )
        for name, columns in (
            ("ix_on_device_sessions_analysis_run_id", ["analysis_run_id"]),
            ("ix_on_device_sessions_match_key", ["match_key"]),
            ("ix_on_device_sessions_event_key", ["event_key"]),
            ("ix_on_device_sessions_room_key", ["room_key"]),
            ("ix_on_device_sessions_principal_hash", ["principal_hash"]),
            ("ix_on_device_sessions_quality_score", ["quality_score"]),
            ("ix_on_device_sessions_status", ["status"]),
        ):
            op.create_index(name, "on_device_sessions", columns)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "on_device_sessions" in set(inspector.get_table_names()):
        op.drop_table("on_device_sessions")
    inspector = inspect(bind)
    if bind.dialect.name != "sqlite":
        op.drop_constraint(
            "ck_analysis_runs_run_kind", "analysis_runs", type_="check"
        )
    if _has_index(inspector, "analysis_runs", "ix_analysis_runs_run_kind"):
        op.drop_index("ix_analysis_runs_run_kind", table_name="analysis_runs")
    if "run_kind" in _column_names(inspector, "analysis_runs"):
        op.drop_column("analysis_runs", "run_kind")
