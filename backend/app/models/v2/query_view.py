"""Immutable QueryDataView manifests used for repeatable Object Query reads."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, CheckConstraint
from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class QueryDataView(Base):
    __tablename__ = "v2_query_data_views"

    id = Column(String(64), primary_key=True, default=lambda: f"view_{uuid4().hex}")
    ontology_id = Column(String, ForeignKey("ontology_projects.id"), nullable=False, index=True)
    source_manifest_digest = Column(String(128), nullable=False)
    source_snapshot_id = Column(String, ForeignKey('v2_data_model_snapshots.id'), nullable=True)
    base_view_id = Column(String(64), ForeignKey("v2_query_data_views.id"), nullable=True)
    changeset_digest = Column(String(128), nullable=True)
    changeset_version = Column(String(64), nullable=True)
    builder_version = Column(String(64), nullable=False, default="query-view-builder-v1")
    index_version = Column(String(64), nullable=False, default="instance-index-v1")
    metadata_digest = Column(String(128), nullable=False)
    content_digest = Column(String(128), nullable=True)
    graph_key = Column(String(200), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="building")
    object_count = Column(Integer, nullable=False, default=0)
    edge_count = Column(Integer, nullable=False, default=0)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    retention_until = Column(DateTime(timezone=True), nullable=True)
    refcount = Column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("status IN ('building','validating','ready','failed','expired')", name="ck_query_view_status"),
        CheckConstraint("object_count >= 0 AND edge_count >= 0 AND refcount >= 0", name="ck_query_view_counts"),
    )
