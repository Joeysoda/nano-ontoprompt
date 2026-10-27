"""Scheduled, query-backed derived computations and immutable run evidence."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class DerivedProjection(Base):
    __tablename__ = "v2_derived_projections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    api_name: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    spec_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="paused")
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    etag: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schedule_error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("ontology_id", "api_name", name="uq_derived_projection_api_name"),
        CheckConstraint("status IN ('active','paused','retired')", name="ck_derived_projection_status"),
        CheckConstraint("interval_minutes IS NULL OR interval_minutes BETWEEN 5 AND 1440", name="ck_derived_projection_interval"),
    )


class DerivedProjectionRun(Base):
    __tablename__ = "v2_derived_projection_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    projection_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_derived_projections.id", ondelete="CASCADE"), nullable=False, index=True)
    logic_asset_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("v2_logic_asset_runs.id", ondelete="SET NULL"), nullable=True)
    query_version: Mapped[str] = mapped_column(String(40), nullable=False)
    data_view_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("v2_query_data_views.id", ondelete="SET NULL"), nullable=True)
    spec_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dependency_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (CheckConstraint("status IN ('completed','failed')", name="ck_derived_projection_run_status"),)
