"""Versioned local heterogeneous logic assets and execution records."""
from alembic import op
import sqlalchemy as sa

revision = "0019_logic_assets"
down_revision = "0018_agent_runs"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind(); inspector = sa.inspect(bind)
    if not inspector.has_table("v2_logic_assets"):
        op.create_table(
            "v2_logic_assets",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("asset_key", sa.String(120), nullable=False), sa.Column("name", sa.String(200), nullable=False),
            sa.Column("kind", sa.String(40), nullable=False), sa.Column("description", sa.Text(), nullable=False),
            sa.Column("implementation", sa.String(120), nullable=False), sa.Column("input_schema", sa.JSON(), nullable=False),
            sa.Column("output_schema", sa.JSON(), nullable=False), sa.Column("bindings", sa.JSON(), nullable=False),
            sa.Column("interface_key", sa.String(160), nullable=False, server_default=""),
            sa.Column("interface_version", sa.String(40), nullable=False, server_default="1.0.0"),
            sa.Column("executor_type", sa.String(40), nullable=False, server_default="local"),
            sa.Column("binding_spec", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("config", sa.JSON(), nullable=False), sa.Column("version", sa.String(40), nullable=False),
            sa.Column("status", sa.String(20), nullable=False), sa.Column("deterministic", sa.Boolean(), nullable=False),
            sa.Column("side_effect", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
        )
        op.create_index("ix_v2_logic_assets_ontology", "v2_logic_assets", ["ontology_id"])
    if not inspector.has_table("v2_logic_asset_runs"):
        op.create_table(
            "v2_logic_asset_runs", sa.Column("id", sa.String(), primary_key=True),
            sa.Column("asset_id", sa.String(), sa.ForeignKey("v2_logic_assets.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("asset_version", sa.String(40), nullable=False), sa.Column("inputs", sa.JSON(), nullable=False),
            sa.Column("output", sa.JSON()), sa.Column("status", sa.String(20), nullable=False), sa.Column("error", sa.JSON()),
            sa.Column("trace", sa.JSON()), sa.Column("duration_ms", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True)),
        )
        op.create_index("ix_v2_logic_asset_runs_ontology", "v2_logic_asset_runs", ["ontology_id"])

def downgrade():
    op.drop_table("v2_logic_asset_runs"); op.drop_table("v2_logic_assets")
