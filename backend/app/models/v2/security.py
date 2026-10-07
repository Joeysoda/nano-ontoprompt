"""Database policy records for object and property authorization."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OntologySecurityPolicy(Base):
    __tablename__ = "v2_ontology_security_policies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    revision_id: Mapped[str | None] = mapped_column(String, ForeignKey("ontology_revisions.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="user")  # user|role
    subject_id: Mapped[str] = mapped_column(String(200), nullable=False)
    effect: Mapped[str] = mapped_column(String(10), nullable=False, default="allow")  # allow|deny
    scope_kind: Mapped[str] = mapped_column(String(30), nullable=False, default="ontology")  # ontology|object_type|property|link
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True)
    conditions_json: Mapped[list | dict] = mapped_column(JSON, nullable=False, default=list)
    field_allowlist_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_v2_security_policy_subject", "ontology_id", "subject_kind", "subject_id", "enabled"),
        Index("ix_v2_security_policy_scope", "ontology_id", "scope_kind", "scope_id"),
    )


__all__ = ["OntologySecurityPolicy"]
