"""Canonical semantic metadata, authorization policies and schema migrations."""
from alembic import op
import sqlalchemy as sa


revision = "0019_semantic_security_core"
down_revision = "0018_temporal_stream_facts"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if table not in set(sa.inspect(bind).get_table_names()):
        return
    columns = {item["name"] for item in sa.inspect(bind).get_columns(table)}
    if column.name not in columns:
        op.add_column(table, column)


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    bind = op.get_bind()
    if table not in set(sa.inspect(bind).get_table_names()):
        return
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    _add_column_if_missing("ontology_revisions", sa.Column("metadata_digest", sa.String(length=64), nullable=True))
    _add_column_if_missing("ontology_revisions", sa.Column("metadata_schema_version", sa.String(length=40), nullable=False, server_default="semantic-core-v1"))
    _add_column_if_missing("entity_instances", sa.Column("object_type_resource_id", sa.String(), nullable=True))
    _add_column_if_missing("entity_instances", sa.Column("source_version", sa.String(length=120), nullable=True))
    _add_column_if_missing("entities", sa.Column("semantic_resource_id", sa.String(), nullable=True))
    _create_index_if_missing("ix_ontology_revisions_metadata_digest", "ontology_revisions", ["metadata_digest"])
    _create_index_if_missing("ix_entity_instances_object_type_resource_id", "entity_instances", ["object_type_resource_id"])
    _create_index_if_missing("ix_entities_semantic_resource_id", "entities", ["semantic_resource_id"])

    if "v2_semantic_resources" not in tables:
        op.create_table(
            "v2_semantic_resources",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("api_name", sa.String(length=200), nullable=False),
            sa.Column("created_in_revision_id", sa.String(), nullable=True),
            sa.Column("retired_in_revision_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("ontology_id", "api_name", name="uq_v2_semantic_resource_api"),
        )
        op.create_index("ix_v2_semantic_resources_ontology_kind", "v2_semantic_resources", ["ontology_id", "kind"])
        op.create_index("ix_v2_semantic_resources_created_in_revision_id", "v2_semantic_resources", ["created_in_revision_id"])
        op.create_index("ix_v2_semantic_resources_retired_in_revision_id", "v2_semantic_resources", ["retired_in_revision_id"])

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_semantic_resource_versions" not in tables:
        op.create_table(
            "v2_semantic_resource_versions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("resource_id", sa.String(), sa.ForeignKey("v2_semantic_resources.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("revision_id", sa.String(), sa.ForeignKey("ontology_revisions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("api_name", sa.String(length=200), nullable=False),
            sa.Column("display_name", sa.String(length=240), nullable=True),
            sa.Column("name_cn", sa.String(length=240), nullable=True),
            sa.Column("name_en", sa.String(length=240), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("parent_resource_id", sa.String(), nullable=True),
            sa.Column("source_resource_id", sa.String(), nullable=True),
            sa.Column("target_resource_id", sa.String(), nullable=True),
            sa.Column("source_name", sa.String(length=200), nullable=True),
            sa.Column("target_name", sa.String(length=200), nullable=True),
            sa.Column("direction", sa.String(length=20), nullable=True, server_default="directed"),
            sa.Column("interface_resource_id", sa.String(), nullable=True),
            sa.Column("value_type_resource_id", sa.String(), nullable=True),
            sa.Column("struct_resource_id", sa.String(), nullable=True),
            sa.Column("base_type", sa.String(length=60), nullable=True),
            sa.Column("cardinality", sa.String(length=30), nullable=True),
            sa.Column("unit", sa.String(length=80), nullable=True),
            sa.Column("source_field", sa.String(length=240), nullable=True),
            sa.Column("is_identifier", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_array", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("constraints_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("provenance_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("revision_id", "resource_id", name="uq_v2_semantic_resource_version"),
            sa.UniqueConstraint("revision_id", "api_name", name="uq_v2_semantic_revision_api"),
        )
        op.create_index("ix_v2_semantic_resource_versions_ontology_revision", "v2_semantic_resource_versions", ["ontology_id", "revision_id"])
    _add_column_if_missing("v2_semantic_resource_versions", sa.Column("source_name", sa.String(length=200), nullable=True))
    _add_column_if_missing("v2_semantic_resource_versions", sa.Column("target_name", sa.String(length=200), nullable=True))
    _add_column_if_missing("v2_semantic_resource_versions", sa.Column("direction", sa.String(length=20), nullable=True, server_default="directed"))

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_ontology_source_mappings" not in tables:
        op.create_table(
            "v2_ontology_source_mappings",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("revision_id", sa.String(), sa.ForeignKey("ontology_revisions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("resource_id", sa.String(), sa.ForeignKey("v2_semantic_resources.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_dataset_id", sa.String(), nullable=True),
            sa.Column("source_version_id", sa.String(), nullable=True),
            sa.Column("source_table", sa.String(length=240), nullable=True),
            sa.Column("source_field", sa.String(length=240), nullable=True),
            sa.Column("mapping_kind", sa.String(length=40), nullable=False, server_default="field"),
            sa.Column("mapping_status", sa.String(length=20), nullable=False, server_default="confirmed"),
            sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_v2_ontology_source_mappings_revision", "v2_ontology_source_mappings", ["ontology_id", "revision_id"])
    _add_column_if_missing(
        "v2_ontology_source_mappings",
        sa.Column("mapping_status", sa.String(length=20), nullable=False, server_default="confirmed"),
    )

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_ontology_security_policies" not in tables:
        op.create_table(
            "v2_ontology_security_policies",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("revision_id", sa.String(), sa.ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("subject_kind", sa.String(length=20), nullable=False, server_default="user"),
            sa.Column("subject_id", sa.String(length=200), nullable=False),
            sa.Column("effect", sa.String(length=10), nullable=False, server_default="allow"),
            sa.Column("scope_kind", sa.String(length=30), nullable=False, server_default="ontology"),
            sa.Column("scope_id", sa.String(), nullable=True),
            sa.Column("conditions_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("field_allowlist_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
        )
        op.create_index("ix_v2_security_policy_subject", "v2_ontology_security_policies", ["ontology_id", "subject_kind", "subject_id", "enabled"])
        op.create_index("ix_v2_security_policy_scope", "v2_ontology_security_policies", ["ontology_id", "scope_kind", "scope_id"])

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_schema_migration_plans" not in tables:
        op.create_table(
            "v2_schema_migration_plans",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("base_revision_id", sa.String(), sa.ForeignKey("ontology_revisions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("target_revision_id", sa.String(), sa.ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
            sa.Column("phase", sa.String(length=40), nullable=False, server_default="impact"),
            sa.Column("impact_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("shadow_namespace", sa.String(length=240), nullable=True),
            sa.Column("result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("execution_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_v2_schema_migration_plans_ontology", "v2_schema_migration_plans", ["ontology_id", "created_at"])
    _add_column_if_missing("v2_schema_migration_plans", sa.Column("execution_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_schema_migration_instructions" not in tables:
        op.create_table(
            "v2_schema_migration_instructions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("plan_id", sa.String(), sa.ForeignKey("v2_schema_migration_plans.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sequence_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("instruction_kind", sa.String(length=50), nullable=False),
            sa.Column("resource_id", sa.String(), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="planned"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_v2_schema_migration_instructions_plan", "v2_schema_migration_instructions", ["plan_id", "sequence_no"])

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_schema_migration_runs" not in tables:
        op.create_table(
            "v2_schema_migration_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("plan_id", sa.String(), sa.ForeignKey("v2_schema_migration_plans.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
            sa.Column("phase", sa.String(length=40), nullable=False, server_default="impact"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("target_revision_id", sa.String(), nullable=True),
            sa.Column("result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_v2_schema_migration_runs_plan_id", "v2_schema_migration_runs", ["plan_id"])
        op.create_index("ix_v2_schema_migration_runs_ontology_id", "v2_schema_migration_runs", ["ontology_id"])

    tables = set(sa.inspect(bind).get_table_names())
    if "v2_schema_dependencies" not in tables:
        op.create_table(
            "v2_schema_dependencies",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("revision_id", sa.String(), sa.ForeignKey("ontology_revisions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_kind", sa.String(length=40), nullable=False),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("target_kind", sa.String(length=40), nullable=False),
            sa.Column("target_id", sa.String(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_v2_schema_dependencies_source", "v2_schema_dependencies", ["ontology_id", "revision_id", "source_kind", "source_id"])
        op.create_index("ix_v2_schema_dependencies_target", "v2_schema_dependencies", ["ontology_id", "revision_id", "target_kind", "target_id"])


def downgrade() -> None:
    # Preserve semantic and security history.  A downgrade must not delete
    # policy, migration or provenance records from a user's database.
    pass
