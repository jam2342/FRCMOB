"""Drop the ML shadow tables.

The shadow models never went live (0 active of 878 registered; predictions stopped
2026-10-05) and their code is gone. The off-site Neon copy needs the same drops, or
the hourly pg_restore --clean there fails (docs/BACKEND_HOST.md).

Revision ID: 20261017_0025
Revises: 20261017_0024
"""
import importlib.util
from pathlib import Path

from alembic import op

revision = "20261017_0025"
down_revision = "20261017_0024"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("ml_shadow_predictions")
    op.drop_table("ml_model_registry")
    op.drop_table("ml_feature_snapshots")


def downgrade():
    # Empty tables as 20260308_0008 created them (its upgrade skips what exists).
    path = Path(__file__).with_name("20260308_0008_ml_shadow_pipeline.py")
    spec = importlib.util.spec_from_file_location("ml_shadow_pipeline_0008", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.upgrade()
