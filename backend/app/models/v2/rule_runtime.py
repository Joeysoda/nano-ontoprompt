"""Rule runtime: proposals, immutable rule versions, evaluations and alerts.

Rule and Proposal follow the public Foundry Rules object model.  Alert state
(open/acknowledged/resolved) is a project extension: Palantir models alerts as
ordinary Ontology objects whose lifecycle is workflow-specific.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class RuleResource(Base):
    __tablename__ = "v2_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    api_name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("ontology_id", "api_name", name="uq_v2_rule_api_name"),
        CheckConstraint("status IN ('active','muted','paused','retired')", name="ck_v2_rule_status"),
    )


class RuleVersion(Base):
    __tablename__ = "v2_rule_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    logic: Mapped[dict] = mapped_column(JSON, nullable=False)
    logic_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("rule_id", "version", name="uq_v2_rule_version"),)


class RuleProposal(Base):
    __tablename__ = "v2_rule_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    old_logic: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_logic: Mapped[dict] = mapped_column(JSON, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    author_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    review_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    self_approved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (CheckConstraint("status IN ('open','approved','rejected','stale')", name="ck_v2_rule_proposal_status"),)


class RuleEvaluation(Base):
    __tablename__ = "v2_rule_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_rule_versions.id"), nullable=False)
    data_view_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    triggered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_keys: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    principal_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("rule_id", "idempotency_key", name="uq_v2_rule_evaluation_idempotency"),
        CheckConstraint("status IN ('succeeded','failed')", name="ck_v2_rule_evaluation_status"),
    )


class RuleAlert(Base):
    __tablename__ = "v2_rule_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_rule_versions.id"), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    window_start: Mapped[str | None] = mapped_column(String(40), nullable=True)
    window_end: Mapped[str | None] = mapped_column(String(40), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    properties: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    suggested_action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    first_evaluation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    last_evaluation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("rule_id", "dedupe_key", name="uq_v2_rule_alert_dedupe"),
        CheckConstraint("status IN ('open','acknowledged','resolved')", name="ck_v2_rule_alert_status"),
        CheckConstraint("severity IN ('low','medium','high','critical')", name="ck_v2_rule_alert_severity"),
    )


class RuleAlertEvent(Base):
    """Append-only history, mirroring Automate's triggered/recovered/failed log."""
    __tablename__ = "v2_rule_alert_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    alert_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("v2_rule_alerts.id", ondelete="CASCADE"), nullable=True, index=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('rule_created','proposal_opened','proposal_approved','proposal_rejected','status_changed',"
            "'triggered','recurred','recovered','acknowledged','resolved','evaluation_failed')",
            name="ck_v2_rule_alert_event_kind"),
    )
