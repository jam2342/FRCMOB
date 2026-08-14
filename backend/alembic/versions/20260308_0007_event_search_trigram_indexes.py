# add trigram indexes for event search
#
# Revision ID: 20260308_0007
# Revises: 20260305_0006
# Create Date: 2026-03-08 21:10:00.000000
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260308_0007"
down_revision = "20260305_0006"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_events_event_key_trgm
        ON events USING gin (lower(event_key) gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_events_name_trgm
        ON events USING gin (lower(name) gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_event_profiles_city_trgm
        ON event_profiles USING gin (lower(coalesce(city, '')) gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_event_profiles_state_trgm
        ON event_profiles USING gin (lower(coalesce(state_prov, '')) gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_event_profiles_country_trgm
        ON event_profiles USING gin (lower(coalesce(country, '')) gin_trgm_ops)
        """
    )

def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS ix_event_profiles_country_trgm")
    op.execute("DROP INDEX IF EXISTS ix_event_profiles_state_trgm")
    op.execute("DROP INDEX IF EXISTS ix_event_profiles_city_trgm")
    op.execute("DROP INDEX IF EXISTS ix_events_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_events_event_key_trgm")
