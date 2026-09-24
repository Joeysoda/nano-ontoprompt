"""Add runtime contract metadata to stored logic assets."""
from alembic import op
import sqlalchemy as sa

revision = "0020_logic_contracts"
down_revision = "0019_logic_assets"
branch_labels = None
depends_on = None

def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("v2_logic_assets")}
    if "interface_key" not in columns:
        op.add_column("v2_logic_assets", sa.Column("interface_key", sa.String(160), nullable=False, server_default=""))
    if "interface_version" not in columns:
        op.add_column("v2_logic_assets", sa.Column("interface_version", sa.String(40), nullable=False, server_default="1.0.0"))
    if "executor_type" not in columns:
        op.add_column("v2_logic_assets", sa.Column("executor_type", sa.String(40), nullable=False, server_default="local"))
    if "binding_spec" not in columns:
        op.add_column("v2_logic_assets", sa.Column("binding_spec", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    if not inspector.has_table("v2_logic_derived_facts"):
        op.create_table(
            "v2_logic_derived_facts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("run_id", sa.String(), sa.ForeignKey("v2_logic_asset_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("asset_id", sa.String(), sa.ForeignKey("v2_logic_assets.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_id", sa.String(), nullable=True),
            sa.Column("fact_key", sa.String(200), nullable=False),
            sa.Column("value", sa.JSON(), nullable=True),
            sa.Column("source_path", sa.String(300), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True)),
        )
        op.create_index("ix_v2_logic_derived_facts_ontology", "v2_logic_derived_facts", ["ontology_id"])

def downgrade():
    op.drop_table("v2_logic_derived_facts")
    for name in ("binding_spec", "executor_type", "interface_version", "interface_key"):
        op.drop_column("v2_logic_assets", name)
