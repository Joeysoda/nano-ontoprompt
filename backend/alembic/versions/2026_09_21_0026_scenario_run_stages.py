"""persisted scenario run workflow stages and result manifest"""
from alembic import op
import sqlalchemy as sa

revision = "0026_scenario_run_stages"
down_revision = "0025_scenarios"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("v2_scenario_runs", sa.Column("result_manifest", sa.JSON(), nullable=True))
    op.add_column("v2_scenario_runs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("v2_scenario_runs", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("v2_scenario_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("v2_scenario_runs", sa.Column("celery_task_id", sa.String(200), nullable=True))
    op.create_table(
        "v2_scenario_run_stages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("v2_scenario_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage_key", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.UniqueConstraint("run_id", "stage_key", name="uq_scenario_run_stage"),
        sa.CheckConstraint("status IN ('pending','running','completed','failed','cancelled','skipped')", name="ck_scenario_run_stage_status"),
    )
    op.create_index("ix_v2_scenario_run_stages_run_id", "v2_scenario_run_stages", ["run_id"])

def downgrade():
    op.drop_index("ix_v2_scenario_run_stages_run_id", table_name="v2_scenario_run_stages")
    op.drop_table("v2_scenario_run_stages")
    for name in ("celery_task_id", "duration_ms", "completed_at", "started_at", "result_manifest"):
        op.drop_column("v2_scenario_runs", name)
