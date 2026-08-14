# add ml shadow pipeline tables
#
# Revision ID: 20260308_0008
# Revises: 20260308_0007
# Create Date: 2026-03-08 22:00:00.000000
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "20260308_0008"
down_revision = "20260308_0007"
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

def _drop_if_exists(inspector, name: str, table: str) -> None:
    if _has_index(inspector, table, name):
        op.drop_index(name, table_name=table)

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "ml_feature_snapshots" not in existing_tables:
        op.create_table(
            "ml_feature_snapshots",
            sa.Column("snapshot_key", sa.String(), nullable=False),
            sa.Column("scope", sa.String(), nullable=False),
            sa.Column("event_key", sa.String(), nullable=True),
            sa.Column("match_key", sa.String(), nullable=True),
            sa.Column("team_key", sa.String(), nullable=True),
            sa.Column("alliance_color", sa.String(), nullable=True),
            sa.Column("feature_vector", sa.JSON(), nullable=False),
            sa.Column("target", sa.JSON(), nullable=False),
            sa.Column("split_tag", sa.String(), nullable=False, server_default=sa.text("'train'")),
            sa.Column("source_version", sa.String(), nullable=False, server_default=sa.text("'shadow_features_v1'")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["event_key"], ["events.event_key"]),
            sa.ForeignKeyConstraint(["match_key"], ["matches.match_key"]),
            sa.ForeignKeyConstraint(["team_key"], ["teams.team_key"]),
            sa.PrimaryKeyConstraint("snapshot_key"),
        )
        existing_tables.add("ml_feature_snapshots")

    if "ml_model_registry" not in existing_tables:
        op.create_table(
            "ml_model_registry",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("model_key", sa.String(), nullable=False),
            sa.Column("model_version", sa.String(), nullable=False),
            sa.Column("framework", sa.String(), nullable=False, server_default=sa.text("'torch'")),
            sa.Column("artifact_path", sa.String(), nullable=False),
            sa.Column("input_schema", sa.JSON(), nullable=False),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("trained_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("activated_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("model_key", "model_version", name="uq_ml_model_registry_key_version"),
        )
        existing_tables.add("ml_model_registry")

    if "ml_shadow_predictions" not in existing_tables:
        op.create_table(
            "ml_shadow_predictions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("model_key", sa.String(), nullable=False),
            sa.Column("model_version", sa.String(), nullable=False),
            sa.Column("event_key", sa.String(), nullable=True),
            sa.Column("match_key", sa.String(), nullable=True),
            sa.Column("team_key", sa.String(), nullable=True),
            sa.Column("target_key", sa.String(), nullable=False),
            sa.Column("prediction_value", sa.Float(), nullable=True),
            sa.Column("prediction_json", sa.JSON(), nullable=False),
            sa.Column("feature_snapshot_key", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["event_key"], ["events.event_key"]),
            sa.ForeignKeyConstraint(["match_key"], ["matches.match_key"]),
            sa.ForeignKeyConstraint(["team_key"], ["teams.team_key"]),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("ml_shadow_predictions")

    inspector = inspect(bind)

    # ml_feature_snapshots indexes
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_scope"):
        op.create_index("ix_ml_feature_snapshots_scope", "ml_feature_snapshots", ["scope"], unique=False)
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_event_key"):
        op.create_index("ix_ml_feature_snapshots_event_key", "ml_feature_snapshots", ["event_key"], unique=False)
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_match_key"):
        op.create_index("ix_ml_feature_snapshots_match_key", "ml_feature_snapshots", ["match_key"], unique=False)
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_team_key"):
        op.create_index("ix_ml_feature_snapshots_team_key", "ml_feature_snapshots", ["team_key"], unique=False)
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_alliance_color"):
        op.create_index("ix_ml_feature_snapshots_alliance_color", "ml_feature_snapshots", ["alliance_color"], unique=False)
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_split_tag"):
        op.create_index("ix_ml_feature_snapshots_split_tag", "ml_feature_snapshots", ["split_tag"], unique=False)
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_source_version"):
        op.create_index("ix_ml_feature_snapshots_source_version", "ml_feature_snapshots", ["source_version"], unique=False)
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_created_at"):
        op.create_index("ix_ml_feature_snapshots_created_at", "ml_feature_snapshots", ["created_at"], unique=False)
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_updated_at"):
        op.create_index("ix_ml_feature_snapshots_updated_at", "ml_feature_snapshots", ["updated_at"], unique=False)
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_scope_event_created"):
        op.create_index(
            "ix_ml_feature_snapshots_scope_event_created",
            "ml_feature_snapshots",
            ["scope", "event_key", "created_at"],
            unique=False,
        )
    if not _has_index(inspector, "ml_feature_snapshots", "ix_ml_feature_snapshots_scope_split_created"):
        op.create_index(
            "ix_ml_feature_snapshots_scope_split_created",
            "ml_feature_snapshots",
            ["scope", "split_tag", "created_at"],
            unique=False,
        )

    # ml_model_registry indexes
    if not _has_index(inspector, "ml_model_registry", "ix_ml_model_registry_model_key"):
        op.create_index("ix_ml_model_registry_model_key", "ml_model_registry", ["model_key"], unique=False)
    if not _has_index(inspector, "ml_model_registry", "ix_ml_model_registry_is_active"):
        op.create_index("ix_ml_model_registry_is_active", "ml_model_registry", ["is_active"], unique=False)
    if not _has_index(inspector, "ml_model_registry", "ix_ml_model_registry_trained_at"):
        op.create_index("ix_ml_model_registry_trained_at", "ml_model_registry", ["trained_at"], unique=False)
    if not _has_index(inspector, "ml_model_registry", "ix_ml_model_registry_activated_at"):
        op.create_index("ix_ml_model_registry_activated_at", "ml_model_registry", ["activated_at"], unique=False)
    if not _has_index(inspector, "ml_model_registry", "ix_ml_model_registry_created_at"):
        op.create_index("ix_ml_model_registry_created_at", "ml_model_registry", ["created_at"], unique=False)
    if not _has_index(inspector, "ml_model_registry", "ix_ml_model_registry_key_active"):
        op.create_index(
            "ix_ml_model_registry_key_active",
            "ml_model_registry",
            ["model_key", "is_active"],
            unique=False,
        )

    # ml_shadow_predictions indexes
    if not _has_index(inspector, "ml_shadow_predictions", "ix_ml_shadow_predictions_model_key"):
        op.create_index("ix_ml_shadow_predictions_model_key", "ml_shadow_predictions", ["model_key"], unique=False)
    if not _has_index(inspector, "ml_shadow_predictions", "ix_ml_shadow_predictions_model_version"):
        op.create_index("ix_ml_shadow_predictions_model_version", "ml_shadow_predictions", ["model_version"], unique=False)
    if not _has_index(inspector, "ml_shadow_predictions", "ix_ml_shadow_predictions_event_key"):
        op.create_index("ix_ml_shadow_predictions_event_key", "ml_shadow_predictions", ["event_key"], unique=False)
    if not _has_index(inspector, "ml_shadow_predictions", "ix_ml_shadow_predictions_match_key"):
        op.create_index("ix_ml_shadow_predictions_match_key", "ml_shadow_predictions", ["match_key"], unique=False)
    if not _has_index(inspector, "ml_shadow_predictions", "ix_ml_shadow_predictions_team_key"):
        op.create_index("ix_ml_shadow_predictions_team_key", "ml_shadow_predictions", ["team_key"], unique=False)
    if not _has_index(inspector, "ml_shadow_predictions", "ix_ml_shadow_predictions_target_key"):
        op.create_index("ix_ml_shadow_predictions_target_key", "ml_shadow_predictions", ["target_key"], unique=False)
    if not _has_index(inspector, "ml_shadow_predictions", "ix_ml_shadow_predictions_feature_snapshot_key"):
        op.create_index(
            "ix_ml_shadow_predictions_feature_snapshot_key",
            "ml_shadow_predictions",
            ["feature_snapshot_key"],
            unique=False,
        )
    if not _has_index(inspector, "ml_shadow_predictions", "ix_ml_shadow_predictions_created_at"):
        op.create_index("ix_ml_shadow_predictions_created_at", "ml_shadow_predictions", ["created_at"], unique=False)
    if not _has_index(inspector, "ml_shadow_predictions", "ix_ml_shadow_predictions_event_model_target"):
        op.create_index(
            "ix_ml_shadow_predictions_event_model_target",
            "ml_shadow_predictions",
            ["event_key", "model_key", "model_version", "target_key"],
            unique=False,
        )

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "ml_shadow_predictions" in existing_tables:
        _drop_if_exists(inspector, "ix_ml_shadow_predictions_event_model_target", "ml_shadow_predictions")
        _drop_if_exists(inspector, "ix_ml_shadow_predictions_created_at", "ml_shadow_predictions")
        _drop_if_exists(inspector, "ix_ml_shadow_predictions_feature_snapshot_key", "ml_shadow_predictions")
        _drop_if_exists(inspector, "ix_ml_shadow_predictions_target_key", "ml_shadow_predictions")
        _drop_if_exists(inspector, "ix_ml_shadow_predictions_team_key", "ml_shadow_predictions")
        _drop_if_exists(inspector, "ix_ml_shadow_predictions_match_key", "ml_shadow_predictions")
        _drop_if_exists(inspector, "ix_ml_shadow_predictions_event_key", "ml_shadow_predictions")
        _drop_if_exists(inspector, "ix_ml_shadow_predictions_model_version", "ml_shadow_predictions")
        _drop_if_exists(inspector, "ix_ml_shadow_predictions_model_key", "ml_shadow_predictions")
        op.drop_table("ml_shadow_predictions")

    if "ml_model_registry" in existing_tables:
        _drop_if_exists(inspector, "ix_ml_model_registry_key_active", "ml_model_registry")
        _drop_if_exists(inspector, "ix_ml_model_registry_created_at", "ml_model_registry")
        _drop_if_exists(inspector, "ix_ml_model_registry_activated_at", "ml_model_registry")
        _drop_if_exists(inspector, "ix_ml_model_registry_trained_at", "ml_model_registry")
        _drop_if_exists(inspector, "ix_ml_model_registry_is_active", "ml_model_registry")
        _drop_if_exists(inspector, "ix_ml_model_registry_model_key", "ml_model_registry")
        op.drop_table("ml_model_registry")

    if "ml_feature_snapshots" in existing_tables:
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_scope_split_created", "ml_feature_snapshots")
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_scope_event_created", "ml_feature_snapshots")
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_updated_at", "ml_feature_snapshots")
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_created_at", "ml_feature_snapshots")
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_source_version", "ml_feature_snapshots")
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_split_tag", "ml_feature_snapshots")
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_alliance_color", "ml_feature_snapshots")
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_team_key", "ml_feature_snapshots")
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_match_key", "ml_feature_snapshots")
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_event_key", "ml_feature_snapshots")
        _drop_if_exists(inspector, "ix_ml_feature_snapshots_scope", "ml_feature_snapshots")
        op.drop_table("ml_feature_snapshots")
