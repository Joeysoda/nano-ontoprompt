"""Add scheduled derived projections and immutable run evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0044_derived_projections"
down_revision = "0043_ontology_queries"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_derived_projections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("api_name", sa.String(100), nullable=False), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False), sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("spec_digest", sa.String(64), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=True), sa.Column("etag", sa.Integer(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_run_id", sa.String(36), nullable=True), sa.Column("schedule_error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ontology_id", "api_name", name="uq_derived_projection_api_name"),
        sa.CheckConstraint("status IN ('active','paused','retired')", name="ck_derived_projection_status"),
        sa.CheckConstraint("interval_minutes IS NULL OR interval_minutes BETWEEN 5 AND 1440", name="ck_derived_projection_interval"))
    op.create_index("ix_v2_derived_projections_ontology_id", "v2_derived_projections", ["ontology_id"])
    op.create_table("v2_derived_projection_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("projection_id", sa.String(36), sa.ForeignKey("v2_derived_projections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("logic_asset_run_id", sa.String(36), sa.ForeignKey("v2_logic_asset_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("query_version", sa.String(40), nullable=False),
        sa.Column("data_view_id", sa.String(64), sa.ForeignKey("v2_query_data_views.id", ondelete="SET NULL"), nullable=True),
        sa.Column("spec_digest", sa.String(64), nullable=False), sa.Column("input_digest", sa.String(64), nullable=True),
        sa.Column("dependency_digest", sa.String(64), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True), sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('completed','failed')", name="ck_derived_projection_run_status"))
    op.create_index("ix_v2_derived_projection_runs_projection_id", "v2_derived_projection_runs", ["projection_id"])


def downgrade():
    op.drop_table("v2_derived_projection_runs")
    op.drop_table("v2_derived_projections")
