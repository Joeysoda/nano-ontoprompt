"""Persist the live/Scenario execution context on Action runs."""

from alembic import op
import sqlalchemy as sa


revision = "0032_action_run_execution_context"
down_revision = "0031_merge_temporal_scenario"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "v2_ontology_action_runs",
        sa.Column("execution_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    # SQLite cannot ALTER TABLE ... DROP DEFAULT.  Retaining the harmless
    # default there keeps the migration executable in the project's test DB;
    # PostgreSQL removes it after existing rows have been backfilled.
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column("v2_ontology_action_runs", "execution_context", server_default=None)


def downgrade() -> None:
    op.drop_column("v2_ontology_action_runs", "execution_context")
