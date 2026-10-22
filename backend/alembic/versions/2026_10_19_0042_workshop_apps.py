"""Add user-built Workshop-style applications with immutable versions."""
from alembic import op
import sqlalchemy as sa

revision = "0042_workshop_apps"
down_revision = "0041_decision_packages"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_workshop_apps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("head_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_v2_workshop_apps_ontology_id", "v2_workshop_apps", ["ontology_id"])
    op.create_table("v2_workshop_app_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("app_id", sa.String(36), sa.ForeignKey("v2_workshop_apps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False), sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("app_id", "version", name="uq_workshop_app_version"))
    op.create_index("ix_v2_workshop_app_versions_app_id", "v2_workshop_app_versions", ["app_id"])


def downgrade():
    op.drop_index("ix_v2_workshop_app_versions_app_id", table_name="v2_workshop_app_versions")
    op.drop_table("v2_workshop_app_versions")
    op.drop_index("ix_v2_workshop_apps_ontology_id", table_name="v2_workshop_apps")
    op.drop_table("v2_workshop_apps")
