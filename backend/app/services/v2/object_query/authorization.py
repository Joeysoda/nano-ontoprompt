"""Request-scoped authorization wiring for Object Query.

Object Query used to perform an ontology owner/admin check and then create a
local ``QueryPolicy`` containing only a principal string.  That made the
query, aggregate, search, and background materialization paths disagree with
the Plan A object/property projection.  This module is the narrow adapter
between the shared authorization service and the storage-independent Query
Core.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject
from app.services.v2.authorization_service import (
    AuthorizationContext,
    AuthorizationProjection,
    load_applicable_policies,
    resolve_projection,
)
from app.services.v2.object_query.core import QueryPolicy
from app.services.v2.object_query.errors import ObjectQueryError


def _revision_id(db: Session, ontology_id: str, requested: str | None = None) -> str | None:
    if requested:
        return str(requested)
    project = db.get(OntologyProject, ontology_id)
    return str(project.current_revision_id) if project and project.current_revision_id else None


def build_query_policy(
    db: Session,
    ontology_id: str,
    principal_id: str,
    principal_role: str | None = None,
    *,
    metadata_revision_id: str | None = None,
    requested_fields: tuple[str, ...] = (),
    preloaded_policies: list[Any] | None = None,
) -> QueryPolicy:
    """Compile one policy set for a request and reuse it across query pages.

    ``resolve_projection`` still evaluates records on every bounded adapter
    read, but ``load_applicable_policies`` runs exactly once here.  This is
    important for ``materialize`` and SetHandle pagination: page count must
    not become policy-table query count.
    """
    revision_id = _revision_id(db, ontology_id, metadata_revision_id)
    context = AuthorizationContext(
        ontology_id=ontology_id,
        principal_id=str(principal_id),
        principal_role=principal_role,
        metadata_revision_id=revision_id,
        requested_fields=requested_fields,
    )
    policies = preloaded_policies if preloaded_policies is not None else load_applicable_policies(db, context, principal_role)

    def resolve(records: list[dict[str, Any]]) -> AuthorizationProjection:
        return resolve_projection(db, context, preloaded_policies=policies, records=records)

    return QueryPolicy(
        principal=str(principal_id),
        revision=revision_id or "plan-a-current",
        projection_resolver=resolve,
    )


def policies_from_projection(projection: AuthorizationProjection) -> list[Any]:
    """Extract the already loaded policy rows from an access decision."""
    rows = [
        *projection.object_allow_policies,
        *projection.object_deny_policies,
        *projection.field_allow_policies,
        *projection.field_deny_policies,
        *projection.link_allow_policies,
        *projection.link_deny_policies,
    ]
    return list({row.id: row for row in rows}.values())


def require_ontology_access(
    db: Session,
    ontology_id: str,
    principal_id: str,
    principal_role: str | None = None,
    *,
    metadata_revision_id: str | None = None,
) -> AuthorizationProjection:
    """Return the same ontology/data decision used by terminal queries."""
    project = db.get(OntologyProject, ontology_id)
    if project is None:
        raise ObjectQueryError("not_found", "ontology_id", "Ontology not found or inaccessible")
    revision_id = _revision_id(db, ontology_id, metadata_revision_id)
    context = AuthorizationContext(
        ontology_id=ontology_id,
        principal_id=str(principal_id),
        principal_role=principal_role,
        metadata_revision_id=revision_id,
    )
    policies = load_applicable_policies(db, context, principal_role)
    projection = resolve_projection(db, context, preloaded_policies=policies)
    if not projection.allowed:
        raise ObjectQueryError("not_found", "ontology_id", "Ontology not found or inaccessible")
    return projection


__all__ = ["build_query_policy", "policies_from_projection", "require_ontology_access"]
