"""Recoverable five-step construction drafts."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConstructionDraft(Base):
    __tablename__ = "v2_construction_drafts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id: Mapped[str] = mapped_column(String, ForeignKey("v2_datasets.id", ondelete="CASCADE"), nullable=False)
    # A create-mode draft is intentionally allowed to exist without an
    # ontology.  The project is created atomically only when the user confirms
    # the final build, so abandoned first steps do not pollute the library.
    ontology_id: Mapped[str | None] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=True)
    data_class: Mapped[str] = mapped_column(String(30), nullable=False, default="regular")
    target_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="append")
    new_ontology_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    new_ontology_domain: Mapped[str | None] = mapped_column(String(100), nullable=True)
    privacy_level: Mapped[str] = mapped_column(String(20), nullable=False, default="standard")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    selection_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    processing_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    preflight_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    mapping_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    build_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    mapping_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
