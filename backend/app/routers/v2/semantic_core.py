"""Canonical semantic metadata API and legacy editor bridge."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_editor
from app.models.user import User
from app.services.v2.authorization_service import AuthorizationContext, AuthorizationError, redact_link_record, resolve_projection
from app.services.v2.dynamic_ontology_service import impact_for_change
from app.services.v2.semantic_core_service import SemanticCoreError, apply_semantic_change, semantic_change_impact as canonical_change_impact, semantic_schema
from app.schemas.v2.semantic_core import SemanticChangeRequest, SemanticSchemaContract

router = APIRouter(dependencies=[Depends(get_current_user)])


def _raise(exc: Exception) -> None:
    code = getattr(exc, "code", "SEMANTIC_CORE_ERROR")
    details = dict(getattr(exc, "details", {}) or {})
    status = 404 if code == "NOT_FOUND" else 409 if code in {"REVISION_CONFLICT", "CHANGE_BLOCKED"} else 422
    raise HTTPException(status_code=status, detail={"code": code, "message": str(exc), "next_action": details.pop("next_action", "检查请求上下文后重试"), "context_id": getattr(exc, "context_id", None) or uuid.uuid4().hex, **details})


def _filter_semantic_schema(db: Session, ontology_id: str, user: User, schema: dict) -> dict:
    """Return only schema resources authorized by one batched projection."""
    resources = schema.get("resources") or {}
    object_types = list(resources.get("object_type") or [])
    properties = list(resources.get("property") or [])
    type_records = []
    for item in object_types:
        type_id = str(item.get("resource_id") or item.get("id") or "")
        legacy_id = str((item.get("metadata") or {}).get("legacy_entity_id") or type_id)
        child_properties = [prop for prop in properties if str(prop.get("parent_resource_id") or "") == type_id]
        type_records.append({
            "id": type_id,
            "entity_id": legacy_id,
            "entity_type": legacy_id,
            "semantic_resource_id": type_id,
            "property_ids": [str(prop.get("resource_id") or "") for prop in child_properties],
            "properties": {str(prop.get("api_name") or ""): None for prop in child_properties if prop.get("api_name")},
        })
    projection = resolve_projection(
        db,
        AuthorizationContext(
            ontology_id=ontology_id,
            principal_id=str(user.id),
            principal_role=user.role,
            metadata_revision_id=schema.get("revision_id"),
        ),
        records=type_records,
    )
    if projection.unrestricted:
        return schema
    visible_object_ids = projection.visible_object_ids or set()
    if object_types and not visible_object_ids:
        raise SemanticCoreError("NOT_FOUND", "本体不存在")

    visible_types = [item for item in object_types if str(item.get("resource_id") or item.get("id") or "") in visible_object_ids]
    visible_type_ids = {str(item.get("resource_id") or item.get("id") or "") for item in visible_types}
    visible_properties = []
    visible_property_ids: set[str] = set()
    for type_id in visible_type_ids:
        allowed = projection.allowed_fields_by_object.get(type_id, projection.allowed_fields)
        denied = projection.denied_fields | projection.denied_fields_by_object.get(type_id, set())
        for prop in properties:
            if str(prop.get("parent_resource_id") or "") != type_id:
                continue
            api_name = str(prop.get("api_name") or "")
            if api_name and (allowed is None or api_name in allowed) and api_name not in denied:
                visible_properties.append(prop)
                visible_property_ids.add(str(prop.get("resource_id") or prop.get("id") or ""))

    visible_links = []
    visible_link_ids: set[str] = set()
    for link in resources.get("link_type") or []:
        link_id = str(link.get("resource_id") or link.get("id") or "")
        if str(link.get("source_resource_id") or "") not in visible_type_ids or str(link.get("target_resource_id") or "") not in visible_type_ids:
            continue
        redacted = redact_link_record(
            projection,
            {"id": link_id, "link_type_id": link_id, "type": link.get("api_name"), "properties": dict(link)},
            link_type_id=link_id,
        )
        if redacted is None:
            continue
        visible_link = dict(link)
        redacted_properties = redacted.get("properties") or {}
        for field in ("source_resource_id", "target_resource_id", "source_name", "target_name", "direction", "cardinality", "description", "name_cn", "name_en"):
            if field in redacted_properties and redacted_properties[field] is None:
                visible_link[field] = None
        visible_links.append(visible_link)
        visible_link_ids.add(link_id)

    # Include referenced value/interface/struct definitions needed to interpret
    # the fields and links that survived authorization. Rules have no separate
    # policy scope yet, so they remain hidden from non-owners.
    referenced_ids: set[str] = set(visible_type_ids | visible_property_ids | visible_link_ids)
    for item in [*visible_properties, *visible_links]:
        for field in ("value_type_resource_id", "struct_resource_id", "interface_resource_id", "source_resource_id", "target_resource_id"):
            if item.get(field):
                referenced_ids.add(str(item[field]))
    visible_resources: dict[str, list[dict]] = {
        kind: [] for kind in resources
    }
    visible_resources["object_type"] = visible_types
    visible_resources["property"] = visible_properties
    visible_resources["link_type"] = visible_links
    for kind in ("interface", "value_type", "value_type_version", "struct", "struct_field", "shared_property", "interface_implementation"):
        for item in resources.get(kind) or []:
            resource_id = str(item.get("resource_id") or item.get("id") or "")
            parent_id = str(item.get("parent_resource_id") or item.get("struct_resource_id") or "")
            if resource_id in referenced_ids or parent_id in visible_type_ids or parent_id in referenced_ids:
                visible_resources.setdefault(kind, []).append(item)
                referenced_ids.add(resource_id)

    visible_resources["logic_rule"] = []
    visible_resources["source_mapping"] = []
    visible_mappings = [
        item for item in schema.get("source_mappings", [])
        if item.get("resource_id") in visible_property_ids and item.get("mapping_status") == "confirmed"
    ]
    result = dict(schema)
    result["resources"] = visible_resources
    result["all_resources"] = [item for items in visible_resources.values() for item in items]
    result["source_mappings"] = visible_mappings
    result["counts"] = {kind: len(items) for kind, items in visible_resources.items()}
    return result


@router.get("/{ontology_id}/semantic-schema", response_model=SemanticSchemaContract)
def get_semantic_schema(ontology_id: str, revision_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        schema = semantic_schema(db, ontology_id, revision_id)
        return _filter_semantic_schema(db, ontology_id, user, schema) if isinstance(user, User) else schema
    except (SemanticCoreError, AuthorizationError) as exc:
        _raise(exc)


@router.post("/{ontology_id}/semantic-changes/impact")
def semantic_change_impact(ontology_id: str, body: SemanticChangeRequest, db: Session = Depends(get_db), _=Depends(require_editor)):
    body_data = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else dict(body)
    kind = str(body_data.get("resource_kind") or body_data.get("target_kind") or body_data.get("kind") or "").lower()
    if kind in {"object_type", "entity_type", "entity", "property", "attribute", "link_type", "relationship", "relation", "logic_rule", "rule", "interface", "interface_implementation", "shared_property", "value_type", "value_type_version", "struct", "struct_field", "source_mapping"} and body_data.get("resource_kind"):
        try:
            return canonical_change_impact(db, ontology_id, body_data)
        except Exception as exc:
            _raise(exc)
    if kind in {"entity_type", "entity", "property", "attribute", "link_type", "relationship", "relation", "logic_rule", "rule"}:
        try:
            return impact_for_change(db, ontology_id, {**body_data, "target_kind": {"object_type": "entity_type", "link_type": "relationship"}.get(kind, kind)})
        except Exception as exc:
            _raise(exc)
    return {"ontology_id": ontology_id, "can_apply": True, "canonical": True, "impact": {"resource_kind": kind, "requires_revision": True, "requires_shadow_projection": kind in {"value_type", "struct", "struct_field"}}}


@router.post("/{ontology_id}/semantic-changes")
def create_semantic_change(ontology_id: str, body: SemanticChangeRequest, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    try:
        body_data = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else dict(body)
        return apply_semantic_change(db, ontology_id, body_data, user_id=user.id)
    except SemanticCoreError as exc:
        _raise(exc)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": "SEMANTIC_CHANGE_FAILED", "message": str(exc)[:800], "next_action": "检查请求字段和当前修订后重试", "context_id": uuid.uuid4().hex}) from exc
