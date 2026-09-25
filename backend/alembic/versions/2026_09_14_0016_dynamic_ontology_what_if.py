"""Dynamic ontology changes and isolated What-If scenarios."""
from alembic import op
import sqlalchemy as sa


revision = "0016_dynamic_ontology_what_if"
down_revision = "0015_ontology_mapping_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "v2_ontology_changes" not in tables:
        op.create_table(
            "v2_ontology_changes",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("base_revision_id", sa.String(), sa.ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("result_revision_id", sa.String(), sa.ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True, unique=True),
            sa.Column("target_kind", sa.String(length=40), nullable=False),
            sa.Column("operation", sa.String(length=20), nullable=False),
            sa.Column("target_id", sa.String(), nullable=True),
            sa.Column("before_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("after_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("impact_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("validation_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="applied"),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_v2_ontology_changes_ontology_id", "v2_ontology_changes", ["ontology_id"])
    if "v2_what_if_scenarios" not in tables:
        op.create_table(
            "v2_what_if_scenarios",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("base_revision_id", sa.String(), sa.ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("dataset_version_id", sa.String(), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
            sa.Column("baseline_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("assumptions_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("rule_overrides_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_v2_what_if_scenarios_ontology_id", "v2_what_if_scenarios", ["ontology_id"])
    if "v2_what_if_runs" not in tables:
        op.create_table(
            "v2_what_if_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("scenario_id", sa.String(), sa.ForeignKey("v2_what_if_scenarios.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
            sa.Column("stage", sa.String(length=40), nullable=False, server_default="prepare_baseline"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("engine", sa.String(length=80), nullable=False, server_default="semantica"),
            sa.Column("engine_version", sa.String(length=64), nullable=False),
            sa.Column("context_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("baseline_result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("scenario_result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("diff_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("input_hash", sa.String(length=64), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_v2_what_if_runs_scenario_id", "v2_what_if_runs", ["scenario_id"])
        op.create_index("ix_v2_what_if_runs_ontology_id", "v2_what_if_runs", ["ontology_id"])


def downgrade() -> None:
    # Runtime data and immutable provenance are intentionally retained.
    pass
