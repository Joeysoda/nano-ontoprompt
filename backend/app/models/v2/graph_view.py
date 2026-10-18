"""Saved Vertex-style graph resources with immutable versions.

A version pins the Object Set expression, the data view it was explored on,
layer styling and node positions.  It stores no object data: opening a
version re-queries through Object Query, so authorization always applies.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GraphView(Base):
    __tablename__ = "v2_graph_views"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    head_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class GraphViewVersion(Base):
    __tablename__ = "v2_graph_view_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    graph_view_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_graph_views.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    definition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    data_view_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("graph_view_id", "version", name="uq_v2_graph_view_version"),)
