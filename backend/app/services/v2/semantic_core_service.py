"""Canonical semantic metadata and legacy compatibility adapters.

The service intentionally keeps the public implementation small.  A semantic
resource has a stable identity, while its definition is immutable per
``OntologyRevision``.  Legacy ``Entity``/``Relation`` rows are projected into
this plane and old edit calls are translated through the same revision-aware
service instead of creating a second source of truth.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.action import Action
from app.models.logic import LogicRule
from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.relation import Relation
from app.models.v2.construction import EvidenceRef
from app.models.v2.semantic_core import OntologySemanticResource, OntologySemanticResourceVersion, OntologySourceMapping
from app.services.v2.revision_service import create_revision, get_storage_service, snapshot_ontology


SEMANTIC_KINDS = {
    "object_type",
    "property",
    "interface",
    "interface_implementation",
    "link_type",
    "shared_property",
    "value_type",
    "value_type_version",
    "struct",
    "struct_field",
    "logic_rule",
    "source_mapping",
}
CARDINALITIES = {"one-to-one", "one-to-many", "many-to-one", "many-to-many", "optional-one", "optional-many"}
BASE_TYPES = {"string", "text", "integer", "long", "double", "float", "boolean", "date", "timestamp", "datetime", "decimal", "uuid", "json", "geopoint", "media"}
SAFE_API_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,199}$")
RESOURCE_NAMESPACE = uuid.UUID("7a22c8b1-9c2c-4b58-a2fd-2a19b1f8db6d")


class SemanticCoreError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.context_id = str(uuid.uuid4())


def _safe_api_name(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)
    if not text or not text[0].isalpha():
        text = f"{fallback}_{text}" if text else fallback
    return text[:200]


def _stable_resource_id(ontology_id: str, kind: str, legacy_id: str) -> str:
    return str(uuid.uuid5(RESOURCE_NAMESPACE, f"{ontology_id}:{kind}:{legacy_id}"))


def _current_revision(db: Session, ontology_id: str) -> OntologyRevision | None:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise SemanticCoreError("NOT_FOUND", "本体不存在")
    if project.current_revision_id:
        row = db.query(OntologyRevision).filter(OntologyRevision.id == project.current_revision_id).first()
        if row:
            return row
    return db.query(OntologyRevision).filter(
        OntologyRevision.ontology_id == ontology_id,
        OntologyRevision.is_current.is_(True),
    ).order_by(OntologyRevision.revision_no.desc()).first()


def _property_definitions(entity: Entity) -> list[dict[str, Any]]:
    raw = entity.properties or {}
    if isinstance(raw, list):
        return [copy.deepcopy(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        if isinstance(raw.get("property_definitions"), list):
            return [copy.deepcopy(item) for item in raw["property_definitions"] if isinstance(item, dict)]
        if isinstance(raw.get("properties"), list):
            return [copy.deepcopy(item) for item in raw["properties"] if isinstance(item, dict)]
        return [
            {"id": str(key), "name": str(key), "type": "string", "example": value}
            for key, value in raw.items()
            if key not in {"schema_version", "source_fields", "evidence", "color", "icon", "data_class"}
        ]
    return []


def _normalize_cardinality(value: Any) -> str:
    aliases = {
        "1:1": "one-to-one",
        "1:N": "one-to-many",
        "N:1": "many-to-one",
        "N:M": "many-to-many",
        "M:N": "many-to-many",
        "0..1:1": "optional-one",
        "0..1:N": "optional-many",
        "0..*:N": "many-to-many",
        "0..*:1": "many-to-one",
    }
    normalized = aliases.get(str(value or "").strip(), str(value or "").strip())
    return normalized if normalized in CARDINALITIES else "many-to-many"


def _upsert_resource(db: Session, ontology_id: str, revision_id: str, *, kind: str, legacy_id: str, api_name: str, **values: Any) -> OntologySemanticResourceVersion:
    if kind not in SEMANTIC_KINDS:
        raise SemanticCoreError("INVALID_RESOURCE_KIND", f"不支持的语义资源类型：{kind}")
    resource_id = str(values.pop("resource_id", None) or _stable_resource_id(ontology_id, kind, legacy_id))
    resource = db.query(OntologySemanticResource).filter(OntologySemanticResource.id == resource_id).first()
    if not resource:
        # Legacy properties are often named ``id``, ``value`` or ``status``
        # on several object types.  The canonical plane keeps API names
        # unique within an ontology, so retain the readable name when it is
        # free and add a deterministic owner suffix only on collision.
        existing = db.query(OntologySemanticResource).filter(
            OntologySemanticResource.ontology_id == ontology_id,
            OntologySemanticResource.api_name == api_name,
        ).first()
        if existing:
            suffix = re.sub(r"[^A-Za-z0-9]+", "_", legacy_id).strip("_")[-32:] or "resource"
            api_name = _safe_api_name(f"{api_name}_{suffix}", kind)
            counter = 2
            while db.query(OntologySemanticResource).filter(
                OntologySemanticResource.ontology_id == ontology_id,
                OntologySemanticResource.api_name == api_name,
            ).first():
                api_name = _safe_api_name(f"{api_name}_{counter}", kind)
                counter += 1
        resource = OntologySemanticResource(
            id=resource_id,
            ontology_id=ontology_id,
            kind=kind,
            api_name=api_name,
            created_in_revision_id=revision_id,
        )
        db.add(resource)
        db.flush()
    version = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.revision_id == revision_id,
        OntologySemanticResourceVersion.resource_id == resource.id,
    ).first()
    if version:
        return version
    version = OntologySemanticResourceVersion(
        id=str(uuid.uuid4()), resource_id=resource.id, ontology_id=ontology_id,
        revision_id=revision_id, kind=kind, api_name=api_name,
        **{key: value for key, value in values.items() if value is not None},
    )
    db.add(version)
    db.flush()
    return version


def _resource_id_for_legacy(db: Session, ontology_id: str, kind: str, legacy_id: str) -> str:
    resource = db.query(OntologySemanticResource).filter(
        OntologySemanticResource.ontology_id == ontology_id,
        OntologySemanticResource.kind == kind,
        OntologySemanticResource.id == _stable_resource_id(ontology_id, kind, legacy_id),
    ).first()
    return resource.id if resource else _stable_resource_id(ontology_id, kind, legacy_id)


def _resource_payload(row: OntologySemanticResourceVersion) -> dict[str, Any]:
    return {
        "id": row.resource_id,
        "resource_id": row.resource_id,
        "revision_id": row.revision_id,
        "kind": row.kind,
        "api_name": row.api_name,
        "display_name": row.display_name,
        "name_cn": row.name_cn,
        "name_en": row.name_en,
        "description": row.description,
        "parent_resource_id": row.parent_resource_id,
        "source_resource_id": row.source_resource_id,
        "target_resource_id": row.target_resource_id,
        "source_name": row.source_name,
        "target_name": row.target_name,
        "direction": row.direction,
        "interface_resource_id": row.interface_resource_id,
        "value_type_resource_id": row.value_type_resource_id,
        "struct_resource_id": row.struct_resource_id,
        "base_type": row.base_type,
        "cardinality": row.cardinality,
        "unit": row.unit,
        "source_field": row.source_field,
        "is_identifier": bool(row.is_identifier),
        "is_required": bool(row.is_required),
        "is_array": bool(row.is_array),
        "constraints": row.constraints_json or {},
        "provenance": row.provenance_json or {},
        "metadata": row.metadata_json or {},
    }


def _digest(rows: list[OntologySemanticResourceVersion]) -> str:
    payload = json.dumps(
        [_resource_payload(row) for row in sorted(rows, key=lambda item: (item.kind, item.api_name, item.resource_id))],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sync_legacy_to_semantic(db: Session, ontology_id: str, revision_id: str, *, commit: bool = False) -> dict[str, Any]:
    """Backfill one revision from the current legacy domain tables.

    The operation is idempotent.  It never deletes a canonical resource and
    never mutates an existing version row, which makes it safe during startup
    repair and during the compatibility period.
    """
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    revision = db.query(OntologyRevision).filter(OntologyRevision.id == revision_id, OntologyRevision.ontology_id == ontology_id).first()
    if not project or not revision:
        raise SemanticCoreError("NOT_FOUND", "本体或修订不存在")
    entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).order_by(Entity.id.asc()).all()
    entity_resources: dict[str, str] = {}
    for entity in entities:
        legacy_id = str(entity.id)
        api_name = _safe_api_name(entity.canonical_id or entity.id, "ObjectType")
        object_version = _upsert_resource(
            db, ontology_id, revision_id, kind="object_type", legacy_id=legacy_id, api_name=api_name,
            display_name=entity.name_cn or entity.name_en or api_name,
            name_cn=entity.name_cn, name_en=entity.name_en, description=entity.description,
            metadata_json={"legacy_entity_id": entity.id, "type": entity.type, "confidence": entity.confidence},
            provenance_json={"adapter": "legacy_entity"},
        )
        entity_resources[entity.id] = object_version.resource_id
        if entity.semantic_resource_id != object_version.resource_id:
            entity.semantic_resource_id = object_version.resource_id
        for prop in _property_definitions(entity):
            prop_id = str(prop.get("id") or prop.get("name") or "property")
            prop_resource = _upsert_resource(
                db, ontology_id, revision_id, kind="property", legacy_id=f"{entity.id}:{prop_id}",
                api_name=_safe_api_name(prop_id, "property"),
                display_name=str(prop.get("label") or prop.get("name") or prop_id),
                name_cn=prop.get("name_cn"), name_en=prop.get("name_en"),
                description=prop.get("description"), parent_resource_id=object_version.resource_id,
                base_type=str(prop.get("type") or "string").lower(), unit=prop.get("unit"),
                source_field=prop.get("source_field"), is_identifier=bool(prop.get("is_identifier", False)),
                is_required=bool(prop.get("required", prop.get("is_required", False))),
                is_array=bool(prop.get("is_array", False)), constraints_json={"enum": prop.get("enum") or prop.get("values") or []},
                provenance_json={"adapter": "legacy_entity", "legacy_property_id": prop_id},
            )
            source_field = prop.get("source_field")
            if source_field:
                exists = db.query(OntologySourceMapping).filter(
                    OntologySourceMapping.revision_id == revision_id,
                    OntologySourceMapping.resource_id == prop_resource.resource_id,
                    OntologySourceMapping.source_field == str(source_field),
                ).first()
                if not exists:
                    db.add(OntologySourceMapping(
                        ontology_id=ontology_id, revision_id=revision_id, resource_id=prop_resource.resource_id,
                        source_field=str(source_field), mapping_kind="field", mapping_status="confirmed",
                        evidence_json={"adapter": "legacy_entity"},
                    ))
    db.flush()

    # Existing instances remain source-owned and read-only, but their
    # canonical Object Type identity is filled once the resource mapping is
    # known.  This makes legacy row lookups and the semantic plane converge
    # without rewriting the source payload.
    for instance in db.query(EntityInstance).filter(EntityInstance.ontology_id == ontology_id).all():
        resource_id = entity_resources.get(instance.entity_id)
        if resource_id and instance.object_type_resource_id != resource_id:
            instance.object_type_resource_id = resource_id

    for relation in db.query(Relation).filter(Relation.ontology_id == ontology_id).order_by(Relation.id.asc()).all():
        source_id = entity_resources.get(relation.source_entity) or _resource_id_for_legacy(db, ontology_id, "object_type", relation.source_entity)
        target_id = entity_resources.get(relation.target_entity) or _resource_id_for_legacy(db, ontology_id, "object_type", relation.target_entity)
        properties = relation.properties or {}
        _upsert_resource(
            db, ontology_id, revision_id, kind="link_type", legacy_id=str(relation.id), api_name=_safe_api_name(properties.get("api_name") or relation.type, "link"),
            display_name=str(properties.get("name") or relation.type or relation.id),
            name_cn=properties.get("name_cn"), name_en=properties.get("name_en"),
            description=properties.get("description"), source_resource_id=source_id, target_resource_id=target_id,
            source_name=properties.get("source_name") or properties.get("from_name"),
            target_name=properties.get("target_name") or properties.get("to_name"),
            direction=str(properties.get("direction") or "directed"),
            cardinality=_normalize_cardinality(properties.get("cardinality")),
            constraints_json={"legacy_relation_id": relation.id, "attributes": properties.get("attributes") or {}},
            provenance_json={"adapter": "legacy_relation"},
        )
    for rule in db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).order_by(LogicRule.id.asc()).all():
        linked_entities = [
            entity_resources.get(str(item), str(item))
            for item in (rule.linked_entities or [])
        ]
        _upsert_resource(
            db, ontology_id, revision_id, kind="logic_rule", legacy_id=str(rule.id), api_name=_safe_api_name(rule.name_en or rule.name_cn or rule.id, "rule"),
            display_name=rule.name_cn or rule.name_en or rule.id, name_cn=rule.name_cn, name_en=rule.name_en,
            description=rule.description, constraints_json={"legacy_rule_id": rule.id, "condition": rule.condition_json or {}, "effect": rule.effect_json or {}, "linked_entities": linked_entities, "enabled": bool(rule.enabled)},
            provenance_json={"adapter": "legacy_logic_rule", "model_invocation_id": rule.model_invocation_id},
        )
    db.flush()
    rows = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.revision_id == revision_id,
    ).all()
    revision.metadata_digest = _digest(rows)
    revision.metadata_schema_version = "semantic-core-v1"
    if commit:
        db.commit()
    return {"revision_id": revision_id, "resource_count": len(rows), "metadata_digest": revision.metadata_digest}


def ensure_semantic_metadata(db: Session, ontology_id: str, revision_id: str | None = None, *, commit: bool = False) -> dict[str, Any]:
    revision = db.query(OntologyRevision).filter(
        OntologyRevision.id == revision_id,
        OntologyRevision.ontology_id == ontology_id,
    ).first() if revision_id else _current_revision(db, ontology_id)
    if revision_id and not revision:
        raise SemanticCoreError("NOT_FOUND", "本体修订不存在")
    if not revision:
        revision = create_revision(db, ontology_id, commit=False)
    count = db.query(OntologySemanticResourceVersion.id).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.revision_id == revision.id,
    ).count()
    if count == 0:
        return sync_legacy_to_semantic(db, ontology_id, revision.id, commit=commit)
    rows = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.revision_id == revision.id,
    ).all()
    digest = _digest(rows)
    if revision.metadata_digest != digest:
        revision.metadata_digest = digest
        if commit:
            db.commit()
    return {"revision_id": revision.id, "resource_count": len(rows), "metadata_digest": digest}


def semantic_schema(db: Session, ontology_id: str, revision_id: str | None = None) -> dict[str, Any]:
    # First access may be the compatibility backfill for an older ontology.
    # Persist that idempotent migration so a read does not recreate the same
    # semantic rows on every request after the session closes.
    info = ensure_semantic_metadata(db, ontology_id, revision_id, commit=True)
    revision = db.query(OntologyRevision).filter(OntologyRevision.id == info["revision_id"]).first()
    rows = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.revision_id == info["revision_id"],
    ).order_by(OntologySemanticResourceVersion.kind.asc(), OntologySemanticResourceVersion.api_name.asc()).all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.kind, []).append(_resource_payload(row))
    source_mappings = db.query(OntologySourceMapping).filter(
        OntologySourceMapping.ontology_id == ontology_id,
        OntologySourceMapping.revision_id == info["revision_id"],
    ).order_by(
        OntologySourceMapping.source_dataset_id.asc(),
        OntologySourceMapping.source_table.asc(),
        OntologySourceMapping.source_field.asc(),
        OntologySourceMapping.id.asc(),
    ).all()
    return {
        "ontology_id": ontology_id,
        "revision_id": info["revision_id"],
        "revision_no": revision.revision_no if revision else None,
        "metadata_digest": info["metadata_digest"],
        "schema_version": "semantic-core-v1",
        "resources": grouped,
        "all_resources": [_resource_payload(row) for row in rows],
        "source_mappings": [{
            "id": row.id,
            "resource_id": row.resource_id,
            "source_dataset_id": row.source_dataset_id,
            "source_version_id": row.source_version_id,
            "source_table": row.source_table,
            "source_field": row.source_field,
            "mapping_kind": row.mapping_kind,
            "mapping_status": row.mapping_status,
            "evidence": row.evidence_json or {},
        } for row in source_mappings],
        "counts": {kind: len(items) for kind, items in grouped.items()},
        "legacy_compatibility": {"entities": True, "relations": True, "writes_translated": True, "deprecated": True},
    }


def _copy_semantic_versions(db: Session, source_revision_id: str, target_revision_id: str) -> list[OntologySemanticResourceVersion]:
    rows = db.query(OntologySemanticResourceVersion).filter(OntologySemanticResourceVersion.revision_id == source_revision_id).all()
    copied: list[OntologySemanticResourceVersion] = []
    for row in rows:
        values = {
            column.name: getattr(row, column.name)
            for column in OntologySemanticResourceVersion.__table__.columns
            if column.name not in {"id", "revision_id", "created_at"}
        }
        values.update({"id": str(uuid.uuid4()), "revision_id": target_revision_id})
        clone = OntologySemanticResourceVersion(**values)
        db.add(clone)
        copied.append(clone)
    db.flush()
    return copied


def _copy_source_mappings(
    db: Session,
    source_revision_id: str,
    target_revision_id: str,
    copied_versions: list[OntologySemanticResourceVersion],
) -> None:
    """Carry provenance mappings forward with revision-local mapping IDs."""
    source_rows = db.query(OntologySourceMapping).filter(
        OntologySourceMapping.revision_id == source_revision_id,
    ).all()
    id_map: dict[str, str] = {}
    for mapping in source_rows:
        new_id = str(uuid.uuid4())
        values = {
            column.name: getattr(mapping, column.name)
            for column in OntologySourceMapping.__table__.columns
            if column.name not in {"id", "revision_id", "created_at"}
        }
        db.add(OntologySourceMapping(id=new_id, revision_id=target_revision_id, **values))
        id_map[str(mapping.id)] = new_id
    for version in copied_versions:
        if version.kind != "source_mapping":
            continue
        metadata = copy.deepcopy(version.metadata_json or {})
        old_id = str(metadata.get("source_mapping_row_id") or "")
        if old_id in id_map:
            metadata["source_mapping_row_id"] = id_map[old_id]
            version.metadata_json = metadata
    db.flush()


def _validate_canonical_payload(
    db: Session,
    ontology_id: str,
    revision_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    ignore_resource_id: str | None = None,
) -> dict[str, Any]:
    if kind not in SEMANTIC_KINDS:
        raise SemanticCoreError("INVALID_RESOURCE_KIND", f"不支持的语义资源类型：{kind}")
    api_name = str(payload.get("api_name") or payload.get("name") or "").strip()
    if not api_name or not SAFE_API_NAME.fullmatch(api_name):
        raise SemanticCoreError("INVALID_API_NAME", "api_name 必须以字母开头，只能包含字母、数字、下划线、点、冒号或短横线")
    existing_resource_query = db.query(OntologySemanticResource).filter(
        OntologySemanticResource.ontology_id == ontology_id,
        OntologySemanticResource.api_name == api_name,
    )
    if ignore_resource_id:
        existing_resource_query = existing_resource_query.filter(OntologySemanticResource.id != str(ignore_resource_id))
    existing_resource = existing_resource_query.first()
    if existing_resource:
        raise SemanticCoreError("DUPLICATE_API_NAME", f"当前本体已有同名资源：{api_name}")
    duplicate_query = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.revision_id == revision_id,
        OntologySemanticResourceVersion.api_name == api_name,
    )
    if ignore_resource_id:
        duplicate_query = duplicate_query.filter(OntologySemanticResourceVersion.resource_id != str(ignore_resource_id))
    duplicate = duplicate_query.first()
    if duplicate:
        raise SemanticCoreError("DUPLICATE_API_NAME", f"当前修订已有同名资源：{api_name}")
    base_type = str(payload.get("base_type") or payload.get("type") or "").lower() or None
    if kind in {"property", "value_type", "value_type_version", "struct_field"} and base_type and base_type not in BASE_TYPES:
        raise SemanticCoreError("INVALID_BASE_TYPE", f"不支持的数据类型：{base_type}")
    if kind == "struct_field" and bool(payload.get("is_array")):
        raise SemanticCoreError("INVALID_STRUCT", "Struct 字段不能是数组")
    if kind == "value_type" and not base_type:
        raise SemanticCoreError("INVALID_VALUE_TYPE", "Value Type 必须声明基础类型")
    cardinality = payload.get("cardinality")
    if kind in {"link_type", "interface_implementation"} and cardinality:
        normalized_cardinality = _normalize_cardinality(cardinality)
        if str(cardinality).strip() not in CARDINALITIES and normalized_cardinality == "many-to-many" and str(cardinality).strip() not in {"N:M", "M:N", "0..*:N"}:
            raise SemanticCoreError("INVALID_CARDINALITY", f"关系基数无效：{cardinality}")
        cardinality = normalized_cardinality
    if kind == "link_type" and payload.get("direction") not in (None, "directed", "undirected"):
        raise SemanticCoreError("INVALID_DIRECTION", "Link Type direction 只能是 directed 或 undirected")

    def referenced(resource_id: Any, expected: set[str], label: str) -> OntologySemanticResourceVersion:
        ref = str(resource_id or "")
        row = db.query(OntologySemanticResourceVersion).filter(
            OntologySemanticResourceVersion.ontology_id == ontology_id,
            OntologySemanticResourceVersion.revision_id == revision_id,
            OntologySemanticResourceVersion.resource_id == ref,
        ).first()
        if not row or row.kind not in expected:
            raise SemanticCoreError("INVALID_RESOURCE_REFERENCE", f"{label} 引用的规范资源不存在或类型不匹配")
        return row

    if kind == "link_type":
        referenced(payload.get("source_resource_id") or payload.get("source_id"), {"object_type", "interface"}, "source_resource_id")
        referenced(payload.get("target_resource_id") or payload.get("target_id"), {"object_type", "interface"}, "target_resource_id")
        if not cardinality:
            raise SemanticCoreError("INVALID_CARDINALITY", "Link Type 必须声明基数")
    elif kind == "interface_implementation":
        referenced(payload.get("interface_resource_id"), {"interface"}, "interface_resource_id")
        referenced(payload.get("parent_resource_id") or payload.get("object_type_resource_id"), {"object_type"}, "parent_resource_id")
    elif kind == "property":
        referenced(payload.get("parent_resource_id") or payload.get("parent_id") or payload.get("object_type_resource_id"), {"object_type", "interface", "struct"}, "parent_resource_id")
    elif kind == "struct":
        fields = payload.get("fields") or []
        if not isinstance(fields, list):
            raise SemanticCoreError("INVALID_STRUCT", "Struct fields 必须是列表")
        if any(
            isinstance(item, dict)
            and (
                str(item.get("type") or item.get("base_type") or "").lower() in {"struct", "array<struct>"}
                or item.get("struct_resource_id")
                or bool(item.get("is_array"))
            )
            for item in fields
        ):
            raise SemanticCoreError("INVALID_STRUCT", "Struct 不允许嵌套 Struct")
    elif kind == "struct_field":
        referenced(payload.get("struct_resource_id"), {"struct"}, "struct_resource_id")
        if str(base_type or "").lower() == "struct":
            raise SemanticCoreError("INVALID_STRUCT", "Struct 字段不能再次引用 Struct")
    elif kind == "value_type_version":
        referenced(payload.get("value_type_resource_id") or payload.get("parent_resource_id"), {"value_type"}, "value_type_resource_id")
    elif kind == "source_mapping":
        mapped_resource_id = payload.get("mapped_resource_id") or (payload.get("metadata") or {}).get("mapped_resource_id") or payload.get("resource_id")
        if not mapped_resource_id:
            raise SemanticCoreError("INVALID_SOURCE_MAPPING", "Source Mapping 必须指定 resource_id")
        referenced(mapped_resource_id, SEMANTIC_KINDS - {"source_mapping"}, "resource_id")
        if not (payload.get("source_field") or payload.get("source_table")):
            raise SemanticCoreError("INVALID_SOURCE_MAPPING", "Source Mapping 必须指定 source_field 或 source_table")
        mapping_status = str(payload.get("mapping_status") or (payload.get("metadata") or {}).get("mapping_status") or "candidate")
        if mapping_status not in {"candidate", "confirmed", "rejected"}:
            raise SemanticCoreError("INVALID_SOURCE_MAPPING_STATUS", "mapping_status 必须是 candidate、confirmed 或 rejected")
    elif kind == "logic_rule":
        linked_entities = payload.get("linked_entities") or (payload.get("constraints") or {}).get("linked_entities") or []
        if not isinstance(linked_entities, list):
            raise SemanticCoreError("INVALID_RULE_REFERENCE", "逻辑规则的 linked_entities 必须是列表")
        for resource_id in linked_entities:
            referenced(resource_id, {"object_type", "property", "interface", "link_type"}, "linked_entities")
    return {"api_name": api_name, "base_type": base_type, "cardinality": cardinality}


def _canonical_delete_blockers(
    db: Session,
    ontology_id: str,
    revision_id: str,
    kind: str,
    target: OntologySemanticResourceVersion,
) -> list[dict[str, Any]]:
    """Return concrete blockers for a destructive canonical change.

    Impact preview and apply must use the same check.  Keeping it in one
    helper prevents the preview from saying a drop is safe when the commit
    path would later discover instances, evidence, or semantic references.
    """
    blockers: list[dict[str, Any]] = []
    if kind == "object_type":
        entity = _legacy_entity_for_resource(db, ontology_id, target.resource_id)
        if entity:
            instance_count = db.query(EntityInstance.id).filter(
                EntityInstance.ontology_id == ontology_id,
                EntityInstance.entity_id == entity.id,
            ).count()
            relation_count = db.query(Relation.id).filter(
                Relation.ontology_id == ontology_id,
                (Relation.source_entity == entity.id) | (Relation.target_entity == entity.id),
            ).count()
            evidence_count = db.query(EvidenceRef.id).filter(
                EvidenceRef.ontology_id == ontology_id,
                EvidenceRef.assertion_id == entity.id,
            ).count()
            if instance_count:
                blockers.append({"kind": "instances", "count": instance_count, "message": f"仍有 {instance_count} 个真实实例"})
            if relation_count:
                blockers.append({"kind": "relationships", "count": relation_count, "message": f"仍被 {relation_count} 条关系引用"})
            if evidence_count:
                blockers.append({"kind": "evidence", "count": evidence_count, "message": f"仍有 {evidence_count} 条证据引用"})
    elif kind == "property":
        entity, definition = _legacy_property_parent(db, ontology_id, target.resource_id)
        if not entity or not definition:
            blockers.append({"kind": "compatibility", "message": "属性没有可解析的所属 Object Type"})
        else:
            prop_id = str(definition.get("id") or definition.get("name"))
            instance_count = sum(
                1
                for row in db.query(EntityInstance).filter(
                    EntityInstance.ontology_id == ontology_id,
                    EntityInstance.entity_id == entity.id,
                ).all()
                if isinstance(row.row_data, dict) and row.row_data.get(prop_id) not in (None, "")
            )
            if instance_count:
                blockers.append({"kind": "instances", "count": instance_count, "message": f"仍有 {instance_count} 个实例包含该字段"})
    elif kind == "link_type":
        relation = _legacy_relation_for_resource(db, ontology_id, target.resource_id)
        if relation:
            evidence_count = db.query(EvidenceRef.id).filter(
                EvidenceRef.ontology_id == ontology_id,
                EvidenceRef.assertion_id == relation.id,
            ).count()
            if evidence_count:
                blockers.append({"kind": "evidence", "count": evidence_count, "message": f"仍有 {evidence_count} 条证据引用"})
    elif kind == "logic_rule":
        legacy_rule_id = str((target.constraints_json or {}).get("legacy_rule_id") or target.resource_id)
        action_count = sum(
            1
            for row in db.query(Action).filter(Action.ontology_id == ontology_id).all()
            if legacy_rule_id in (row.linked_logic_ids or [])
        )
        if action_count:
            blockers.append({"kind": "actions", "count": action_count, "message": f"仍被 {action_count} 个动作引用"})

    references = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.revision_id == revision_id,
        OntologySemanticResourceVersion.resource_id != target.resource_id,
        (OntologySemanticResourceVersion.parent_resource_id == target.resource_id)
        | (OntologySemanticResourceVersion.source_resource_id == target.resource_id)
        | (OntologySemanticResourceVersion.target_resource_id == target.resource_id)
        | (OntologySemanticResourceVersion.interface_resource_id == target.resource_id)
        | (OntologySemanticResourceVersion.value_type_resource_id == target.resource_id)
        | (OntologySemanticResourceVersion.struct_resource_id == target.resource_id),
    ).count()
    if references:
        blockers.append({"kind": "semantic_references", "count": references, "message": f"仍被 {references} 个规范资源引用"})
    return blockers


def semantic_change_impact(db: Session, ontology_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Validate a canonical operation without changing the current revision."""
    raw_kind = str(body.get("resource_kind") or body.get("target_kind") or body.get("kind") or "").strip().lower()
    aliases = {"entity_type": "object_type", "entity": "object_type", "relationship": "link_type", "relation": "link_type", "rule": "logic_rule", "attribute": "property"}
    kind = aliases.get(raw_kind, raw_kind)
    operation = str(body.get("operation") or "").lower()
    if operation not in {"add", "update", "delete"}:
        raise SemanticCoreError("INVALID_OPERATION", "操作必须是 add、update 或 delete")
    current = _current_revision(db, ontology_id)
    expected = body.get("base_revision_id")
    if expected and str(expected) != str(current.id):
        raise SemanticCoreError("REVISION_CONFLICT", "页面基于旧修订，请刷新后重试", details={"expected": expected, "actual": current.id})
    ensure_semantic_metadata(db, ontology_id, current.id)
    payload = copy.deepcopy(body.get("payload") or body.get("resource") or {})
    target_id = str(body.get("resource_id") or body.get("target_id") or "")
    target = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.revision_id == current.id,
        OntologySemanticResourceVersion.resource_id == target_id,
    ).first() if target_id else None
    if operation == "add":
        validated = _validate_canonical_payload(db, ontology_id, current.id, kind, payload)
        return {
            "ontology_id": ontology_id,
            "base_revision_id": current.id,
            "resource_kind": kind,
            "operation": operation,
            "can_apply": True,
            "validation": {"ok": True, "api_name": validated["api_name"]},
            "impact": {"creates": [{"kind": kind, "api_name": validated["api_name"]}], "blockers": [], "requires_revision": True},
        }
    if not target:
        raise SemanticCoreError("NOT_FOUND", "规范资源不存在")
    if operation == "update":
        if target.kind != kind:
            raise SemanticCoreError("INVALID_RESOURCE_KIND", "目标规范资源类型与请求不一致")
        if payload.get("api_name") and payload["api_name"] != target.api_name:
            raise SemanticCoreError("API_NAME_MIGRATION_REQUIRED", "API name 变更必须通过 schema migration")
        merged_payload = _resource_payload(target)
        merged_payload.update(payload)
        _validate_canonical_payload(
            db,
            ontology_id,
            current.id,
            kind,
            merged_payload,
            ignore_resource_id=target.resource_id,
        )
        if kind in {"value_type", "value_type_version"} and any(key in payload for key in ("base_type", "type", "constraints")):
            raise SemanticCoreError("VALUE_TYPE_IMMUTABLE", "Value Type 基础类型和约束需创建新版本")
        return {
            "ontology_id": ontology_id,
            "base_revision_id": current.id,
            "resource_kind": kind,
            "operation": operation,
            "can_apply": True,
            "validation": {"ok": True},
            "impact": {"updates": [{"resource_id": target.resource_id, "api_name": target.api_name}], "blockers": [], "requires_revision": True},
        }
    if target.kind != kind:
        raise SemanticCoreError("INVALID_RESOURCE_KIND", "目标规范资源类型与请求不一致")
    blockers = _canonical_delete_blockers(db, ontology_id, current.id, kind, target)
    return {
        "ontology_id": ontology_id,
        "base_revision_id": current.id,
        "resource_kind": kind,
        "operation": operation,
        "can_apply": not blockers,
        "validation": {"ok": not blockers},
        "impact": {"deletes": [{"resource_id": target.resource_id, "api_name": target.api_name}], "blockers": blockers, "requires_revision": True},
    }


def _legacy_entity_for_resource(db: Session, ontology_id: str, resource_id: str) -> Entity | None:
    """Resolve the compatibility Entity for a canonical Object Type."""
    row = db.query(Entity).filter(
        Entity.ontology_id == ontology_id,
        Entity.semantic_resource_id == resource_id,
    ).first()
    if row:
        return row
    version = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.resource_id == resource_id,
        OntologySemanticResourceVersion.kind == "object_type",
    ).order_by(OntologySemanticResourceVersion.created_at.desc()).first()
    legacy_id = (version.metadata_json or {}).get("legacy_entity_id") if version else None
    if legacy_id:
        return db.query(Entity).filter(Entity.ontology_id == ontology_id, Entity.id == str(legacy_id)).first()
    return None


def _legacy_relation_for_resource(db: Session, ontology_id: str, resource_id: str) -> Relation | None:
    for row in db.query(Relation).filter(Relation.ontology_id == ontology_id).all():
        if str((row.properties or {}).get("semantic_resource_id") or "") == str(resource_id):
            return row
    version = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.resource_id == resource_id,
        OntologySemanticResourceVersion.kind == "link_type",
    ).order_by(OntologySemanticResourceVersion.created_at.desc()).first()
    legacy_id = (version.constraints_json or {}).get("legacy_relation_id") if version else None
    return db.query(Relation).filter(Relation.ontology_id == ontology_id, Relation.id == str(legacy_id)).first() if legacy_id else None


def _legacy_property_parent(db: Session, ontology_id: str, resource_id: str) -> tuple[Entity | None, dict[str, Any] | None]:
    version = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.resource_id == resource_id,
        OntologySemanticResourceVersion.kind == "property",
    ).order_by(OntologySemanticResourceVersion.created_at.desc()).first()
    if not version or not version.parent_resource_id:
        return None, None
    entity = _legacy_entity_for_resource(db, ontology_id, version.parent_resource_id)
    if not entity:
        return None, None
    prop_id = str((version.provenance_json or {}).get("legacy_property_id") or version.api_name)
    definition = next((item for item in _property_definitions(entity) if str(item.get("id") or item.get("name")) == prop_id), None)
    return entity, definition


def _compatible_entity_id(db: Session, ontology_id: str, resource_id: str) -> str | None:
    return str(entity.id) if (entity := _legacy_entity_for_resource(db, ontology_id, resource_id)) else None


def _materialize_legacy_compatibility(
    db: Session,
    ontology_id: str,
    kind: str,
    operation: str,
    resource_id: str,
    payload: dict[str, Any],
    version: OntologySemanticResourceVersion | None,
) -> None:
    """Update the legacy read projection for one canonical operation.

    The semantic rows remain authoritative.  This projection is kept in the
    same transaction so existing graph/inspector endpoints see the exact
    revision that was just published, without a second independent write API.
    """
    if kind == "object_type":
        entity = _legacy_entity_for_resource(db, ontology_id, resource_id)
        if operation == "delete":
            if entity:
                db.delete(entity)
            return
        if not entity:
            api_name = str((version.api_name if version else payload.get("api_name")) or resource_id)
            candidate = _safe_api_name(str(payload.get("legacy_id") or api_name), "Entity")
            if db.query(Entity).filter(Entity.ontology_id == ontology_id, Entity.id == candidate).first():
                candidate = resource_id
            entity = Entity(
                id=candidate,
                ontology_id=ontology_id,
                name_cn=str(payload.get("name_cn") or payload.get("display_name") or api_name),
                name_en=payload.get("name_en"),
                canonical_id=api_name,
                type=str(payload.get("type") or "Entity"),
                description=payload.get("description"),
                properties={"property_definitions": copy.deepcopy(payload.get("properties") or payload.get("property_definitions") or [])},
                confidence=float(payload.get("confidence") or 1.0),
                semantic_resource_id=resource_id,
            )
            db.add(entity)
        else:
            if payload.get("name_cn") is not None:
                entity.name_cn = payload.get("name_cn")
            if payload.get("name_en") is not None:
                entity.name_en = payload.get("name_en")
            if payload.get("description") is not None:
                entity.description = payload.get("description")
            if payload.get("type") is not None:
                entity.type = payload.get("type")
            entity.semantic_resource_id = resource_id
        db.flush()
        return

    if kind == "property":
        parent_resource_id = (version.parent_resource_id if version else payload.get("parent_resource_id") or payload.get("parent_id"))
        entity = _legacy_entity_for_resource(db, ontology_id, str(parent_resource_id or "")) if parent_resource_id else None
        if not entity:
            raise SemanticCoreError("COMPATIBILITY_PROJECTION_FAILED", "属性未找到所属的 Object Type")
        definitions = _property_definitions(entity)
        prop_id = str((version.provenance_json or {}).get("legacy_property_id") if version else payload.get("legacy_id") or (version.api_name if version else payload.get("api_name")) or resource_id)
        existing = next((item for item in definitions if str(item.get("id") or item.get("name")) == prop_id), None)
        if operation == "delete":
            entity.properties = {**(entity.properties if isinstance(entity.properties, dict) else {}), "property_definitions": [item for item in definitions if item is not existing]}
            db.flush()
            return
        item = existing or {"id": prop_id, "name": prop_id}
        item.update({
            "name": payload.get("name") or payload.get("display_name") or (version.display_name if version else item.get("name") or prop_id),
            "type": str(payload.get("base_type") or payload.get("type") or (version.base_type if version else item.get("type") or "string")),
            "source_field": payload.get("source_field") if payload.get("source_field") is not None else (version.source_field if version else item.get("source_field")),
            "unit": payload.get("unit") if payload.get("unit") is not None else (version.unit if version else item.get("unit")),
            "is_identifier": bool(payload.get("is_identifier", version.is_identifier if version else item.get("is_identifier", False))),
            "is_array": bool(payload.get("is_array", version.is_array if version else item.get("is_array", False))),
        })
        if not existing:
            definitions.append(item)
        entity.properties = {**(entity.properties if isinstance(entity.properties, dict) else {}), "property_definitions": definitions}
        db.flush()
        return

    if kind == "link_type":
        relation = _legacy_relation_for_resource(db, ontology_id, resource_id)
        if operation == "delete":
            if relation:
                db.delete(relation)
            return
        source_id = str((version.source_resource_id if version else payload.get("source_resource_id") or payload.get("source_id")) or "")
        target_id = str((version.target_resource_id if version else payload.get("target_resource_id") or payload.get("target_id")) or "")
        source_entity = _compatible_entity_id(db, ontology_id, source_id)
        target_entity = _compatible_entity_id(db, ontology_id, target_id)
        if not source_entity or not target_entity:
            # Interfaces can be canonical-only and therefore have no legacy
            # edge.  Keep the semantic link authoritative in that case.
            return
        relation_props = {
            "api_name": version.api_name if version else payload.get("api_name"),
            "name": payload.get("display_name") or payload.get("name") or (version.display_name if version else None) or (version.api_name if version else "关联"),
            "description": payload.get("description") if payload.get("description") is not None else (version.description if version else None),
            "cardinality": version.cardinality if version else payload.get("cardinality"),
            "source_name": version.source_name if version else payload.get("source_name") or payload.get("from_name"),
            "target_name": version.target_name if version else payload.get("target_name") or payload.get("to_name"),
            "direction": version.direction if version else payload.get("direction") or "directed",
            "semantic_resource_id": resource_id,
        }
        if relation:
            relation.source_entity = source_entity
            relation.target_entity = target_entity
            relation.type = str(version.api_name if version else payload.get("api_name") or relation.type or "RELATED")
            relation.properties = {**(relation.properties or {}), **relation_props}
        else:
            db.add(Relation(
                id=str(payload.get("legacy_id") or resource_id),
                ontology_id=ontology_id,
                source_entity=source_entity,
                target_entity=target_entity,
                type=str(version.api_name if version else payload.get("api_name") or "RELATED"),
                properties=relation_props,
                confidence=float(payload.get("confidence") or 1.0),
            ))
        db.flush()
        return

    if kind == "logic_rule":
        rule = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id, LogicRule.id == resource_id).first()
        if not rule and version:
            legacy_rule_id = str((version.constraints_json or {}).get("legacy_rule_id") or "")
            if legacy_rule_id:
                rule = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id, LogicRule.id == legacy_rule_id).first()
        if operation == "delete":
            if rule:
                db.delete(rule)
            return
        if not rule:
            rule = LogicRule(id=str(payload.get("legacy_id") or resource_id), ontology_id=ontology_id, name_cn=str(payload.get("name_cn") or payload.get("display_name") or (version.api_name if version else "逻辑规则")))
            db.add(rule)
        fallback_name = (version.display_name or version.api_name) if version else (rule.name_cn or "逻辑规则")
        rule.name_cn = str(payload.get("name_cn") or payload.get("display_name") or fallback_name)
        rule.name_en = payload.get("name_en") if payload.get("name_en") is not None else (version.name_en if version else rule.name_en)
        rule.description = payload.get("description") if payload.get("description") is not None else (version.description if version else rule.description)
        constraints = (version.constraints_json if version else {}) or {}
        rule.condition_json = payload.get("condition") or payload.get("conditions") or constraints.get("condition") or rule.condition_json or {}
        rule.effect_json = payload.get("effect") or constraints.get("effect") or rule.effect_json or {}
        linked_resource_ids = list(payload.get("linked_entities") or constraints.get("linked_entities") or rule.linked_entities or [])
        rule.linked_entities = [
            _legacy_entity_for_resource(db, ontology_id, str(item)).id
            if _legacy_entity_for_resource(db, ontology_id, str(item))
            else str(item)
            for item in linked_resource_ids
        ]
        rule.enabled = bool(payload.get("enabled", constraints.get("enabled", rule.enabled if rule.enabled is not None else True)))
        db.flush()


def apply_semantic_change(db: Session, ontology_id: str, body: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
    """Apply a typed semantic change while retaining legacy editor support."""
    raw_kind = str(body.get("resource_kind") or body.get("target_kind") or body.get("kind") or "").strip().lower()
    aliases = {"entity_type": "object_type", "entity": "object_type", "relationship": "link_type", "relation": "link_type", "rule": "logic_rule", "attribute": "property"}
    kind = aliases.get(raw_kind, raw_kind)
    operation = str(body.get("operation") or "").lower()
    if operation not in {"add", "update", "delete"}:
        raise SemanticCoreError("INVALID_OPERATION", "操作必须是 add、update 或 delete")
    # The old editor uses target_kind=entity_type/relationship/attribute/rule.
    # Those calls remain a compatibility facade.  A canonical resource_kind
    # (or an explicitly canonical target_kind) is handled below so it is
    # validated and written from the semantic plane first.
    legacy_request = raw_kind in {"entity_type", "entity", "relationship", "relation", "attribute", "rule"} and not body.get("resource_kind")
    if legacy_request:
        from app.services.v2.dynamic_ontology_service import apply_change
        legacy_kind = {"object_type": "entity_type", "link_type": "relationship"}.get(kind, kind)
        translated = {**body, "target_kind": legacy_kind}
        try:
            result = apply_change(db, ontology_id, translated, user_id=user_id)
            revision_id = result.get("revision", {}).get("id")
            if revision_id:
                ensure_semantic_metadata(db, ontology_id, revision_id, commit=True)
            result["semantic_schema"] = semantic_schema(db, ontology_id, revision_id)
            return result
        except Exception as exc:
            if isinstance(exc, SemanticCoreError):
                raise
            raise

    current = _current_revision(db, ontology_id)
    expected = body.get("base_revision_id")
    if expected and str(expected) != str(current.id):
        raise SemanticCoreError("REVISION_CONFLICT", "页面基于旧修订，请刷新后重试", details={"expected": expected, "actual": current.id})
    ensure_semantic_metadata(db, ontology_id, current.id)
    target_id = str(body.get("resource_id") or body.get("target_id") or "")
    target = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.revision_id == current.id,
        OntologySemanticResourceVersion.resource_id == target_id,
    ).first() if target_id else None
    payload = copy.deepcopy(body.get("payload") or body.get("resource") or {})
    before = _resource_payload(target) if target else {}
    if operation == "add":
        validated = _validate_canonical_payload(db, ontology_id, current.id, kind, payload)
        target_id = str(uuid.uuid4())
        resource = OntologySemanticResource(id=target_id, ontology_id=ontology_id, kind=kind, api_name=validated["api_name"], created_in_revision_id=None)
        db.add(resource)
    elif not target:
        raise SemanticCoreError("NOT_FOUND", "规范资源不存在")
    elif operation == "update":
        if target.kind != kind:
            raise SemanticCoreError("INVALID_RESOURCE_KIND", "目标规范资源类型与请求不一致")
        merged_payload = _resource_payload(target)
        merged_payload.update(payload)
        _validate_canonical_payload(
            db,
            ontology_id,
            current.id,
            kind,
            merged_payload,
            ignore_resource_id=target.resource_id,
        )
        if kind in {"value_type", "value_type_version"} and any(key in payload for key in ("base_type", "type", "constraints")):
            raise SemanticCoreError("VALUE_TYPE_IMMUTABLE", "Value Type 基础类型和约束需创建新版本")
    if operation == "delete":
        if target.kind != kind:
            raise SemanticCoreError("INVALID_RESOURCE_KIND", "目标规范资源类型与请求不一致")
        blockers = _canonical_delete_blockers(db, ontology_id, current.id, kind, target)
        if blockers:
            raise SemanticCoreError("CHANGE_BLOCKED", "删除被依赖引用阻断", details={"blockers": blockers})
        target_id = target.resource_id
    new_revision = create_revision(db, ontology_id, parent_revision_id=current.id, summary={"semantic_change": kind, "operation": operation}, commit=False)
    copied = _copy_semantic_versions(db, current.id, new_revision.id)
    _copy_source_mappings(db, current.id, new_revision.id, copied)
    by_id = {row.resource_id: row for row in copied}
    source_mapping_id_to_delete: str | None = None
    if operation == "add":
        canonical_constraints = copy.deepcopy(payload.get("constraints") or {})
        if kind == "logic_rule":
            canonical_constraints.update({
                key: copy.deepcopy(payload[key])
                for key in ("condition", "conditions", "effect", "linked_entities", "enabled")
                if key in payload
            })
        values = {
            "resource_id": target_id,
            "revision_id": new_revision.id,
            "ontology_id": ontology_id,
            "kind": kind,
            "api_name": validated["api_name"],
            "display_name": payload.get("display_name") or payload.get("name"),
            "name_cn": payload.get("name_cn"), "name_en": payload.get("name_en"),
            "description": payload.get("description"),
            "parent_resource_id": payload.get("parent_resource_id") or payload.get("parent_id"),
            "source_resource_id": payload.get("source_resource_id") or payload.get("source_id"),
            "target_resource_id": payload.get("target_resource_id") or payload.get("target_id"),
            "source_name": payload.get("source_name") or payload.get("from_name"),
            "target_name": payload.get("target_name") or payload.get("to_name"),
            "direction": payload.get("direction") or "directed",
            "interface_resource_id": payload.get("interface_resource_id"),
            "value_type_resource_id": payload.get("value_type_resource_id"),
            "struct_resource_id": payload.get("struct_resource_id"),
            "base_type": validated.get("base_type"), "cardinality": validated.get("cardinality"),
            "unit": payload.get("unit"), "source_field": payload.get("source_field"),
            "is_identifier": bool(payload.get("is_identifier", False)), "is_required": bool(payload.get("is_required", False)), "is_array": bool(payload.get("is_array", False)),
            "constraints_json": canonical_constraints,
            "provenance_json": payload.get("provenance") or {},
            "metadata_json": {
                **(payload.get("metadata") or {}),
                **({"fields": copy.deepcopy(payload.get("fields") or [])} if kind == "struct" and "fields" in payload else {}),
            },
        }
        if kind == "source_mapping":
            mapped_source_id = payload.get("mapped_resource_id") or (payload.get("metadata") or {}).get("mapped_resource_id") or payload.get("resource_id")
            mapping_status = str(payload.get("mapping_status") or (payload.get("metadata") or {}).get("mapping_status") or "candidate")
            source_mapping_row_id = str(uuid.uuid4())
            values["metadata_json"] = {
                **(values["metadata_json"] or {}),
                "mapped_resource_id": mapped_source_id,
                "mapping_status": mapping_status,
                "source_mapping_row_id": source_mapping_row_id,
                "source": {
                    "dataset_id": payload.get("source_dataset_id"),
                    "version_id": payload.get("source_version_id"),
                    "table": payload.get("source_table"),
                    "field": payload.get("source_field"),
                    "mapping_kind": str(payload.get("mapping_kind") or "field"),
                    "evidence": copy.deepcopy(payload.get("evidence") or {}),
                },
            }
            db.add(OntologySourceMapping(
                id=source_mapping_row_id,
                ontology_id=ontology_id,
                revision_id=new_revision.id,
                resource_id=str(mapped_source_id),
                source_dataset_id=payload.get("source_dataset_id"),
                source_version_id=payload.get("source_version_id"),
                source_table=payload.get("source_table"),
                source_field=payload.get("source_field"),
                mapping_kind=str(payload.get("mapping_kind") or "field"),
                mapping_status=mapping_status,
                evidence_json=payload.get("evidence") or {},
            ))
        row = OntologySemanticResourceVersion(id=str(uuid.uuid4()), **values)
        db.add(row)
        resource.created_in_revision_id = new_revision.id
        after = _resource_payload(row)
    elif operation == "update":
        row = by_id.get(target.resource_id)
        if not row:
            raise SemanticCoreError("NOT_FOUND", "新修订中找不到规范资源")
        if payload.get("api_name") and payload["api_name"] != row.api_name:
            raise SemanticCoreError("API_NAME_MIGRATION_REQUIRED", "API name 变更必须通过 schema migration")
        for field in ("display_name", "name_cn", "name_en", "description", "unit", "source_field", "is_required", "is_array", "direction", "cardinality", "source_name", "target_name", "metadata_json", "provenance_json"):
            if field in payload:
                setattr(row, field, payload[field])
        if kind == "property" and any(key in payload for key in ("base_type", "type")):
            row.base_type = str(payload.get("base_type") or payload.get("type") or row.base_type).lower()
        if "constraints" in payload:
            row.constraints_json = payload["constraints"]
        if kind == "struct" and "fields" in payload:
            row.metadata_json = {**(row.metadata_json or {}), "fields": copy.deepcopy(payload.get("fields") or [])}
        if kind == "source_mapping" and "mapping_status" in payload:
            mapping_status = str(payload.get("mapping_status") or "")
            if mapping_status not in {"candidate", "confirmed", "rejected"}:
                raise SemanticCoreError("INVALID_SOURCE_MAPPING_STATUS", "mapping_status 必须是 candidate、confirmed 或 rejected")
            metadata = copy.deepcopy(row.metadata_json or {})
            mapping_id = str(metadata.get("source_mapping_row_id") or "")
            mapping_row = db.query(OntologySourceMapping).filter(
                OntologySourceMapping.id == mapping_id,
                OntologySourceMapping.revision_id == new_revision.id,
            ).first()
            if not mapping_row:
                raise SemanticCoreError("INVALID_SOURCE_MAPPING", "该映射没有可更新的来源记录")
            mapping_row.mapping_status = mapping_status
            metadata["mapping_status"] = mapping_status
            row.metadata_json = metadata
        if kind in {"value_type", "value_type_version"} and any(key in payload for key in ("base_type", "type", "constraints")):
            raise SemanticCoreError("VALUE_TYPE_IMMUTABLE", "Value Type 基础类型和约束需创建新版本")
        after = _resource_payload(row)
    else:
        row = by_id.get(target.resource_id)
        if row:
            if kind == "source_mapping":
                source_mapping_id_to_delete = str((row.metadata_json or {}).get("source_mapping_row_id") or "")
            db.delete(row)
        resource = db.query(OntologySemanticResource).filter(OntologySemanticResource.id == target.resource_id).first()
        if resource:
            resource.retired_in_revision_id = new_revision.id
        after = {}
    _materialize_legacy_compatibility(
        db,
        ontology_id,
        kind,
        operation,
        target_id,
        payload,
        # A delete still needs the current revision's definition to resolve
        # the legacy parent/relationship/rule for the compatibility
        # projection.  The row is removed only from the new revision above;
        # ``target`` remains the immutable base definition.
        row if operation != "delete" else target,
    )
    if kind == "source_mapping" and operation == "delete":
        db.query(OntologySourceMapping).filter(
            OntologySourceMapping.id == (source_mapping_id_to_delete or ""),
            OntologySourceMapping.revision_id == new_revision.id,
        ).delete(synchronize_session=False)
    db.flush()
    # ``create_revision`` captures the compatibility snapshot before the
    # canonical operation is projected.  Refresh it now so legacy restore and
    # comparison endpoints point at the same published definition.
    compatibility_snapshot = snapshot_ontology(db, ontology_id)
    compatibility_payload = json.dumps(compatibility_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    compatibility_hash = hashlib.sha256(compatibility_payload).hexdigest()
    new_revision.snapshot_json = compatibility_snapshot
    new_revision.snapshot_hash = compatibility_hash
    new_revision.snapshot_uri = get_storage_service().put_bytes(
        "curated-datasets",
        f"ontologies/{ontology_id}/revisions/{new_revision.revision_no}-{compatibility_hash[:12]}.json",
        compatibility_payload,
        content_type="application/json",
    )
    remaining = db.query(OntologySemanticResourceVersion).filter(OntologySemanticResourceVersion.revision_id == new_revision.id).all()
    new_revision.metadata_digest = _digest(remaining)
    new_revision.status = "current"
    db.add(__import__("app.models.v2.dynamic_ontology", fromlist=["OntologyChange"]).OntologyChange(
        id=str(uuid.uuid4()), ontology_id=ontology_id, base_revision_id=current.id, result_revision_id=new_revision.id,
        target_kind=kind, operation=operation, target_id=target_id, before_json=before, after_json=after,
        impact_json={"canonical": True}, validation_json={"ok": True}, status="applied", created_by=user_id,
    ))
    db.commit()
    return {"revision": {"id": new_revision.id, "revision_no": new_revision.revision_no, "is_current": True}, "resource": after, "semantic_schema": semantic_schema(db, ontology_id, new_revision.id)}


def serialize_policy_shape(policy: Any) -> dict[str, Any]:
    return {
        "id": policy.id,
        "ontology_id": policy.ontology_id,
        "revision_id": policy.revision_id,
        "name": policy.name,
        "subject_kind": policy.subject_kind,
        "subject_id": policy.subject_id,
        "effect": policy.effect,
        "scope_kind": policy.scope_kind,
        "scope_id": policy.scope_id,
        "conditions": policy.conditions_json or [],
        "field_allowlist": policy.field_allowlist_json or [],
        "enabled": bool(policy.enabled),
        "priority": policy.priority,
        "created_by": policy.created_by,
        "created_at": policy.created_at.isoformat() if policy.created_at else None,
        "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
        "note": policy.note,
    }


__all__ = [
    "SEMANTIC_KINDS",
    "SemanticCoreError",
    "ensure_semantic_metadata",
    "sync_legacy_to_semantic",
    "semantic_schema",
    "semantic_change_impact",
    "serialize_policy_shape",
]
