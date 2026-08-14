# add performance indexes for hot read paths
#
# Revision ID: 20260225_0004
# Revises: 20260222_0003
# Create Date: 2026-02-25 22:00:00.000000
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "20260225_0004"
down_revision = "20260222_0003"
branch_labels = None
depends_on = None

def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    for index in indexes:
        if str(index.get("name") or "") == index_name:
            return True
    return False

def _create_if_missing(inspector, name: str, table: str, cols: list[str]) -> None:
    if not _has_index(inspector, table, name):
        op.create_index(name, table, cols, unique=False)

def _drop_if_exists(inspector, name: str, table: str) -> None:
    if _has_index(inspector, table, name):
        op.drop_index(name, table_name=table)

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    _create_if_missing(inspector, "ix_events_year", "events", ["year"])
    _create_if_missing(inspector, "ix_event_teams_team_key", "event_teams", ["team_key"])
    _create_if_missing(inspector, "ix_event_team_rating_team_key", "event_team_ratings", ["team_key"])
    _create_if_missing(
        inspector,
        "ix_event_team_rating_team_updated",
        "event_team_ratings",
        ["team_key", "updated_at"],
    )
    _create_if_missing(inspector, "ix_matches_event_time", "matches", ["event_key", "time"])
    _create_if_missing(
        inspector,
        "ix_matches_event_comp_order",
        "matches",
        ["event_key", "comp_level", "set_number", "match_number"],
    )
    _create_if_missing(inspector, "ix_match_teams_event_team", "match_teams", ["event_key", "team_key"])
    _create_if_missing(inspector, "ix_match_teams_team_event", "match_teams", ["team_key", "event_key"])
    _create_if_missing(
        inspector,
        "ix_team_match_finding_team_event_id",
        "team_match_findings",
        ["team_key", "event_key", "id"],
    )
    _create_if_missing(
        inspector,
        "ix_team_match_finding_team_run",
        "team_match_findings",
        ["team_key", "analysis_run_id"],
    )
    _create_if_missing(
        inspector,
        "ix_robot_track_team_event_time",
        "robot_tracks",
        ["team_key", "event_key", "time_sec", "id"],
    )
    _create_if_missing(
        inspector,
        "ix_robot_track_team_run_time",
        "robot_tracks",
        ["team_key", "analysis_run_id", "time_sec", "id"],
    )
    _create_if_missing(
        inspector,
        "ix_match_events_team_event_time",
        "match_events",
        ["team_key", "event_key", "time_sec", "id"],
    )
    _create_if_missing(
        inspector,
        "ix_match_events_team_run_time",
        "match_events",
        ["team_key", "analysis_run_id", "time_sec", "id"],
    )

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    _drop_if_exists(inspector, "ix_match_events_team_run_time", "match_events")
    _drop_if_exists(inspector, "ix_match_events_team_event_time", "match_events")
    _drop_if_exists(inspector, "ix_robot_track_team_run_time", "robot_tracks")
    _drop_if_exists(inspector, "ix_robot_track_team_event_time", "robot_tracks")
    _drop_if_exists(inspector, "ix_team_match_finding_team_run", "team_match_findings")
    _drop_if_exists(inspector, "ix_team_match_finding_team_event_id", "team_match_findings")
    _drop_if_exists(inspector, "ix_match_teams_team_event", "match_teams")
    _drop_if_exists(inspector, "ix_match_teams_event_team", "match_teams")
    _drop_if_exists(inspector, "ix_matches_event_comp_order", "matches")
    _drop_if_exists(inspector, "ix_matches_event_time", "matches")
    _drop_if_exists(inspector, "ix_event_team_rating_team_updated", "event_team_ratings")
    _drop_if_exists(inspector, "ix_event_team_rating_team_key", "event_team_ratings")
    _drop_if_exists(inspector, "ix_event_teams_team_key", "event_teams")
    _drop_if_exists(inspector, "ix_events_year", "events")
