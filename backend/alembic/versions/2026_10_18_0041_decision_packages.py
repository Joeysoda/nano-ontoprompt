"""Add typed decision packages and transactional outbox."""
from alembic import op
import sqlalchemy as sa

revision = "0041_decision_packages"
down_revision = "0040_merge_plan_a2_plan_b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("v2_decision_packages",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_id", sa.String(36), sa.ForeignKey("v2_scenarios.id", ondelete="CASCADE"), nullable=False), sa.Column("scenario_revision", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("v2_scenario_runs.id", ondelete="RESTRICT"), nullable=False), sa.Column("title", sa.String(200), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("preview_digest", sa.String(64)), sa.Column("etag", sa.Integer(), nullable=False), sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_by", sa.String(), sa.ForeignKey("users.id")), sa.Column("applied_decision_id", sa.String(), sa.ForeignKey("v2_decisions.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('draft','in_review','approved','rejected','applying','applied','apply_failed','reverted','superseded')", name="ck_decision_package_status"))
    for column in ("ontology_id", "scenario_id", "run_id", "status"):
        op.create_index(f"ix_v2_decision_packages_{column}", "v2_decision_packages", [column])
    op.create_table("v2_decision_package_actions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("package_id", sa.String(36), sa.ForeignKey("v2_decision_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("action_type_id", sa.String(100), nullable=False), sa.Column("invocation", sa.JSON(), nullable=False),
        sa.Column("expected_edits", sa.JSON(), nullable=False), sa.Column("external_effect_class", sa.String(40), nullable=False), sa.Column("receipt", sa.JSON()),
        sa.UniqueConstraint("package_id", "sequence", name="uq_decision_package_action_sequence"))
    op.create_index("ix_v2_decision_package_actions_package_id", "v2_decision_package_actions", ["package_id"])
    op.create_table("v2_decision_package_evidence", sa.Column("id", sa.String(36), primary_key=True), sa.Column("package_id", sa.String(36), sa.ForeignKey("v2_decision_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_type", sa.String(40), nullable=False), sa.Column("reference", sa.JSON(), nullable=False), sa.Column("content_digest", sa.String(64), nullable=False))
    op.create_index("ix_v2_decision_package_evidence_package_id", "v2_decision_package_evidence", ["package_id"])
    op.create_table("v2_decision_package_events", sa.Column("id", sa.String(36), primary_key=True), sa.Column("package_id", sa.String(36), sa.ForeignKey("v2_decision_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("event_type", sa.String(40), nullable=False), sa.Column("actor_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False), sa.Column("before_digest", sa.String(64)), sa.Column("after_digest", sa.String(64)), sa.Column("request_id", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("package_id", "sequence", name="uq_decision_package_event_sequence"), sa.UniqueConstraint("package_id", "request_id", name="uq_decision_package_event_request"))
    op.create_index("ix_v2_decision_package_events_package_id", "v2_decision_package_events", ["package_id"])
    op.create_table("v2_decision_outbox", sa.Column("id", sa.String(36), primary_key=True), sa.Column("package_id", sa.String(36), sa.ForeignKey("v2_decision_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_id", sa.String(36), sa.ForeignKey("v2_decision_package_actions.id", ondelete="CASCADE"), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_v2_decision_outbox_package_id", "v2_decision_outbox", ["package_id"])


def downgrade():
    for table in ("v2_decision_outbox", "v2_decision_package_events", "v2_decision_package_evidence", "v2_decision_package_actions", "v2_decision_packages"):
        op.drop_table(table)
