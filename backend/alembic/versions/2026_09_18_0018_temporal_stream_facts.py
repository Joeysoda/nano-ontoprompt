"""Add event-level temporal stream facts and published data-model snapshots."""
from alembic import op
import sqlalchemy as sa


revision = "0018_temporal_stream_facts"
down_revision = "0017_temporal_replays"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns(table)}
    if column.name not in columns:
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "ontology_projects" in tables:
        _add_column_if_missing(
            "ontology_projects",
            sa.Column("current_data_snapshot_id", sa.String(), nullable=True),
        )
        try:
            op.create_index(
                "ix_ontology_projects_current_data_snapshot_id",
                "ontology_projects",
                ["current_data_snapshot_id"],
            )
        except Exception:
            pass

    if "v2_temporal_replays" in tables:
        replay_columns = {
            "source_mode": sa.Column("source_mode", sa.String(length=20), nullable=False, server_default="file_replay"),
            "schema_revision_id": sa.Column("schema_revision_id", sa.String(), nullable=True),
            "graph_namespace": sa.Column("graph_namespace", sa.String(length=240), nullable=True),
            "event_interval_ms": sa.Column("event_interval_ms", sa.Integer(), nullable=False, server_default="1000"),
            "current_event_index": sa.Column("current_event_index", sa.Integer(), nullable=False, server_default="-1"),
            "total_events": sa.Column("total_events", sa.Integer(), nullable=False, server_default="0"),
            "committed_events": sa.Column("committed_events", sa.Integer(), nullable=False, server_default="0"),
            "watermark_ordinal": sa.Column("watermark_ordinal", sa.Numeric(24, 9), nullable=True),
            "watermark_sequence": sa.Column("watermark_sequence", sa.Integer(), nullable=True),
            "published_snapshot_id": sa.Column("published_snapshot_id", sa.String(), nullable=True),
            "published_at": sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        }
        existing = {item["name"] for item in inspector.get_columns("v2_temporal_replays")}
        for name, column in replay_columns.items():
            if name not in existing:
                op.add_column("v2_temporal_replays", column)

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_temporal_stream_events" not in tables:
        op.create_table(
            "v2_temporal_stream_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("replay_id", sa.String(), sa.ForeignKey("v2_temporal_replays.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_key", sa.String(length=300), nullable=False),
            sa.Column("episode_id", sa.String(length=200), nullable=False),
            sa.Column("entity_key", sa.String(length=200), nullable=False),
            sa.Column("ordinal", sa.Numeric(24, 9), nullable=False),
            sa.Column("source_sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source_row_id", sa.String(length=200), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("source_ref", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("payload_hash", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("replay_id", "event_key", name="uq_v2_temporal_stream_event_key"),
        )
        op.create_index("ix_v2_temporal_stream_events_replay_id", "v2_temporal_stream_events", ["replay_id"])
        op.create_index("ix_v2_temporal_stream_events_watermark", "v2_temporal_stream_events", ["replay_id", "ordinal", "source_sequence"])

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_temporal_facts" not in tables:
        op.create_table(
            "v2_temporal_facts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("replay_id", sa.String(), sa.ForeignKey("v2_temporal_replays.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_id", sa.String(length=300), nullable=False),
            sa.Column("predicate", sa.String(length=160), nullable=False),
            sa.Column("object_id", sa.String(length=300), nullable=True),
            sa.Column("object_value", sa.JSON(), nullable=True),
            sa.Column("state_key", sa.String(length=100), nullable=False, server_default=""),
            sa.Column("valid_from_ordinal", sa.Numeric(24, 9), nullable=False),
            sa.Column("valid_to_ordinal", sa.Numeric(24, 9), nullable=True),
            sa.Column("source_event_id", sa.String(), sa.ForeignKey("v2_temporal_stream_events.id", ondelete="SET NULL"), nullable=True),
            sa.Column("evidence_ref_id", sa.String(), nullable=True),
            sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_v2_temporal_facts_replay_id", "v2_temporal_facts", ["replay_id"])
        op.create_index("ix_v2_temporal_facts_state", "v2_temporal_facts", ["replay_id", "state_key", "subject_id"])
        op.create_index("ix_v2_temporal_facts_interval", "v2_temporal_facts", ["replay_id", "valid_from_ordinal", "valid_to_ordinal"])

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_data_model_snapshots" not in tables:
        op.create_table(
            "v2_data_model_snapshots",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("schema_revision_id", sa.String(), nullable=True),
            sa.Column("replay_id", sa.String(), sa.ForeignKey("v2_temporal_replays.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("dataset_version_id", sa.String(), nullable=True),
            sa.Column("graph_namespace", sa.String(length=240), nullable=False),
            sa.Column("through_ordinal", sa.Numeric(24, 9), nullable=True),
            sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("node_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("edge_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("fact_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("snapshot_hash", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="published"),
            sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_v2_data_model_snapshots_ontology_id", "v2_data_model_snapshots", ["ontology_id"])
        op.create_index("ix_v2_data_model_snapshots_replay_id", "v2_data_model_snapshots", ["replay_id"])


def downgrade() -> None:
    # Dynamic stream records and published snapshots are audit data.  Keep a
    # conservative downgrade that never deletes a user's temporal history.
    pass
