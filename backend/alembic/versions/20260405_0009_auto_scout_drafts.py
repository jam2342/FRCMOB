# add auto scout drafts table
#
# Revision ID: 20260405_0009
# Revises: 20260308_0008
# Create Date: 2026-04-05 13:30:00.000000
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260405_0009"
down_revision = "20260308_0008"
branch_labels = None
depends_on = None

def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    return any(str(index.get("name") or "") == index_name for index in indexes)

def _drop_if_exists(inspector, name: str, table: str) -> None:
    if _has_index(inspector, table, name):
        op.drop_index(name, table_name=table)

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "auto_scout_drafts" not in existing_tables:
        op.create_table(
            "auto_scout_drafts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_key", sa.String(), nullable=False),
            sa.Column("match_key", sa.String(), nullable=False),
            sa.Column("team_key", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
            sa.Column("draft_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("approved_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("field_confidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("field_provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("field_evidence_refs", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("field_overrides", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("coverage_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("missing_reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("analysis_version", sa.String(), nullable=False, server_default=sa.text("'video_v3_tracks'")),
            sa.Column("analysis_run_id", sa.Integer(), nullable=True),
            sa.Column("mapper_version", sa.String(), nullable=False, server_default=sa.text("'unknown'")),
            sa.Column("draft_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("approved_by", sa.String(), nullable=True),
            sa.Column("rejected_at", sa.DateTime(), nullable=True),
            sa.Column("rejected_reason", sa.String(), nullable=True),
            sa.Column("superseded_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
            sa.ForeignKeyConstraint(["event_key"], ["events.event_key"]),
            sa.ForeignKeyConstraint(["match_key"], ["matches.match_key"]),
            sa.ForeignKeyConstraint(["team_key"], ["teams.team_key"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "match_key",
                "team_key",
                "analysis_run_id",
                "mapper_version",
                name="uq_auto_scout_draft_team_run_mapper",
            ),
        )
        existing_tables.add("auto_scout_drafts")

    inspector = inspect(bind)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_event_key"):
        op.create_index("ix_auto_scout_drafts_event_key", "auto_scout_drafts", ["event_key"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_match_key"):
        op.create_index("ix_auto_scout_drafts_match_key", "auto_scout_drafts", ["match_key"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_team_key"):
        op.create_index("ix_auto_scout_drafts_team_key", "auto_scout_drafts", ["team_key"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_status"):
        op.create_index("ix_auto_scout_drafts_status", "auto_scout_drafts", ["status"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_analysis_version"):
        op.create_index("ix_auto_scout_drafts_analysis_version", "auto_scout_drafts", ["analysis_version"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_analysis_run_id"):
        op.create_index("ix_auto_scout_drafts_analysis_run_id", "auto_scout_drafts", ["analysis_run_id"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_mapper_version"):
        op.create_index("ix_auto_scout_drafts_mapper_version", "auto_scout_drafts", ["mapper_version"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_generated_at"):
        op.create_index("ix_auto_scout_drafts_generated_at", "auto_scout_drafts", ["generated_at"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_updated_at"):
        op.create_index("ix_auto_scout_drafts_updated_at", "auto_scout_drafts", ["updated_at"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_approved_at"):
        op.create_index("ix_auto_scout_drafts_approved_at", "auto_scout_drafts", ["approved_at"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_approved_by"):
        op.create_index("ix_auto_scout_drafts_approved_by", "auto_scout_drafts", ["approved_by"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_rejected_at"):
        op.create_index("ix_auto_scout_drafts_rejected_at", "auto_scout_drafts", ["rejected_at"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_superseded_at"):
        op.create_index("ix_auto_scout_drafts_superseded_at", "auto_scout_drafts", ["superseded_at"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_event_status"):
        op.create_index("ix_auto_scout_drafts_event_status", "auto_scout_drafts", ["event_key", "status"], unique=False)
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_match_team_generated"):
        op.create_index(
            "ix_auto_scout_drafts_match_team_generated",
            "auto_scout_drafts",
            ["match_key", "team_key", "generated_at"],
            unique=False,
        )
    if not _has_index(inspector, "auto_scout_drafts", "ix_auto_scout_drafts_team_status"):
        op.create_index(
            "ix_auto_scout_drafts_team_status",
            "auto_scout_drafts",
            ["team_key", "status"],
            unique=False,
        )

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "auto_scout_drafts" not in existing_tables:
        return

    _drop_if_exists(inspector, "ix_auto_scout_drafts_team_status", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_match_team_generated", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_event_status", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_superseded_at", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_rejected_at", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_approved_by", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_approved_at", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_updated_at", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_generated_at", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_mapper_version", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_analysis_run_id", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_analysis_version", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_status", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_team_key", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_match_key", "auto_scout_drafts")
    _drop_if_exists(inspector, "ix_auto_scout_drafts_event_key", "auto_scout_drafts")
    op.drop_table("auto_scout_drafts")
