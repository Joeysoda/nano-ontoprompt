"""Store temporal dataset profiles and validated LLM suggestions."""
from alembic import op
import sqlalchemy as sa


revision = "0006_temporal_profiles"
down_revision = "0005_construction_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "v2_temporal_dataset_profiles" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "v2_temporal_dataset_profiles",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("dataset_id", sa.String(), nullable=False),
            sa.Column("dataset_version_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("deterministic_profile", sa.JSON(), nullable=False),
            sa.Column("llm_suggestion", sa.JSON(), nullable=False),
            sa.Column("model_name", sa.String(length=200), nullable=True),
            sa.Column("model_config_id", sa.String(), nullable=True),
            sa.Column("prompt_version", sa.String(length=80), nullable=True),
            sa.Column("llm_used", sa.Boolean(), nullable=False),
            sa.Column("response_hash", sa.String(length=64), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["dataset_id"], ["v2_datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dataset_version_id"], ["v2_dataset_versions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_v2_temporal_dataset_profiles_dataset_version", "v2_temporal_dataset_profiles", ["dataset_version_id"])


def downgrade() -> None:
    op.drop_index("ix_v2_temporal_dataset_profiles_dataset_version", table_name="v2_temporal_dataset_profiles")
    op.drop_table("v2_temporal_dataset_profiles")
