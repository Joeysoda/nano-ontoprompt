"""Add versioned ontology queries and MCP scopes."""
from alembic import op
import sqlalchemy as sa

revision = "0043_ontology_queries"
down_revision = "0042_workshop_apps"
branch_labels = None
depends_on = None


def upgrade():
    legacy_tables = {"v2_ontology_queries", "v2_ontology_query_versions", "v2_mcp_scopes"}
    if legacy_tables.issubset(set(sa.inspect(op.get_bind()).get_table_names())):
        return
    op.create_table("v2_ontology_queries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("api_name", sa.String(100), nullable=False), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False), sa.Column("latest_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ontology_id", "api_name", name="uq_ontology_query_api_name"))
    op.create_index("ix_v2_ontology_queries_ontology_id", "v2_ontology_queries", ["ontology_id"])
    op.create_table("v2_ontology_query_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("query_id", sa.String(36), sa.ForeignKey("v2_ontology_queries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(40), nullable=False), sa.Column("major", sa.Integer(), nullable=False),
        sa.Column("minor", sa.Integer(), nullable=False), sa.Column("patch", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False), sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False), sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("query_id", "version", name="uq_ontology_query_version"))
    op.create_index("ix_v2_ontology_query_versions_query_id", "v2_ontology_query_versions", ["query_id"])
    op.create_table("v2_mcp_scopes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("queries", sa.JSON(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("token_prefix", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_v2_mcp_scopes_ontology_id", "v2_mcp_scopes", ["ontology_id"])


def downgrade():
    op.drop_table("v2_mcp_scopes")
    op.drop_table("v2_ontology_query_versions")
    op.drop_table("v2_ontology_queries")
