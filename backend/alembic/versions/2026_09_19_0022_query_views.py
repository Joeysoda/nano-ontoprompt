from alembic import op
import sqlalchemy as sa

revision = "0022_query_views"
down_revision = "0021_object_sets"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "v2_query_data_views",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("source_manifest_digest", sa.String(length=128), nullable=False),
        sa.Column("metadata_digest", sa.String(length=128), nullable=False),
        sa.Column("content_digest", sa.String(length=128), nullable=True),
        sa.Column("graph_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("object_count", sa.Integer(), nullable=False),
        sa.Column("edge_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refcount", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('building','validating','ready','failed','expired')", name="ck_query_view_status"),
        sa.CheckConstraint("object_count >= 0 AND edge_count >= 0 AND refcount >= 0", name="ck_query_view_counts"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("graph_key"),
    )
    op.create_index("ix_v2_query_data_views_ontology_id", "v2_query_data_views", ["ontology_id"])


def downgrade():
    raise RuntimeError("Query view history is retained; destructive downgrade is not supported")
