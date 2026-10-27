"""Add canonical versioned scenario studies, cases and compiled run plans."""
from alembic import op
import sqlalchemy as sa


revision = "0036_versioned_scenario_studies"
down_revision = "0035_merge_semantic_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older development databases were maintained with ``metadata.create_all``.
    # They can already contain this complete table set while Alembic still
    # reports the preceding revision.  Treat that state as reconciled instead
    # of aborting the whole (transactional) upgrade on the first duplicate.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    expected_tables = {
        "v2_study_definitions",
        "v2_study_versions",
        "v2_case_definitions",
        "v2_scenario_run_plans",
    }
    if expected_tables.issubset(set(inspector.get_table_names())):
        run_columns = {column["name"] for column in inspector.get_columns("v2_scenario_runs")}
        if "run_plan_id" not in run_columns:
            with op.batch_alter_table("v2_scenario_runs") as batch:
                batch.add_column(
                    sa.Column(
                        "run_plan_id",
                        sa.String(36),
                        sa.ForeignKey(
                            "v2_scenario_run_plans.id",
                            ondelete="RESTRICT",
                            name="fk_v2_scenario_runs_run_plan_id_v2_scenario_run_plans",
                        ),
                        nullable=True,
                    )
                )
                batch.create_index("ix_v2_scenario_runs_run_plan_id", ["run_plan_id"])
        return

    op.create_table(
        "v2_study_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("draft_spec", sa.JSON(), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False),
        sa.Column("etag", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('draft','published','archived')", name="ck_study_definition_status"),
    )
    op.create_index("ix_v2_study_definitions_ontology_id", "v2_study_definitions", ["ontology_id"])
    op.create_index("ix_v2_study_definitions_owner_id", "v2_study_definitions", ["owner_id"])

    op.create_table(
        "v2_study_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("study_id", sa.String(36), sa.ForeignKey("v2_study_definitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec_version", sa.String(40), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("spec_hash", sa.String(128), nullable=False),
        sa.Column("ontology_metadata_digest", sa.String(128), nullable=False),
        sa.Column("compatibility", sa.String(20), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("study_id", "version", name="uq_study_version_number"),
        sa.UniqueConstraint("study_id", "spec_hash", name="uq_study_version_spec_hash"),
        sa.CheckConstraint("compatibility IN ('compatible','breaking')", name="ck_study_version_compatibility"),
    )
    op.create_index("ix_v2_study_versions_study_id", "v2_study_versions", ["study_id"])

    op.create_table(
        "v2_case_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("study_version_id", sa.String(36), sa.ForeignKey("v2_study_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scenario_id", sa.String(36), sa.ForeignKey("v2_scenarios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_case_id", sa.String(36), sa.ForeignKey("v2_case_definitions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("case_kind", sa.String(20), nullable=False),
        sa.Column("parameter_overrides", sa.JSON(), nullable=False),
        sa.Column("ordered_actions", sa.JSON(), nullable=False),
        sa.Column("case_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("etag", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scenario_id", name="uq_case_definition_scenario"),
        sa.CheckConstraint("case_kind IN ('baseline','candidate')", name="ck_case_definition_kind"),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_case_definition_status"),
    )
    op.create_index("ix_v2_case_definitions_study_version_id", "v2_case_definitions", ["study_version_id"])
    op.create_index("ix_v2_case_definitions_scenario_id", "v2_case_definitions", ["scenario_id"])

    op.create_table(
        "v2_scenario_run_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("v2_case_definitions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("study_version_id", sa.String(36), sa.ForeignKey("v2_study_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scenario_id", sa.String(36), sa.ForeignKey("v2_scenarios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scenario_revision", sa.Integer(), nullable=False),
        sa.Column("data_view_id", sa.String(64), sa.ForeignKey("v2_query_data_views.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("spec_hash", sa.String(128), nullable=False),
        sa.Column("case_hash", sa.String(128), nullable=False),
        sa.Column("ontology_metadata_digest", sa.String(128), nullable=False),
        sa.Column("permission_projection_digest", sa.String(128), nullable=False),
        sa.Column("executor_version", sa.String(40), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("plan_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('compiled','superseded')", name="ck_scenario_run_plan_status"),
    )
    op.create_index("ix_v2_scenario_run_plans_case_id", "v2_scenario_run_plans", ["case_id"])
    with op.batch_alter_table("v2_scenario_runs") as batch:
        batch.add_column(
            sa.Column("run_plan_id", sa.String(36), sa.ForeignKey(
                "v2_scenario_run_plans.id",
                ondelete="RESTRICT",
                name="fk_v2_scenario_runs_run_plan_id_v2_scenario_run_plans",
            ), nullable=True),
        )
        batch.create_index("ix_v2_scenario_runs_run_plan_id", ["run_plan_id"])


def downgrade() -> None:
    with op.batch_alter_table("v2_scenario_runs") as batch:
        batch.drop_index("ix_v2_scenario_runs_run_plan_id")
        batch.drop_column("run_plan_id")
    op.drop_index("ix_v2_scenario_run_plans_case_id", table_name="v2_scenario_run_plans")
    op.drop_table("v2_scenario_run_plans")
    op.drop_index("ix_v2_case_definitions_scenario_id", table_name="v2_case_definitions")
    op.drop_index("ix_v2_case_definitions_study_version_id", table_name="v2_case_definitions")
    op.drop_table("v2_case_definitions")
    op.drop_index("ix_v2_study_versions_study_id", table_name="v2_study_versions")
    op.drop_table("v2_study_versions")
    op.drop_index("ix_v2_study_definitions_owner_id", table_name="v2_study_definitions")
    op.drop_index("ix_v2_study_definitions_ontology_id", table_name="v2_study_definitions")
    op.drop_table("v2_study_definitions")
