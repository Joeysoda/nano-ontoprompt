"""Add durable temporal replay and batch checkpoints."""
from alembic import op
import sqlalchemy as sa


revision = "0017_temporal_replays"
down_revision = "0016_dynamic_ontology_what_if"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "v2_temporal_replays" not in tables:
        op.create_table(
            "v2_temporal_replays",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("dataset_id", sa.String(), sa.ForeignKey("v2_datasets.id", ondelete="SET NULL"), nullable=True),
            sa.Column("dataset_version_id", sa.String(), sa.ForeignKey("v2_dataset_versions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("source_id", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("time_kind", sa.String(length=20), nullable=False),
            sa.Column("entity_column", sa.String(length=200), nullable=True),
            sa.Column("time_column", sa.String(length=200), nullable=True),
            sa.Column("series_ids", sa.JSON(), nullable=False),
            sa.Column("start_time", sa.Float(), nullable=True),
            sa.Column("end_time", sa.Float(), nullable=True),
            sa.Column("window_seconds", sa.Float(), nullable=False),
            sa.Column("speed", sa.Float(), nullable=False),
            sa.Column("current_time", sa.Float(), nullable=True),
            sa.Column("current_batch_index", sa.Integer(), nullable=False),
            sa.Column("total_batches", sa.Integer(), nullable=False),
            sa.Column("source_rows", sa.Integer(), nullable=False),
            sa.Column("selected_rows", sa.Integer(), nullable=False),
            sa.Column("normalized_rows", sa.Integer(), nullable=False),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("state", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("pause_requested", sa.Boolean(), nullable=False),
            sa.Column("step_requested", sa.Boolean(), nullable=False),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False),
            sa.Column("worker_token", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_v2_temporal_replays_ontology_id", "v2_temporal_replays", ["ontology_id"])
        op.create_index("ix_v2_temporal_replays_status", "v2_temporal_replays", ["status"])

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_temporal_replay_batches" not in tables:
        op.create_table(
            "v2_temporal_replay_batches",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("replay_id", sa.String(), sa.ForeignKey("v2_temporal_replays.id", ondelete="CASCADE"), nullable=False),
            sa.Column("batch_no", sa.Integer(), nullable=False),
            sa.Column("time_from", sa.Float(), nullable=True),
            sa.Column("time_to", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("source_row_ids", sa.JSON(), nullable=False),
            sa.Column("source_rows", sa.Integer(), nullable=False),
            sa.Column("normalized_rows", sa.Integer(), nullable=False),
            sa.Column("nodes_written", sa.Integer(), nullable=False),
            sa.Column("edges_written", sa.Integer(), nullable=False),
            sa.Column("issue_count", sa.Integer(), nullable=False),
            sa.Column("latest_rows", sa.JSON(), nullable=False),
            sa.Column("payload_hash", sa.String(length=64), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("replay_id", "batch_no", name="uq_v2_temporal_replay_batch_no"),
        )
        op.create_index("ix_v2_temporal_replay_batches_replay_id", "v2_temporal_replay_batches", ["replay_id"])


def downgrade() -> None:
    # Replay checkpoints are operational evidence.  Keep a conservative
    # downgrade so an accidental migration rollback cannot delete them.
    pass
