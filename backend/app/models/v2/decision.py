"""Authority records for decisions; graph is a retryable projection."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class DecisionRecord(Base):
    __tablename__ = 'v2_decisions'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey('ontology_projects.id', ondelete='CASCADE'), index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class DecisionLink(Base):
    __tablename__ = 'v2_decision_links'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey('ontology_projects.id', ondelete='CASCADE'), index=True)
    source: Mapped[str] = mapped_column(String, ForeignKey('v2_decisions.id'))
    target: Mapped[str] = mapped_column(String, ForeignKey('v2_decisions.id'))
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


def utcnow():
    return datetime.now(timezone.utc)


class DecisionPackage(Base):
    __tablename__ = "v2_decision_packages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenario_runs.id", ondelete="RESTRICT"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", index=True)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    etag: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    applied_decision_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_decisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (CheckConstraint("status IN ('draft','in_review','approved','rejected','applying','applied','apply_failed','reverted','superseded')", name="ck_decision_package_status"),)


class DecisionPackageAction(Base):
    __tablename__ = "v2_decision_package_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    package_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_decision_packages.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type_id: Mapped[str] = mapped_column(String(100), nullable=False)
    invocation: Mapped[dict] = mapped_column(JSON, nullable=False)
    expected_edits: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    external_effect_class: Mapped[str] = mapped_column(String(40), nullable=False, default="none")
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    __table_args__ = (UniqueConstraint("package_id", "sequence", name="uq_decision_package_action_sequence"),)


class DecisionPackageEvidence(Base):
    __tablename__ = "v2_decision_package_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    package_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_decision_packages.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class DecisionPackageEvent(Base):
    __tablename__ = "v2_decision_package_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    package_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_decision_packages.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("package_id", "sequence", name="uq_decision_package_event_sequence"), UniqueConstraint("package_id", "request_id", name="uq_decision_package_event_request"),)


class DecisionOutbox(Base):
    __tablename__ = "v2_decision_outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    package_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_decision_packages.id", ondelete="CASCADE"), nullable=False, index=True)
    action_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_decision_package_actions.id", ondelete="CASCADE"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
