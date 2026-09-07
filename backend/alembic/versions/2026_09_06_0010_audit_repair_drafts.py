"""Persist reviewable audit repair drafts."""
from alembic import op
import sqlalchemy as sa


revision = "0010_audit_repair_drafts"
down_revision = "0009_ontology_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "audit_tasks" not in set(sa.inspect(bind).get_table_names()):
        return
    cols = {item["name"] for item in sa.inspect(bind).get_columns("audit_tasks")}
    if "repair_draft" not in cols:
        op.add_column("audit_tasks", sa.Column("repair_draft", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "audit_tasks" in set(sa.inspect(bind).get_table_names()):
        cols = {item["name"] for item in sa.inspect(bind).get_columns("audit_tasks")}
        if "repair_draft" in cols:
            op.drop_column("audit_tasks", "repair_draft")
