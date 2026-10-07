"""Explicit schema-migration plans and durable execution records."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SchemaMigrationPlan(Base):
    __tablename__ = "v2_schema_migration_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    base_revision_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_revisions.id", ondelete="RESTRICT"), nullable=False)
    target_revision_id: Mapped[str | None] = mapped_column(String, ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")  # draft|validated|running|applied|failed|reverted
    phase: Mapped[str] = mapped_column(String(40), nullable=False, default="impact")
    impact_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    shadow_namespace: Mapped[str | None] = mapped_column(String(240), nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Private rollback journal. It is intentionally omitted from public plan
    # serialization because it may contain pre-migration instance values.
    execution_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_v2_schema_migration_plans_ontology", "ontology_id", "created_at"),
    )


class SchemaMigrationRun(Base):
    """Durable execution state for a validated migration plan."""

    __tablename__ = "v2_schema_migration_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("v2_schema_migration_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    phase: Mapped[str] = mapped_column(String(40), nullable=False, default="impact")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class SchemaMigrationInstruction(Base):
    __tablename__ = "v2_schema_migration_instructions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("v2_schema_migration_plans.id", ondelete="CASCADE"), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    instruction_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        Index("ix_v2_schema_migration_instructions_plan", "plan_id", "sequence_no"),
    )


class SchemaDependency(Base):
    __tablename__ = "v2_schema_dependencies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    revision_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_revisions.id", ondelete="CASCADE"), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    target_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        Index("ix_v2_schema_dependencies_source", "ontology_id", "revision_id", "source_kind", "source_id"),
        Index("ix_v2_schema_dependencies_target", "ontology_id", "revision_id", "target_kind", "target_id"),
    )


__all__ = ["SchemaMigrationPlan", "SchemaMigrationInstruction", "SchemaMigrationRun", "SchemaDependency"]
