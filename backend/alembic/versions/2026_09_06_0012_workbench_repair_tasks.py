"""Repair workbench identity boundaries and persist recoverable tasks.

This migration is intentionally additive.  The existing demonstration database
contains partially imported datasets, so it backfills metadata and never drops
records or storage references.
"""
from alembic import op
import sqlalchemy as sa


revision = "0012_workbench_repair_tasks"
down_revision = "0011_construction_drafts"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _add_column(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if column.name not in _columns(bind, table):
        op.add_column(table, column)


def _has_unique(bind, table: str, columns: set[str]) -> bool:
    if table not in _tables(bind):
        return False
    return any(set(item.get("column_names") or []) == columns for item in sa.inspect(bind).get_unique_constraints(table))


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "ontology_projects" in tables:
        _add_column("ontology_projects", sa.Column("data_class", sa.String(length=30), nullable=True))
        op.execute(sa.text("""
            UPDATE ontology_projects
            SET data_class = CASE
              WHEN lower(COALESCE(build_mode, '')) = 'temporal_pipeline'
                OR lower(name) LIKE '%factorynet%'
                OR lower(name) LIKE '%c-mapss%' THEN 'temporal'
              WHEN lower(COALESCE(build_mode, '')) = 'multimodal_workbench'
                OR lower(name) LIKE '%i-badas%' THEN 'multimodal'
              ELSE 'regular'
            END
            WHERE data_class IS NULL OR data_class NOT IN ('regular', 'temporal', 'multimodal')
        """))
        op.alter_column("ontology_projects", "data_class", existing_type=sa.String(length=30), nullable=False, server_default="regular")

    if "v2_datasets" in tables:
        _add_column("v2_datasets", sa.Column("readiness", sa.String(length=30), nullable=True))
        # FactoryNet and temporal upload metadata are authoritative; name-based
        # recognition keeps legacy C-MAPSS entries usable until their source
        # package is rebuilt.
        op.execute(sa.text("""
            UPDATE v2_datasets
            SET data_class = 'temporal'
            WHERE lower(name) LIKE '%factorynet%'
               OR lower(name) LIKE '%c-mapss%'
               OR lower(CAST(schema_json AS TEXT)) LIKE '%temporal_source%'
        """))
        op.execute(sa.text("""
            UPDATE v2_datasets
            SET readiness = CASE
              WHEN latest_version_id IS NULL THEN 'missing_version'
              ELSE 'ready'
            END
            WHERE readiness IS NULL
        """))
        if "v2_dataset_versions" in tables:
            op.execute(sa.text("""
                UPDATE v2_datasets
                SET latest_version_id = (
                  SELECT id FROM v2_dataset_versions
                  WHERE dataset_id = v2_datasets.id
                  ORDER BY version_no DESC
                  LIMIT 1
                )
                WHERE latest_version_id IS NULL
            """))

    if "v2_construction_drafts" in tables:
        # Old drafts retain their target.  New "create" drafts intentionally
        # defer ontology creation until the final build transaction.
        op.alter_column("v2_construction_drafts", "ontology_id", existing_type=sa.String(), nullable=True)
        _add_column("v2_construction_drafts", sa.Column("target_mode", sa.String(length=20), nullable=True))
        _add_column("v2_construction_drafts", sa.Column("new_ontology_name", sa.String(length=200), nullable=True))
        _add_column("v2_construction_drafts", sa.Column("new_ontology_domain", sa.String(length=100), nullable=True))
        _add_column("v2_construction_drafts", sa.Column("mapping_task_id", sa.String(), nullable=True))
        op.execute(sa.text("UPDATE v2_construction_drafts SET target_mode = CASE WHEN ontology_id IS NULL THEN 'create' ELSE 'append' END WHERE target_mode IS NULL"))

    if "v2_multimodal_samples" in tables and not _has_unique(bind, "v2_multimodal_samples", {"dataset_version_id", "sample_key"}):
        op.create_unique_constraint("uq_v2_multimodal_samples_version_key", "v2_multimodal_samples", ["dataset_version_id", "sample_key"])

    if "v2_media_items" in tables:
        _add_column("v2_media_items", sa.Column("source_path", sa.Text(), nullable=True))
        op.execute(sa.text("UPDATE v2_media_items SET source_path = storage_uri WHERE source_path IS NULL"))
        if not _has_unique(bind, "v2_media_items", {"dataset_version_id", "sample_id", "asset_role", "source_path"}):
            op.create_unique_constraint("uq_v2_media_items_sample_asset_source", "v2_media_items", ["dataset_version_id", "sample_id", "asset_role", "source_path"])

    if "v2_mapping_tasks" not in tables:
        op.create_table(
            "v2_mapping_tasks",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("draft_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("model_route", sa.String(length=200), nullable=True),
            sa.Column("payload_hash", sa.String(length=64), nullable=True),
            sa.Column("result_json", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["draft_id"], ["v2_construction_drafts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_v2_mapping_tasks_draft_id", "v2_mapping_tasks", ["draft_id"])

    if "v2_data_import_tasks" not in tables:
        op.create_table(
            "v2_data_import_tasks",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("connection_id", sa.String(), nullable=True),
            sa.Column("dataset_id", sa.String(), nullable=True),
            sa.Column("data_class", sa.String(length=30), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("config_json", sa.JSON(), nullable=False),
            sa.Column("progress", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["connection_id"], ["v2_connections.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["dataset_id"], ["v2_datasets.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_v2_data_import_tasks_dataset_id", "v2_data_import_tasks", ["dataset_id"])

    if "v2_model_invocations" not in tables:
        op.create_table(
            "v2_model_invocations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("draft_id", sa.String(), nullable=True),
            sa.Column("construction_run_id", sa.String(), nullable=True),
            sa.Column("audit_task_id", sa.String(), nullable=True),
            sa.Column("mapping_task_id", sa.String(), nullable=True),
            sa.Column("route_alias", sa.String(length=200), nullable=False),
            sa.Column("provider", sa.String(length=100), nullable=True),
            sa.Column("model_name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("request_ciphertext", sa.Text(), nullable=True),
            sa.Column("response_ciphertext", sa.Text(), nullable=True),
            sa.Column("request_hash", sa.String(length=64), nullable=True),
            sa.Column("response_hash", sa.String(length=64), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["draft_id"], ["v2_construction_drafts.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["construction_run_id"], ["v2_construction_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["mapping_task_id"], ["v2_mapping_tasks.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_v2_model_invocations_created_at", "v2_model_invocations", ["created_at"])


def downgrade() -> None:
    # Workbench tasks and provenance are intentionally retained; removing this
    # migration must not silently erase user data from an existing workstation.
    pass
