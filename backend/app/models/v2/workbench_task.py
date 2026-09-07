"""Persistent workbench task and model-invocation records."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MappingTask(Base):
    __tablename__ = "v2_mapping_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_id: Mapped[str] = mapped_column(String, ForeignKey("v2_construction_drafts.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    model_route: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    stage: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    trace_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_plan_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    transfer_manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False, default="ontology-mapping-v2")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DataImportTask(Base):
    __tablename__ = "v2_data_import_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    connection_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_connections.id", ondelete="SET NULL"), nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_datasets.id", ondelete="SET NULL"), nullable=True)
    data_class: Mapped[str] = mapped_column(String(30), nullable=False, default="regular")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ModelInvocation(Base):
    __tablename__ = "v2_model_invocations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_construction_drafts.id", ondelete="SET NULL"), nullable=True)
    construction_run_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_construction_runs.id", ondelete="SET NULL"), nullable=True)
    audit_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    mapping_task_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_mapping_tasks.id", ondelete="SET NULL"), nullable=True)
    route_alias: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    phase: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
