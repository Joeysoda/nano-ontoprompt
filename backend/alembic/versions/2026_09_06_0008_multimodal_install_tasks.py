"""Persist multimodal catalog installation jobs."""
from alembic import op
import sqlalchemy as sa


revision = "0008_multimodal_install_tasks"
down_revision = "0007_workbench_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "v2_multimodal_install_tasks" in tables:
        return
    op.create_table(
        "v2_multimodal_install_tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("v2_multimodal_install_tasks")
