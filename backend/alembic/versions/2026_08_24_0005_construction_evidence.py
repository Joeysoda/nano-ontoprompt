"""Add construction-run, evidence and multimodal fragment records."""
from alembic import op
import sqlalchemy as sa


revision = "0005_construction_evidence"
down_revision = "0004_audit_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "v2_construction_runs" not in tables:
        op.create_table(
            "v2_construction_runs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("dataset_id", sa.String(), nullable=True),
            sa.Column("mode", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("model_name", sa.String(length=200), nullable=True),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("progress", sa.JSON(), nullable=False),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("artifact_uri", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dataset_id"], ["v2_datasets.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
    if "v2_evidence_refs" not in tables:
        op.create_table(
            "v2_evidence_refs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("construction_run_id", sa.String(), nullable=False),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("assertion_id", sa.String(length=300), nullable=False),
            sa.Column("assertion_kind", sa.String(length=30), nullable=False),
            sa.Column("source_dataset_id", sa.String(), nullable=True),
            sa.Column("source_version", sa.String(length=80), nullable=True),
            sa.Column("source_file", sa.Text(), nullable=True),
            sa.Column("source_row_id", sa.String(length=200), nullable=True),
            sa.Column("source_media_id", sa.String(), nullable=True),
            sa.Column("extractor", sa.String(length=40), nullable=False),
            sa.Column("model_name", sa.String(length=200), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("confidence_method", sa.String(length=80), nullable=True),
            sa.Column("evidence_text", sa.Text(), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["construction_run_id"], ["v2_construction_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    if "v2_extracted_fragments" not in tables:
        op.create_table(
            "v2_extracted_fragments",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("media_item_id", sa.String(), nullable=False),
            sa.Column("dataset_version_id", sa.String(), nullable=False),
            sa.Column("fragment_type", sa.String(length=30), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("locator", sa.JSON(), nullable=False),
            sa.Column("extractor", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["media_item_id"], ["v2_media_items.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dataset_version_id"], ["v2_dataset_versions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    op.drop_table("v2_extracted_fragments")
    op.drop_table("v2_evidence_refs")
    op.drop_table("v2_construction_runs")
