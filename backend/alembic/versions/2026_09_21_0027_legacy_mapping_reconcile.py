"""Reconcile databases stamped by the retired mapping-FK migration.

Those databases were initialized with ``create_all`` and already contain the
scenario tables, but they do not have a traversable Alembic path to the current
scenario head.  This merge migration keeps the old revision as a parent and
idempotently backfills the durable run fields introduced by 0026.
"""
from alembic import op
import sqlalchemy as sa


revision = "0027_legacy_mapping_reconcile"
down_revision = ("0003_mapping_fk_fix", "0026_scenario_run_stages")
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade():
    bind = op.get_bind()
    tables = _tables(bind)
    if "v2_scenario_runs" in tables:
        existing = _columns(bind, "v2_scenario_runs")
        additions = {
            "result_manifest": sa.Column("result_manifest", sa.JSON(), nullable=True),
            "started_at": sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            "completed_at": sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            "duration_ms": sa.Column("duration_ms", sa.Integer(), nullable=True),
            "celery_task_id": sa.Column("celery_task_id", sa.String(200), nullable=True),
        }
        for name, column in additions.items():
            if name not in existing:
                op.add_column("v2_scenario_runs", column)
    if "v2_scenario_run_stages" not in tables:
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
    # Compatibility migrations are intentionally non-destructive.
    pass
