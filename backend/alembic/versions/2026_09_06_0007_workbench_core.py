"""Add workbench classifications, multimodal samples and revisions."""
from alembic import op
import sqlalchemy as sa


revision = "0007_workbench_core"
down_revision = "0006_temporal_profiles"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)} if table in sa.inspect(bind).get_table_names() else set()


def _add_column(table: str, name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if name not in _columns(bind, table):
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "v2_multimodal_samples" not in tables:
        op.create_table(
            "v2_multimodal_samples",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("dataset_version_id", sa.String(), nullable=False),
            sa.Column("sample_key", sa.String(length=240), nullable=False),
            sa.Column("scene_id", sa.String(length=120), nullable=True),
            sa.Column("split", sa.String(length=40), nullable=True),
            sa.Column("label", sa.String(length=120), nullable=True),
            sa.Column("labels", sa.JSON(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["dataset_version_id"], ["v2_dataset_versions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    _add_column("v2_datasets", "data_class", sa.Column("data_class", sa.String(length=30), nullable=True))
    _add_column("v2_datasets", "privacy_level", sa.Column("privacy_level", sa.String(length=20), nullable=True))
    _add_column("v2_media_items", "sample_id", sa.Column("sample_id", sa.String(), nullable=True))
    _add_column("v2_media_items", "asset_role", sa.Column("asset_role", sa.String(length=30), nullable=True))
    _add_column("v2_media_items", "original_name", sa.Column("original_name", sa.String(length=500), nullable=True))
    _add_column("v2_media_items", "mime_type", sa.Column("mime_type", sa.String(length=120), nullable=True))
    _add_column("v2_media_items", "checksum", sa.Column("checksum", sa.String(length=64), nullable=True))
    _add_column("v2_media_items", "metadata_json", sa.Column("metadata_json", sa.JSON(), nullable=True))
    _add_column("v2_construction_runs", "revision_id", sa.Column("revision_id", sa.String(), nullable=True))
    _add_column("v2_construction_runs", "cancel_requested", sa.Column("cancel_requested", sa.Boolean(), nullable=True))
    _add_column("v2_evidence_refs", "source_sample_id", sa.Column("source_sample_id", sa.String(), nullable=True))
    _add_column("v2_evidence_refs", "revision_id", sa.Column("revision_id", sa.String(), nullable=True))
    _add_column("audit_tasks", "revision_id", sa.Column("revision_id", sa.String(), nullable=True))
    _add_column("audit_tasks", "construction_run_id", sa.Column("construction_run_id", sa.String(), nullable=True))
    _add_column("audit_tasks", "cancel_requested", sa.Column("cancel_requested", sa.Boolean(), nullable=True))
    _add_column("ontology_projects", "current_revision_id", sa.Column("current_revision_id", sa.String(), nullable=True))

    # Backfill nullable columns in a dialect-neutral way, then retain the
    # nullable shape for old databases whose rows predate the migration.
    if "v2_datasets" in tables:
        op.execute(sa.text("UPDATE v2_datasets SET data_class = CASE WHEN kind = 'unstructured' THEN 'multimodal' ELSE 'regular' END WHERE data_class IS NULL"))
        op.execute(sa.text("UPDATE v2_datasets SET privacy_level = 'standard' WHERE privacy_level IS NULL"))
    if "v2_media_items" in tables:
        op.execute(sa.text("UPDATE v2_media_items SET asset_role = CASE WHEN media_type = 'image' THEN 'rgb' ELSE 'primary' END WHERE asset_role IS NULL"))
        op.execute(sa.text("UPDATE v2_media_items SET metadata_json = '{}' WHERE metadata_json IS NULL"))
    if "v2_construction_runs" in tables:
        op.execute(sa.text("UPDATE v2_construction_runs SET cancel_requested = false WHERE cancel_requested IS NULL"))
    if "audit_tasks" in tables:
        op.execute(sa.text("UPDATE audit_tasks SET cancel_requested = false WHERE cancel_requested IS NULL"))


def downgrade() -> None:
    # Keep downgrade conservative; dropping columns would destroy provenance
    # in an existing demo database. Future migrations can remove them once a
    # backup policy is in place.
    pass
