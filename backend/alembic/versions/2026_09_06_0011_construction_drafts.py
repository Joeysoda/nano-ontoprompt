"""Add recoverable five-step construction drafts."""
from alembic import op
import sqlalchemy as sa


revision = "0011_construction_drafts"
down_revision = "0010_audit_repair_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "v2_construction_drafts" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "v2_construction_drafts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("data_class", sa.String(length=30), nullable=False),
        sa.Column("privacy_level", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("selection_json", sa.JSON(), nullable=False),
        sa.Column("processing_json", sa.JSON(), nullable=False),
        sa.Column("preflight_json", sa.JSON(), nullable=False),
        sa.Column("mapping_json", sa.JSON(), nullable=False),
        sa.Column("build_run_id", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["v2_datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("v2_construction_drafts")
