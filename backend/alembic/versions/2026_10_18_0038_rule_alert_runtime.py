"""Rule runtime: proposals, immutable versions, evaluations, alerts and history."""
from alembic import op
import sqlalchemy as sa


revision = "0038_rule_alert_runtime"
down_revision = "0037_pin_study_metadata_revision"
branch_labels = None
depends_on = None


def _ts(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "v2_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("api_name", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        _ts("created_at"), _ts("updated_at"),
        sa.UniqueConstraint("ontology_id", "api_name", name="uq_v2_rule_api_name"),
        sa.CheckConstraint("status IN ('active','muted','paused','retired')", name="ck_v2_rule_status"),
    )
    op.create_index("ix_v2_rules_ontology_id", "v2_rules", ["ontology_id"])
    op.create_table(
        "v2_rule_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("v2_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("logic", sa.JSON(), nullable=False),
        sa.Column("logic_digest", sa.String(64), nullable=False),
        sa.Column("proposal_id", sa.String(36), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("rule_id", "version", name="uq_v2_rule_version"),
    )
    op.create_index("ix_v2_rule_versions_rule_id", "v2_rule_versions", ["rule_id"])
    op.create_table(
        "v2_rule_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("v2_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("old_logic", sa.JSON(), nullable=True),
        sa.Column("new_logic", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("author_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewer_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=False),
        sa.Column("self_approved", sa.Integer(), nullable=False),
        _ts("created_at"), _ts("reviewed_at", nullable=True),
        sa.CheckConstraint("status IN ('open','approved','rejected','stale')", name="ck_v2_rule_proposal_status"),
    )
    op.create_index("ix_v2_rule_proposals_rule_id", "v2_rule_proposals", ["rule_id"])
    op.create_table(
        "v2_rule_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("v2_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_version_id", sa.String(36), sa.ForeignKey("v2_rule_versions.id"), nullable=False),
        sa.Column("data_view_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("rule_status", sa.String(20), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("output_count", sa.Integer(), nullable=False),
        sa.Column("triggered_count", sa.Integer(), nullable=False),
        sa.Column("recovered_count", sa.Integer(), nullable=False),
        sa.Column("output_keys", sa.JSON(), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("principal_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("rule_id", "idempotency_key", name="uq_v2_rule_evaluation_idempotency"),
        sa.CheckConstraint("status IN ('succeeded','failed')", name="ck_v2_rule_evaluation_status"),
    )
    op.create_index("ix_v2_rule_evaluations_rule_id", "v2_rule_evaluations", ["rule_id"])
    op.create_table(
        "v2_rule_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ontology_id", sa.String(), sa.ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("v2_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_version_id", sa.String(36), sa.ForeignKey("v2_rule_versions.id"), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(200), nullable=False),
        sa.Column("subject_id", sa.String(300), nullable=False),
        sa.Column("window_start", sa.String(40), nullable=True),
        sa.Column("window_end", sa.String(40), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("suggested_action", sa.JSON(), nullable=True),
        sa.Column("first_evaluation_id", sa.String(36), nullable=False),
        sa.Column("last_evaluation_id", sa.String(36), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        _ts("recovered_at", nullable=True),
        sa.Column("acknowledged_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        _ts("acknowledged_at", nullable=True),
        sa.Column("resolved_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        _ts("resolved_at", nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=False),
        _ts("created_at"), _ts("updated_at"),
        sa.UniqueConstraint("rule_id", "dedupe_key", name="uq_v2_rule_alert_dedupe"),
        sa.CheckConstraint("status IN ('open','acknowledged','resolved')", name="ck_v2_rule_alert_status"),
        sa.CheckConstraint("severity IN ('low','medium','high','critical')", name="ck_v2_rule_alert_severity"),
    )
    op.create_index("ix_v2_rule_alerts_ontology_id", "v2_rule_alerts", ["ontology_id"])
    op.create_index("ix_v2_rule_alerts_rule_id", "v2_rule_alerts", ["rule_id"])
    op.create_index("ix_v2_rule_alerts_subject_id", "v2_rule_alerts", ["subject_id"])
    op.create_table(
        "v2_rule_alert_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("v2_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alert_id", sa.String(36), sa.ForeignKey("v2_rule_alerts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("evaluation_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        _ts("created_at"),
        sa.CheckConstraint(
            "kind IN ('rule_created','proposal_opened','proposal_approved','proposal_rejected','status_changed',"
            "'triggered','recurred','recovered','acknowledged','resolved','evaluation_failed')",
            name="ck_v2_rule_alert_event_kind"),
    )
    op.create_index("ix_v2_rule_alert_events_rule_id", "v2_rule_alert_events", ["rule_id"])
    op.create_index("ix_v2_rule_alert_events_alert_id", "v2_rule_alert_events", ["alert_id"])


def downgrade() -> None:
    for table in ("v2_rule_alert_events", "v2_rule_alerts", "v2_rule_evaluations", "v2_rule_proposals", "v2_rule_versions", "v2_rules"):
        op.drop_table(table)
