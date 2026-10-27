"""Saved Vertex-style graph resources with immutable versions."""
from alembic import op
import sqlalchemy as sa


revision = "0039_graph_views"
down_revision = "0038_rule_alert_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if {"v2_graph_views", "v2_graph_view_versions"}.issubset(
        set(sa.inspect(op.get_bind()).get_table_names())
    ):
        return
    if {"v2_graph_views", "v2_graph_view_versions"}.issubset(
        set(sa.inspect(op.get_bind()).get_table_names())
    ):
        return
    if {"v2_graph_views", "v2_graph_view_versions"}.issubset(
        set(sa.inspect(op.get_bind()).get_table_names())
    ):
        return
    op.create_table(
        "v2_graph_views",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("head_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_graph_views_ontology_id", "v2_graph_views", ["ontology_id"])
    op.create_table(
        "v2_graph_view_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("graph_view_id", sa.String(36), sa.ForeignKey("v2_graph_views.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column("data_view_id", sa.String(64), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("graph_view_id", "version", name="uq_v2_graph_view_version"),
    )
    op.create_index("ix_v2_graph_view_versions_graph_view_id", "v2_graph_view_versions", ["graph_view_id"])


def downgrade() -> None:
    op.drop_table("v2_graph_view_versions")
    op.drop_table("v2_graph_views")
