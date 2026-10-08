"""Versioned local heterogeneous logic assets and execution records."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Text, Boolean, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class LogicAsset(Base):
    __tablename__ = "v2_logic_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    asset_key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    implementation: Mapped[str] = mapped_column(String(120), nullable=False)
    interface_key: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    interface_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0.0")
    executor_type: Mapped[str] = mapped_column(String(40), nullable=False, default="local")
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    bindings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    binding_spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0.0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="published")
    deterministic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    side_effect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class LogicAssetRun(Base):
    __tablename__ = "v2_logic_asset_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str] = mapped_column(String, ForeignKey("v2_logic_assets.id", ondelete="CASCADE"), nullable=False)
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    asset_version: Mapped[str] = mapped_column(String(40), nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LogicDerivedFact(Base):
    __tablename__ = "v2_logic_derived_facts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("v2_logic_asset_runs.id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[str] = mapped_column(String, ForeignKey("v2_logic_assets.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String, nullable=True)
    fact_key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON, nullable=True)
    source_path: Mapped[str] = mapped_column(String(300), nullable=False, default="output")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
