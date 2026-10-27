"""Align the live schema with the current ORM contracts.

Revision ID: 0036_plan_a_schema_alignment
Revises: 0035_merge_semantic_runtime
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0036_plan_a_schema_alignment"
down_revision: Union[str, None] = "0035_merge_semantic_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_before_constraints() -> None:
    """Make historical nullable columns safe to tighten in place."""
    connection = op.get_bind()
    statements = (
        ("audit_tasks", "cancel_requested", "FALSE"),
        ("audit_tasks", "progress", "'{}'"),
        ("entity_instances", "row_data", "'{}'"),
        ("v2_agent_runs", "created_at", "CURRENT_TIMESTAMP"),
        ("v2_construction_drafts", "target_mode", "'append'"),
        ("v2_construction_runs", "cancel_requested", "FALSE"),
        ("v2_datasets", "data_class", "'regular'"),
        ("v2_datasets", "privacy_level", "'standard'"),
        ("v2_logic_asset_runs", "created_at", "CURRENT_TIMESTAMP"),
        ("v2_logic_assets", "created_at", "CURRENT_TIMESTAMP"),
        ("v2_logic_assets", "updated_at", "CURRENT_TIMESTAMP"),
        ("v2_logic_derived_facts", "created_at", "CURRENT_TIMESTAMP"),
        ("v2_media_items", "asset_role", "'primary'"),
        ("v2_media_items", "metadata_json", "'{}'"),
        ("v2_reasoning_runs", "created_at", "CURRENT_TIMESTAMP"),
    )
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    for table, column, value in statements:
        if table not in tables:
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        if column not in columns:
            continue
        connection.execute(sa.text(
            f"UPDATE {table} SET {column} = {value} WHERE {column} IS NULL"
        ))


def _alter_columns() -> None:
    """Apply type/nullability changes with backend-neutral SQLAlchemy types."""
    if op.get_context().dialect.name == "sqlite":
        # SQLite has no ALTER COLUMN.  Alembic's batch implementation
        # recreates each affected table while preserving rows and indexes.
        with op.batch_alter_table("audit_tasks", recreate="always") as batch:
            batch.alter_column("status", existing_type=sa.String(length=20), type_=sa.String(length=30), existing_nullable=False)
            batch.alter_column("cancel_requested", existing_type=sa.Boolean(), nullable=False)
            batch.alter_column("progress", existing_type=sa.JSON(), nullable=False)
        with op.batch_alter_table("entity_instances", recreate="always") as batch:
            batch.alter_column("row_data", existing_type=sa.JSON(), nullable=False)
        with op.batch_alter_table("v2_agent_runs", recreate="always") as batch:
            batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        with op.batch_alter_table("v2_construction_drafts", recreate="always") as batch:
            batch.alter_column("target_mode", existing_type=sa.String(length=20), nullable=False)
        with op.batch_alter_table("v2_construction_runs", recreate="always") as batch:
            batch.alter_column("status", existing_type=sa.String(length=20), type_=sa.String(length=30), existing_nullable=False)
            batch.alter_column("cancel_requested", existing_type=sa.Boolean(), nullable=False)
        with op.batch_alter_table("v2_datasets", recreate="always") as batch:
            batch.alter_column("data_class", existing_type=sa.String(length=30), nullable=False)
            batch.alter_column("privacy_level", existing_type=sa.String(length=20), nullable=False)
        with op.batch_alter_table("v2_logic_asset_runs", recreate="always") as batch:
            batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        with op.batch_alter_table("v2_logic_assets", recreate="always") as batch:
            batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
            batch.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        with op.batch_alter_table("v2_logic_derived_facts", recreate="always") as batch:
            batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        with op.batch_alter_table("v2_media_items", recreate="always") as batch:
            batch.alter_column("asset_role", existing_type=sa.String(length=30), nullable=False)
            batch.alter_column("metadata_json", existing_type=sa.JSON(), nullable=False)
        with op.batch_alter_table("v2_reasoning_runs", recreate="always") as batch:
            batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        return
    op.alter_column("audit_tasks", "status", existing_type=sa.String(length=20), type_=sa.String(length=30), existing_nullable=False)
    op.alter_column("audit_tasks", "cancel_requested", existing_type=sa.Boolean(), nullable=False)
    op.alter_column("audit_tasks", "progress", existing_type=sa.JSON(), nullable=False)
    op.alter_column("entity_instances", "row_data", existing_type=sa.JSON(), nullable=False)
    op.alter_column("v2_agent_runs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("v2_construction_drafts", "target_mode", existing_type=sa.String(length=20), nullable=False)
    op.alter_column("v2_construction_runs", "status", existing_type=sa.String(length=20), type_=sa.String(length=30), existing_nullable=False)
    op.alter_column("v2_construction_runs", "cancel_requested", existing_type=sa.Boolean(), nullable=False)
    op.alter_column("v2_datasets", "data_class", existing_type=sa.String(length=30), nullable=False)
    op.alter_column("v2_datasets", "privacy_level", existing_type=sa.String(length=20), nullable=False)
    op.alter_column("v2_logic_asset_runs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("v2_logic_assets", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("v2_logic_assets", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("v2_logic_derived_facts", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("v2_media_items", "asset_role", existing_type=sa.String(length=30), nullable=False)
    op.alter_column("v2_media_items", "metadata_json", existing_type=sa.JSON(), nullable=False)
    op.alter_column("v2_reasoning_runs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    # Some pre-Alembic development databases were created from an older ORM
    # snapshot.  This revision only tightens columns/constraints that already
    # existed in that snapshot; attempting those ALTERs when the columns are
    # absent rolls back unrelated, additive migrations on the other branch.
    # Later model-specific migrations and create_all provide the tables needed
    # by the local workbench, so make this historical alignment a no-op there.
    inspector = sa.inspect(op.get_bind())
    audit_columns = (
        {column["name"] for column in inspector.get_columns("audit_tasks")}
        if "audit_tasks" in set(inspector.get_table_names())
        else set()
    )
    if not {"cancel_requested", "progress"}.issubset(audit_columns):
        return
    _backfill_before_constraints()
    _alter_columns()

    # The historical SQLite migrations intentionally omit incremental FK
    # changes that SQLite cannot apply without a full table rebuild.  The ORM
    # still enforces these relationships for local development; PostgreSQL,
    # the deployment database, receives the durable constraints below.
    if op.get_context().dialect.name == "sqlite":
        return

    op.drop_constraint("extraction_tasks_prompt_id_fkey", "extraction_tasks", type_="foreignkey")
    op.drop_constraint("extraction_tasks_model_id_fkey", "extraction_tasks", type_="foreignkey")
    op.create_foreign_key(
        "fk_extraction_tasks_prompt_id_set_null", "extraction_tasks", "prompts",
        ["prompt_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_extraction_tasks_model_id_set_null", "extraction_tasks", "model_configs",
        ["model_id"], ["id"], ondelete="SET NULL"
    )
    op.drop_constraint("ontology_revisions_source_run_id_fkey", "ontology_revisions", type_="foreignkey")

    op.drop_index("ix_v2_logic_asset_runs_ontology", table_name="v2_logic_asset_runs")
    op.create_index("ix_v2_logic_asset_runs_ontology_id", "v2_logic_asset_runs", ["ontology_id"])
    op.drop_index("ix_v2_logic_assets_ontology", table_name="v2_logic_assets")
    op.create_index("ix_v2_logic_assets_ontology_id", "v2_logic_assets", ["ontology_id"])
    op.drop_index("ix_v2_logic_derived_facts_ontology", table_name="v2_logic_derived_facts")
    op.create_index("ix_v2_logic_derived_facts_ontology_id", "v2_logic_derived_facts", ["ontology_id"])

    op.drop_constraint("uq_v2_media_items_sample_asset_source", "v2_media_items", type_="unique")
    op.create_foreign_key(
        "fk_v2_media_items_sample_id", "v2_media_items", "v2_multimodal_samples",
        ["sample_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_constraint("uq_v2_multimodal_samples_version_key", "v2_multimodal_samples", type_="unique")
    op.create_foreign_key(
        "fk_v2_query_data_views_source_snapshot_id", "v2_query_data_views", "v2_data_model_snapshots",
        ["source_snapshot_id"], ["id"]
    )
    op.drop_index("ix_v2_temporal_dataset_profiles_dataset_version", table_name="v2_temporal_dataset_profiles")
    op.create_index("ix_v2_temporal_dataset_profiles_dataset_version_id", "v2_temporal_dataset_profiles", ["dataset_version_id"])


def downgrade() -> None:
    op.drop_index("ix_v2_temporal_dataset_profiles_dataset_version_id", table_name="v2_temporal_dataset_profiles")
    op.create_index("ix_v2_temporal_dataset_profiles_dataset_version", "v2_temporal_dataset_profiles", ["dataset_version_id"])
    op.drop_constraint("fk_v2_query_data_views_source_snapshot_id", "v2_query_data_views", type_="foreignkey")
    op.create_unique_constraint("uq_v2_multimodal_samples_version_key", "v2_multimodal_samples", ["dataset_version_id", "sample_key"])
    op.drop_constraint("fk_v2_media_items_sample_id", "v2_media_items", type_="foreignkey")
    op.create_unique_constraint("uq_v2_media_items_sample_asset_source", "v2_media_items", ["dataset_version_id", "sample_id", "asset_role", "source_path"])
    op.alter_column("v2_media_items", "metadata_json", existing_type=sa.JSON(), nullable=True)
    op.alter_column("v2_media_items", "asset_role", existing_type=sa.String(length=30), nullable=True)
    op.drop_index("ix_v2_logic_derived_facts_ontology_id", table_name="v2_logic_derived_facts")
    op.create_index("ix_v2_logic_derived_facts_ontology", "v2_logic_derived_facts", ["ontology_id"])
    op.alter_column("v2_logic_derived_facts", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.drop_index("ix_v2_logic_assets_ontology_id", table_name="v2_logic_assets")
    op.create_index("ix_v2_logic_assets_ontology", "v2_logic_assets", ["ontology_id"])
    op.alter_column("v2_logic_assets", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("v2_logic_assets", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.drop_index("ix_v2_logic_asset_runs_ontology_id", table_name="v2_logic_asset_runs")
    op.create_index("ix_v2_logic_asset_runs_ontology", "v2_logic_asset_runs", ["ontology_id"])
    op.alter_column("v2_logic_asset_runs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("v2_datasets", "privacy_level", existing_type=sa.String(length=20), nullable=True)
    op.alter_column("v2_datasets", "data_class", existing_type=sa.String(length=30), nullable=True)
    op.alter_column("v2_construction_runs", "cancel_requested", existing_type=sa.Boolean(), nullable=True)
    op.alter_column("v2_construction_runs", "status", existing_type=sa.String(length=30), type_=sa.String(length=20), existing_nullable=False)
    op.alter_column("v2_construction_drafts", "target_mode", existing_type=sa.String(length=20), nullable=True)
    op.alter_column("v2_agent_runs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.create_foreign_key("ontology_revisions_source_run_id_fkey", "ontology_revisions", "v2_construction_runs", ["source_run_id"], ["id"], ondelete="SET NULL")
    op.drop_constraint("fk_extraction_tasks_model_id_set_null", "extraction_tasks", type_="foreignkey")
    op.drop_constraint("fk_extraction_tasks_prompt_id_set_null", "extraction_tasks", type_="foreignkey")
    op.create_foreign_key("extraction_tasks_model_id_fkey", "extraction_tasks", "model_configs", ["model_id"], ["id"])
    op.create_foreign_key("extraction_tasks_prompt_id_fkey", "extraction_tasks", "prompts", ["prompt_id"], ["id"])
    op.alter_column("entity_instances", "row_data", existing_type=sa.JSON(), nullable=True)
    op.alter_column("audit_tasks", "progress", existing_type=sa.JSON(), nullable=True)
    op.alter_column("audit_tasks", "cancel_requested", existing_type=sa.Boolean(), nullable=True)
    op.alter_column("audit_tasks", "status", existing_type=sa.String(length=30), type_=sa.String(length=20), existing_nullable=False)
