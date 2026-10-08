"""Pinned time-series syncs projected from published stream checkpoints."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, Integer, JSON, DateTime, ForeignKey, UniqueConstraint
from app.database import Base


class TimeSeriesSync(Base):
    __tablename__ = 'v2_time_series_syncs'
    id = Column(String(64), primary_key=True, default=lambda: str(uuid4()))
    ontology_id = Column(String, ForeignKey('ontology_projects.id'), nullable=False)
    data_view_id = Column(String(64), ForeignKey('v2_query_data_views.id'), nullable=False)
    series_id = Column(String(200), nullable=False)
    root_type = Column(String(200), nullable=False)
    root_id = Column(String(300), nullable=False)
    sensor_type = Column(String(200), nullable=False)
    sensor_id = Column(String(300), nullable=False)
    property_api_name = Column(String(200), nullable=False)
    unit = Column(String(100), nullable=False)
    interpolation = Column(String(40), nullable=False)
    points = Column(JSON, nullable=False, default=list)
    point_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint('data_view_id', 'series_id', name='uq_v2_series_view_id'),)
