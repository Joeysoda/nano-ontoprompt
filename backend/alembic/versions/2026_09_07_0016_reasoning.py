"""Persist reasoning previews and saved result graphs."""
from alembic import op
import sqlalchemy as sa

revision = "0016_reasoning"
down_revision = "0015_ontology_mapping_v2"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy development startup may already have created this additive table.
    if sa.inspect(op.get_bind()).has_table("v2_reasoning_runs"):
        return
    op.create_table(
        "v2_reasoning_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_v2_reasoning_runs_ontology_id", "v2_reasoning_runs", ["ontology_id"])


def downgrade():
    op.drop_table("v2_reasoning_runs")
