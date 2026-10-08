"""Authority records for decisions; graph is a retryable projection."""
import uuid
from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class DecisionRecord(Base):
    __tablename__ = 'v2_decisions'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey('ontology_projects.id', ondelete='CASCADE'), index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class DecisionLink(Base):
    __tablename__ = 'v2_decision_links'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey('ontology_projects.id', ondelete='CASCADE'), index=True)
    source: Mapped[str] = mapped_column(String, ForeignKey('v2_decisions.id'))
    target: Mapped[str] = mapped_column(String, ForeignKey('v2_decisions.id'))
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
