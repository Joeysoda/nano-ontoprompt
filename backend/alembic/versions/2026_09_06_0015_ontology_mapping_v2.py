"""Materialised ontology mapping, task trace and audit rule fields."""
from alembic import op
import sqlalchemy as sa


revision = "0015_ontology_mapping_v2"
down_revision = "0014_cmapss_temporal_class"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "logic_rules" in tables:
        for name, column in [
            ("condition_json", sa.Column("condition_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
            ("effect_json", sa.Column("effect_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
            ("evidence_json", sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
            ("model_invocation_id", sa.Column("model_invocation_id", sa.String(), nullable=True)),
            ("revision_id", sa.Column("revision_id", sa.String(), nullable=True)),
        ]:
            if not _has_column(bind, "logic_rules", name):
                op.add_column("logic_rules", column)
    if "entity_instances" in tables and not _has_column(bind, "entity_instances", "revision_id"):
        op.add_column("entity_instances", sa.Column("revision_id", sa.String(), nullable=True))
        op.create_index("ix_entity_instances_revision_id", "entity_instances", ["revision_id"], unique=False)
    if "v2_mapping_tasks" in tables:
        for name, column in [
            ("stage", sa.Column("stage", sa.String(length=40), nullable=False, server_default="queued")),
            ("progress", sa.Column("progress", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
            ("trace_json", sa.Column("trace_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))),
            ("source_plan_json", sa.Column("source_plan_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
            ("transfer_manifest_json", sa.Column("transfer_manifest_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
            ("schema_version", sa.Column("schema_version", sa.String(length=40), nullable=False, server_default="ontology-mapping-v2")),
        ]:
            if not _has_column(bind, "v2_mapping_tasks", name):
                op.add_column("v2_mapping_tasks", column)
    if "v2_model_invocations" in tables and not _has_column(bind, "v2_model_invocations", "phase"):
        op.add_column("v2_model_invocations", sa.Column("phase", sa.String(length=40), nullable=True))


def downgrade() -> None:
    # Production snapshots retain these fields; rollback is intentionally a no-op.
    pass
