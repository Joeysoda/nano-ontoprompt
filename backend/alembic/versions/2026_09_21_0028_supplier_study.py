"""Persist the supplier study and its independent scenario cases."""
from alembic import op
import sqlalchemy as sa

revision = "0028_supplier_study"
down_revision = "0027_legacy_mapping_reconcile"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_scenario_studies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("base_view_id", sa.String(64), sa.ForeignKey("v2_query_data_views.id"), nullable=False),
        sa.Column("source_manifest_digest", sa.String(128), nullable=False),
        sa.Column("scenario_time", sa.String(40), nullable=False),
        sa.Column("scope_definition", sa.JSON(), nullable=False),
        sa.Column("scope_hash", sa.String(128), nullable=False),
        sa.Column("smoothing_minutes", sa.Integer(), nullable=False),
        sa.Column("run_baseline_sim", sa.Boolean(), nullable=False),
        sa.Column("model_key", sa.String(160), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("model_config_alias", sa.String(100), nullable=False),
        sa.Column("parameter_projection", sa.JSON(), nullable=False),
        sa.Column("etag", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_v2_scenario_studies_ontology_id", "v2_scenario_studies", ["ontology_id"])
    op.create_table("v2_scenario_study_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("study_id", sa.String(36), sa.ForeignKey("v2_scenario_studies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_id", sa.String(36), sa.ForeignKey("v2_scenarios.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("case_key", sa.String(40), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("case_kind", sa.String(20), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("action_key", sa.String(100)),
        sa.Column("submitted_parameters", sa.JSON(), nullable=False),
        sa.Column("parameters_hash", sa.String(128), nullable=False),
        sa.Column("definition_revision", sa.Integer(), nullable=False),
        sa.Column("etag", sa.Integer(), nullable=False),
        sa.Column("synthetic_manifest_hash", sa.String(128), nullable=False),
        sa.UniqueConstraint("study_id", "case_key", name="uq_study_case_key"))
    op.create_index("ix_v2_scenario_study_cases_study_id", "v2_scenario_study_cases", ["study_id"])
    for name, type_ in (("study_id", sa.String(36)), ("case_id", sa.String(36)),
                        ("compatibility_hash", sa.String(128)), ("depends_on_run_id", sa.String(36))):
        op.add_column("v2_scenario_runs", sa.Column(name, type_, nullable=True))
    op.create_index("ix_v2_scenario_runs_study_id", "v2_scenario_runs", ["study_id"])


def downgrade():
    op.drop_index("ix_v2_scenario_runs_study_id", table_name="v2_scenario_runs")
    for name in ("depends_on_run_id", "compatibility_hash", "case_id", "study_id"):
        op.drop_column("v2_scenario_runs", name)
    op.drop_index("ix_v2_scenario_study_cases_study_id", table_name="v2_scenario_study_cases")
    op.drop_table("v2_scenario_study_cases")
    op.drop_index("ix_v2_scenario_studies_ontology_id", table_name="v2_scenario_studies")
    op.drop_table("v2_scenario_studies")
