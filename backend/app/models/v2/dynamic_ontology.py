"""Immutable ontology edits and isolated What-If scenarios."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class OntologyChange(Base):
    __tablename__ = "v2_ontology_changes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    base_revision_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True
    )
    result_revision_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    target_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    before_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    after_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    impact_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="applied")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class WhatIfScenario(Base):
    __tablename__ = "v2_what_if_scenarios"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    base_revision_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True
    )
    dataset_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    baseline_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    assumptions_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rule_overrides_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class WhatIfRun(Base):
    __tablename__ = "v2_what_if_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    scenario_id: Mapped[str] = mapped_column(
        String, ForeignKey("v2_what_if_scenarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ontology_id: Mapped[str] = mapped_column(
        String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    stage: Mapped[str] = mapped_column(String(40), nullable=False, default="prepare_baseline")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    engine: Mapped[str] = mapped_column(String(80), nullable=False, default="semantica")
    engine_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="3a69721abf72d7188a0d6fd72c8462261b2c44eb"
    )
    context_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    baseline_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scenario_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    diff_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
