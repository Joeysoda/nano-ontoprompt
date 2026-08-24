"""Shared construction-run and assertion provenance records."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConstructionRun(Base):
    __tablename__ = "v2_construction_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_datasets.id", ondelete="SET NULL"), nullable=True)
    mode: Mapped[str] = mapped_column(String(40), nullable=False)  # temporal|multimodal|quality_benchmark
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    artifact_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class EvidenceRef(Base):
    __tablename__ = "v2_evidence_refs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    construction_run_id: Mapped[str] = mapped_column(String, ForeignKey("v2_construction_runs.id", ondelete="CASCADE"), nullable=False)
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    assertion_id: Mapped[str] = mapped_column(String(300), nullable=False)
    assertion_kind: Mapped[str] = mapped_column(String(30), nullable=False)  # node|edge|property|mapping|finding
    source_dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_row_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_media_id: Mapped[str | None] = mapped_column(String, nullable=True)
    extractor: Mapped[str] = mapped_column(String(40), nullable=False)  # rule|llm|ocr|bridge_fallback
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
