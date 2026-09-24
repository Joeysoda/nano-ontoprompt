"""Compatibility marker for databases created by the retired mapping-FK migration.

Some existing local databases were stamped with ``0003_mapping_fk_fix`` even
though that migration was later folded into the full baseline.  Keeping the
revision id in the script directory lets Alembic inspect and reconcile those
databases without modifying their data.  The migration was already applied in
those databases, so this compatibility marker intentionally performs no DDL.
"""
from alembic import op  # noqa: F401


revision = "0003_mapping_fk_fix"
down_revision = "0002_entity_identifiers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
