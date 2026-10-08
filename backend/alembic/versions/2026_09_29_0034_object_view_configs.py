"""Published configured Object View runtime schemas."""
from alembic import op
import sqlalchemy as sa

revision = "0034_object_view_configs"
down_revision = "0033_tracking_scenarios"
branch_labels = None
depends_on = None


def upgrade():
    if "v2_object_view_configs" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "v2_object_view_configs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id"), nullable=False),
        sa.Column("object_type", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata_digest", sa.String(64), nullable=False),
        sa.Column("view_schema", sa.JSON(), nullable=False),
        sa.Column("published_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ontology_id", "object_type", "version", name="uq_v2_object_view_version"),
    )


def downgrade():
    if "v2_object_view_configs" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("v2_object_view_configs")
