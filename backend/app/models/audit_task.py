import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class AuditTask(Base):
    __tablename__ = "audit_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    model_id: Mapped[str] = mapped_column(String, ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued")  # queued|running|completed|failed|waiting_for_model|cancelled
    revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    construction_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=True)
    findings: Mapped[dict] = mapped_column(JSON, nullable=True)
    repair_draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    react_trace: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
