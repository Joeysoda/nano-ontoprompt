from alembic import op
import sqlalchemy as sa
revision = "0024_query_view_manifest_fields"
down_revision = "0023_query_jobs"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("v2_query_data_views", sa.Column("base_view_id", sa.String(length=64), nullable=True))
    op.add_column("v2_query_data_views", sa.Column("changeset_digest", sa.String(length=128), nullable=True))
    op.add_column("v2_query_data_views", sa.Column("changeset_version", sa.String(length=64), nullable=True))
    op.add_column("v2_query_data_views", sa.Column("builder_version", sa.String(length=64), nullable=False, server_default="query-view-builder-v1"))
    op.add_column("v2_query_data_views", sa.Column("index_version", sa.String(length=64), nullable=False, server_default="instance-index-v1"))
    # SQLite cannot ALTER a table to add constraints without a batch copy.
    # The application model still enforces the relationship on new schemas;
    # PostgreSQL receives the FK during incremental migration.
    if op.get_context().dialect.name != "sqlite":
        op.create_foreign_key("fk_query_view_base_view", "v2_query_data_views", "v2_query_data_views", ["base_view_id"], ["id"])
def downgrade():
    raise RuntimeError("Query view manifest history is retained; destructive downgrade is not supported")
