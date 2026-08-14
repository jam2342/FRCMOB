# schema integrity fixes: timestamptz columns + analysis_runs status constraint
#
# Revision ID: 20260422_0010
# Revises: 20260405_0009
# Create Date: 2026-04-22 00:00:00.000000
#
# Changes:
# - Convert all TIMESTAMP WITHOUT TIME ZONE columns to TIMESTAMPTZ.
# Existing data is assumed to be UTC (stored by _utc_now()). The USING
# clause re-interprets each naive timestamp as UTC, which is lossless.
# - Add CHECK constraint on analysis_runs.status restricting it to the
# set of values actually used by the application.
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text

revision = "20260422_0010"
down_revision = "20260405_0009"
branch_labels = None
depends_on = None

# (table_name, column_name) pairs for every DateTime column in models.py
_DATETIME_COLUMNS: list[tuple[str, str]] = [
    ("event_team_stats", "updated_at"),
    ("team_static_capabilities", "updated_at"),
    ("event_team_ratings", "updated_at"),
    ("analysis_runs", "created_at"),
    ("analysis_run_contexts", "created_at"),
    ("team_match_findings", "created_at"),
    ("team_match_throughputs", "updated_at"),
    ("robot_tracks", "created_at"),
    ("match_events", "created_at"),
    ("analysis_qualities", "updated_at"),
    ("field_calibrations", "created_at"),
    ("field_calibrations", "updated_at"),
    ("match_phase_windows", "computed_at"),
    ("team_season_strengths", "updated_at"),
    ("team_pair_synergy_priors", "updated_at"),
    ("team_event_throughput_strengths", "updated_at"),
    ("team_pair_synergy_events", "updated_at"),
    ("match_synergy_projections", "computed_at"),
    ("intel_snapshots", "generated_at"),
    ("intel_snapshots", "expires_at"),
    ("ml_feature_snapshots", "created_at"),
    ("ml_feature_snapshots", "updated_at"),
    ("ml_model_registry", "trained_at"),
    ("ml_model_registry", "activated_at"),
    ("ml_model_registry", "created_at"),
    ("ml_shadow_predictions", "created_at"),
    ("scouting_rooms", "created_at"),
    ("scouting_rooms", "updated_at"),
    ("scouting_rooms", "last_activity_at"),
    ("scouting_room_leaders", "created_at"),
    ("scouting_room_leaders", "updated_at"),
    ("scouting_room_entries", "created_at"),
    ("scouting_room_entries", "updated_at"),
    ("scouting_room_assignments", "created_at"),
    ("scouting_room_assignments", "updated_at"),
    ("auto_scout_drafts", "generated_at"),
    ("auto_scout_drafts", "updated_at"),
    ("auto_scout_drafts", "approved_at"),
    ("auto_scout_drafts", "rejected_at"),
    ("auto_scout_drafts", "superseded_at"),
]

def _column_type(inspector, table: str, col: str) -> str | None:
    try:
        cols = inspector.get_columns(table)
    except Exception:
        return None
    for c in cols:
        if c["name"] == col:
            return str(c["type"]).upper()
    return None

def _has_constraint(inspector, table: str, name: str) -> bool:
    try:
        constraints = inspector.get_check_constraints(table)
        return any(c.get("name") == name for c in constraints)
    except Exception:
        return False

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # ── 1. Convert TIMESTAMP → TIMESTAMPTZ ───────────────────────────────
    for table, col in _DATETIME_COLUMNS:
        if table not in existing_tables:
            continue
        col_type = _column_type(inspector, table, col)
        if col_type is None:
            continue
        # Skip if already TIMESTAMPTZ
        if "TIMEZONE" in col_type or "TIMESTAMPTZ" in col_type or col_type == "TIMESTAMP WITH TIME ZONE":
            continue
        op.execute(
            text(
                f'ALTER TABLE "{table}" ALTER COLUMN "{col}" '
                f'TYPE TIMESTAMPTZ USING "{col}" AT TIME ZONE \'UTC\''
            )
        )

    # ── 2. Add CHECK constraint on analysis_runs.status ──────────────────
    if "analysis_runs" in existing_tables and not _has_constraint(
        inspector, "analysis_runs", "ck_analysis_runs_status"
    ):
        op.create_check_constraint(
            "ck_analysis_runs_status",
            "analysis_runs",
            "status IN ('queued', 'running', 'completed', 'failed', 'requeued')",
        )

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # Drop status constraint
    if "analysis_runs" in existing_tables and _has_constraint(
        inspector, "analysis_runs", "ck_analysis_runs_status"
    ):
        op.drop_constraint("ck_analysis_runs_status", "analysis_runs", type_="check")

    # Revert TIMESTAMPTZ → TIMESTAMP (drops timezone info — data remains UTC)
    for table, col in _DATETIME_COLUMNS:
        if table not in existing_tables:
            continue
        col_type = _column_type(inspector, table, col)
        if col_type is None:
            continue
        if "TIMEZONE" not in col_type and "TIMESTAMPTZ" not in col_type:
            continue
        op.execute(
            text(
                f'ALTER TABLE "{table}" ALTER COLUMN "{col}" '
                f'TYPE TIMESTAMP WITHOUT TIME ZONE USING "{col}" AT TIME ZONE \'UTC\''
            )
        )
