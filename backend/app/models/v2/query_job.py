"""Durable state for bounded Object Query materialization jobs."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, JSON, CheckConstraint
from app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class QueryJob(Base):
    __tablename__ = "v2_query_jobs"
    id = Column(String(64), primary_key=True, default=lambda: f"job_{uuid4().hex}")
    ontology_id = Column(String, ForeignKey("ontology_projects.id"), nullable=False, index=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    kind = Column(String(30), nullable=False, default="materialize")
    status = Column(String(20), nullable=False, default="queued")
    request_json = Column(JSON, nullable=False)
    result_json = Column(JSON, nullable=True)
    checkpoint_json = Column(JSON, nullable=True)
    error_json = Column(JSON, nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    lease_token = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (CheckConstraint("status IN ('queued','running','completed','failed','cancelled')", name="ck_query_job_status"),)
