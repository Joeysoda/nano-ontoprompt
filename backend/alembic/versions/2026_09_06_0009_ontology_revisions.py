"""Add immutable ontology revision snapshots."""
from alembic import op
import sqlalchemy as sa


revision = "0009_ontology_revisions"
down_revision = "0008_multimodal_install_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "ontology_revisions" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "ontology_revisions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(), nullable=True),
        sa.Column("source_run_id", sa.String(), nullable=True),
        sa.Column("graph_namespace", sa.String(length=240), nullable=True),
        sa.Column("snapshot_uri", sa.Text(), nullable=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["ontology_revisions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_run_id"], ["v2_construction_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ontology_revisions_ontology_id", "ontology_revisions", ["ontology_id"])


def downgrade() -> None:
    op.drop_index("ix_ontology_revisions_ontology_id", table_name="ontology_revisions")
    op.drop_table("ontology_revisions")
