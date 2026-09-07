"""Immutable ontology revision snapshots used by assisted repair flows."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OntologyRevision(Base):
    __tablename__ = "ontology_revisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_revision_id: Mapped[str | None] = mapped_column(String, ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True)
    # Kept as an opaque id instead of an ORM FK so revision tooling can be
    # imported in isolation (and can read legacy databases that predate the
    # v2 construction table).  The migration still adds a database FK where
    # the table is present.
    source_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    graph_namespace: Mapped[str | None] = mapped_column(String(240), nullable=True)
    snapshot_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="current")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
