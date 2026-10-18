"""Persist the schema-migration rollback journal used by Plan A runs."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0037_schema_migration_execution_journal"
down_revision: Union[str, None] = "0036_plan_a_schema_alignment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "v2_schema_migration_plans" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("v2_schema_migration_plans")}
    if "execution_json" in columns:
        return
    op.add_column(
        "v2_schema_migration_plans",
        sa.Column("execution_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    # The ORM owns the application default; the temporary server default is
    # only needed to backfill pre-existing rows during this migration.
    op.alter_column("v2_schema_migration_plans", "execution_json", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "v2_schema_migration_plans" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("v2_schema_migration_plans")}
        if "execution_json" in columns:
            op.drop_column("v2_schema_migration_plans", "execution_json")
