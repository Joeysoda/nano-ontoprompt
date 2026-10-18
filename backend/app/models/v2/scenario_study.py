"""Canonical, versioned study definitions and compiled scenario run plans."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StudyDefinition(Base):
    __tablename__ = "v2_study_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    draft_spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    etag: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('draft','published','archived')", name="ck_study_definition_status"),
    )


class StudyVersion(Base):
    __tablename__ = "v2_study_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    study_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_study_definitions.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_version: Mapped[str] = mapped_column(String(40), nullable=False, default="scenario-study/v1")
    spec_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    ontology_metadata_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ontology_metadata_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    compatibility: Mapped[str] = mapped_column(String(20), nullable=False, default="compatible")
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("study_id", "version", name="uq_study_version_number"),
        UniqueConstraint("study_id", "spec_hash", name="uq_study_version_spec_hash"),
        CheckConstraint("compatibility IN ('compatible','breaking')", name="ck_study_version_compatibility"),
    )


class CaseDefinition(Base):
    __tablename__ = "v2_case_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    study_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_study_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenarios.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_case_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("v2_case_definitions.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    case_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")
    parameter_overrides: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ordered_actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    case_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    etag: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("scenario_id", name="uq_case_definition_scenario"),
        CheckConstraint("case_kind IN ('baseline','candidate')", name="ck_case_definition_kind"),
        CheckConstraint("status IN ('active','archived')", name="ck_case_definition_status"),
    )


class RunPlan(Base):
    __tablename__ = "v2_scenario_run_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_case_definitions.id", ondelete="RESTRICT"), nullable=False, index=True)
    study_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_study_versions.id", ondelete="RESTRICT"), nullable=False)
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenarios.id", ondelete="RESTRICT"), nullable=False)
    scenario_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data_view_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_query_data_views.id", ondelete="RESTRICT"), nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    case_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    ontology_metadata_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ontology_metadata_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    permission_projection_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    executor_version: Mapped[str] = mapped_column(String(40), nullable=False, default="scenario-dag/v1")
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="compiled")
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('compiled','superseded')", name="ck_scenario_run_plan_status"),
    )
