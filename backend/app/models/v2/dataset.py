import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Integer, BigInteger, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class Dataset(Base):
    __tablename__ = "v2_datasets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_connection_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_connections.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # structured|semi|unstructured
    data_class: Mapped[str] = mapped_column(String(30), nullable=False, default="regular")  # regular|temporal|multimodal
    privacy_level: Mapped[str] = mapped_column(String(20), nullable=False, default="standard")  # standard|private
    readiness: Mapped[str | None] = mapped_column(String(30), nullable=True, default="ready")  # ready|installing|missing_version|repair_needed
    schema_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    latest_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class DatasetVersion(Base):
    __tablename__ = "v2_dataset_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id: Mapped[str] = mapped_column(String, ForeignKey("v2_datasets.id", ondelete="CASCADE"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rowcount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)  # MinIO s3://bucket/key
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class MediaItem(Base):
    __tablename__ = "v2_media_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_version_id: Mapped[str] = mapped_column(String, ForeignKey("v2_dataset_versions.id", ondelete="CASCADE"), nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf|docx|image|audio|depth|mask|point_cloud|metadata
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    sample_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_multimodal_samples.id", ondelete="CASCADE"), nullable=True)
    asset_role: Mapped[str] = mapped_column(String(30), nullable=False, default="primary")
    original_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ocr_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|processing|done|failed
    ocr_result_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MultimodalSample(Base):
    """A stable business sample that groups synchronized modality assets."""

    __tablename__ = "v2_multimodal_samples"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_version_id: Mapped[str] = mapped_column(String, ForeignKey("v2_dataset_versions.id", ondelete="CASCADE"), nullable=False)
    sample_key: Mapped[str] = mapped_column(String(240), nullable=False)
    scene_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    split: Mapped[str | None] = mapped_column(String(40), nullable=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    labels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
