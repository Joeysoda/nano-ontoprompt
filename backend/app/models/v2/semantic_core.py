"""Canonical, revisioned semantic metadata for the ontology workbench.

The original application stored type definitions in ``Entity.properties`` and
``Relation.properties``.  These tables are deliberately additive: the legacy
tables remain available as compatibility projections while this module gives
new code a stable resource identity and a typed, immutable revision surface.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OntologySemanticResource(Base):
    """Stable identity for a semantic resource.

    ``api_name`` is an API identity, not a display label.  A migration may
    change it only through the explicit schema-migration service; ordinary
    editor changes can modify display fields but never silently change this
    identity.
    """

    __tablename__ = "v2_semantic_resources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    api_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_in_revision_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    retired_in_revision_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("ontology_id", "api_name", name="uq_v2_semantic_resource_api"),
        Index("ix_v2_semantic_resources_ontology_kind", "ontology_id", "kind"),
    )


class OntologySemanticResourceVersion(Base):
    """Immutable definition of a resource at an ontology revision.

    The frequently queried invariants are explicit columns.  ``constraints``
    and ``provenance`` are extension bags and are not used to bypass the
    validation performed by ``semantic_core_service``.
    """

    __tablename__ = "v2_semantic_resource_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    resource_id: Mapped[str] = mapped_column(String, ForeignKey("v2_semantic_resources.id", ondelete="CASCADE"), nullable=False)
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    revision_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_revisions.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    api_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    name_cn: Mapped[str | None] = mapped_column(String(240), nullable=True)
    name_en: Mapped[str | None] = mapped_column(String(240), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    target_resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(20), nullable=True, default="directed")
    interface_resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    value_type_resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    struct_resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    base_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    cardinality: Mapped[str | None] = mapped_column(String(30), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_field: Mapped[str | None] = mapped_column(String(240), nullable=True)
    is_identifier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_array: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    constraints_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    provenance_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("revision_id", "resource_id", name="uq_v2_semantic_resource_version"),
        UniqueConstraint("revision_id", "api_name", name="uq_v2_semantic_revision_api"),
        Index("ix_v2_semantic_resource_versions_ontology_revision", "ontology_id", "revision_id"),
    )


class OntologySourceMapping(Base):
    """Provenance mapping from a source field to a canonical resource."""

    __tablename__ = "v2_ontology_source_mappings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    revision_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_revisions.id", ondelete="CASCADE"), nullable=False)
    resource_id: Mapped[str] = mapped_column(String, ForeignKey("v2_semantic_resources.id", ondelete="CASCADE"), nullable=False)
    source_dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_table: Mapped[str | None] = mapped_column(String(240), nullable=True)
    source_field: Mapped[str | None] = mapped_column(String(240), nullable=True)
    mapping_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="field")
    mapping_status: Mapped[str] = mapped_column(String(20), nullable=False, default="confirmed")
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        Index("ix_v2_ontology_source_mappings_revision", "ontology_id", "revision_id"),
    )


__all__ = [
    "OntologySemanticResource",
    "OntologySemanticResourceVersion",
    "OntologySourceMapping",
]
