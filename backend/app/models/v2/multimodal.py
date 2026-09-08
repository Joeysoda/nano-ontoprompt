"""Media extraction fragments used for provenance-first multimodal builds."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExtractedFragment(Base):
    __tablename__ = "v2_extracted_fragments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    media_item_id: Mapped[str] = mapped_column(String, ForeignKey("v2_media_items.id", ondelete="CASCADE"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(String, ForeignKey("v2_dataset_versions.id", ondelete="CASCADE"), nullable=False)
    fragment_type: Mapped[str] = mapped_column(String(30), nullable=False)  # text|table|ocr|metadata
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    locator: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # page, bbox, row/column, etc.
    extractor: Mapped[str] = mapped_column(String(40), nullable=False)  # markitdown|ocr|rule
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="done")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
