"""Persist warnings and result artifacts separately from the Run manifest."""
from alembic import op
import sqlalchemy as sa

revision = "0030_supplier_run_evidence"
down_revision = "0029_supplier_run_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_scenario_run_warnings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("v2_scenario_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(100), nullable=False), sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False), sa.Column("object_ref", sa.JSON()),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "code", name="uq_scenario_run_warning_code"))
    op.create_index("ix_v2_scenario_run_warnings_run_id", "v2_scenario_run_warnings", ["run_id"])
    op.create_table("v2_scenario_run_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("v2_scenario_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_type", sa.String(80), nullable=False),
        sa.Column("data_view_id", sa.String(64), sa.ForeignKey("v2_query_data_views.id")),
        sa.Column("object_set_definition", sa.JSON()), sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(128), nullable=False),
        sa.UniqueConstraint("run_id", "artifact_type", name="uq_scenario_run_artifact_type"))
    op.create_index("ix_v2_scenario_run_artifacts_run_id", "v2_scenario_run_artifacts", ["run_id"])


def downgrade():
    op.drop_index("ix_v2_scenario_run_artifacts_run_id", table_name="v2_scenario_run_artifacts")
    op.drop_table("v2_scenario_run_artifacts")
    op.drop_index("ix_v2_scenario_run_warnings_run_id", table_name="v2_scenario_run_warnings")
    op.drop_table("v2_scenario_run_warnings")
