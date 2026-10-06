"""Versioned, published configuration for the reusable Object View runtime."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint

from app.database import Base


class ObjectViewConfiguration(Base):
    __tablename__ = "v2_object_view_configs"
    id = Column(String(64), primary_key=True, default=lambda: str(uuid4()))
    ontology_id = Column(String, ForeignKey("ontology_projects.id"), nullable=False)
    object_type = Column(String(200), nullable=False)
    version = Column(Integer, nullable=False)
    metadata_digest = Column(String(64), nullable=False)
    view_schema = Column(JSON, nullable=False)
    published_by = Column(String, ForeignKey("users.id"), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("ontology_id", "object_type", "version", name="uq_v2_object_view_version"),)
