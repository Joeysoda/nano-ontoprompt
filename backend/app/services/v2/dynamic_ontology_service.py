"""Transactional, revision-aware ontology editing.

The service is intentionally small and boring: an edit is one typed operation,
validated against the current revision, materialised in the relational schema,
snapshotted, and recorded as an immutable change.  Instances are source data
and are never edited by this module.
"""
from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.action import Action
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.logic import LogicRule
from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.relation import Relation
from app.models.v2.construction import EvidenceRef
from app.models.v2.dynamic_ontology import OntologyChange
from app.services.v2.revision_service import create_revision, snapshot_ontology


ENTITY_KINDS = {"entity_type", "entity"}
PROPERTY_KINDS = {"property", "attribute"}
RELATION_KINDS = {"relationship", "relation"}
RULE_KINDS = {"logic_rule", "rule"}
OPERATIONS = {"add", "update", "delete"}
CARDINALITY_ALIASES = {
    "1:1": "one-to-one",
    "1:N": "one-to-many",
    "N:1": "many-to-one",
    "N:M": "many-to-many",
    "M:N": "many-to-many",
    "0..1:1": "zero-or-one-to-one",
    "0..1:N": "zero-or-one-to-many",
    "0..*:1": "many-to-one",
    "0..*:N": "many-to-many",
}
CARDINALITIES = {
    "one-to-one",
    "one-to-many",
    "many-to-one",
    "many-to-many",
    "zero-or-one-to-one",
    "zero-or-one-to-many",
}
RULE_OPERATORS = {"=", "≠", "!=", ">", "≥", ">=", "<", "≤", "<=", "属于", "in"}
SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,199}$")


class OntologyEditError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _canonical_kind(value: str) -> str:
    value = str(value or "").strip().lower()
    if value in ENTITY_KINDS:
        return "entity_type"
    if value in PROPERTY_KINDS:
        return "property"
    if value in RELATION_KINDS:
        return "relationship"
    if value in RULE_KINDS:
        return "logic_rule"
    raise OntologyEditError("INVALID_TARGET_KIND", f"不支持的修改对象：{value or '空'}")


def normalize_cardinality(value: Any) -> str:
    text = str(value or "").strip()
    normalized = CARDINALITY_ALIASES.get(text, text)
    if normalized not in CARDINALITIES:
        raise OntologyEditError(
            "INVALID_CARDINALITY",
            f"关系基数无效：{text or '空'}。可选 one-to-one、one-to-many、many-to-one、many-to-many",
        )
    return normalized


def _property_definitions(entity: Entity) -> list[dict[str, Any]]:
    raw = entity.properties or {}
    if isinstance(raw, list):
        return [copy.deepcopy(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        values = raw.get("property_definitions") or raw.get("properties")
        if isinstance(values, list):
            return [copy.deepcopy(item) for item in values if isinstance(item, dict)]
        return [
            {"id": str(key), "name": str(key), "label": str(key), "type": "string", "example": value}
            for key, value in raw.items()
            if key not in {"schema_version", "source_fields", "evidence", "color", "icon", "data_class"}
        ]
    return []


def _set_property_definitions(entity: Entity, definitions: list[dict[str, Any]]) -> None:
    raw = entity.properties if isinstance(entity.properties, dict) else {}
    preserved = {
        key: value for key, value in (raw or {}).items()
        if key not in {"property_definitions", "properties"}
    }
    preserved["property_definitions"] = definitions
    entity.properties = preserved


def _entity_payload(entity: Entity) -> dict[str, Any]:
    return {
        "id": entity.id,
        "name_cn": entity.name_cn,
        "name_en": entity.name_en,
        "name_abbr": entity.name_abbr,
        "snomed_id": entity.snomed_id,
        "canonical_id": entity.canonical_id,
        "type": entity.type,
        "description": entity.description,
        "properties": copy.deepcopy(entity.properties or {}),
        "confidence": entity.confidence,
        "version": entity.version,
    }


def _relation_payload(relation: Relation) -> dict[str, Any]:
    return {
        "id": relation.id,
        "source_entity": relation.source_entity,
        "target_entity": relation.target_entity,
        "type": relation.type,
        "properties": copy.deepcopy(relation.properties or {}),
        "confidence": relation.confidence,
    }


def _rule_payload(rule: LogicRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name_cn": rule.name_cn,
        "name_en": rule.name_en,
        "description": rule.description,
        "formula": rule.formula,
        "condition": copy.deepcopy(rule.condition_json or {}),
        "effect": copy.deepcopy(rule.effect_json or {}),
        "evidence": copy.deepcopy(rule.evidence_json or {}),
        "linked_entities": list(rule.linked_entities or []),
        "confidence": rule.confidence,
        "version": rule.version,
        "enabled": bool(rule.enabled),
        "status": rule.status,
    }


def _current_revision(db: Session, ontology_id: str) -> OntologyRevision | None:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise OntologyEditError("NOT_FOUND", "本体不存在")
    if project.current_revision_id:
        return db.query(OntologyRevision).filter(OntologyRevision.id == project.current_revision_id).first()
    return db.query(OntologyRevision).filter(
        OntologyRevision.ontology_id == ontology_id, OntologyRevision.is_current.is_(True)
    ).order_by(OntologyRevision.revision_no.desc()).first()


def _find_entity(db: Session, ontology_id: str, entity_id: str | None) -> Entity:
    row = db.query(Entity).filter(Entity.ontology_id == ontology_id, Entity.id == str(entity_id or "")).first()
    if not row:
        raise OntologyEditError("NOT_FOUND", f"实体类型不存在：{entity_id or '空'}")
    return row


def _find_relation(db: Session, ontology_id: str, relation_id: str | None) -> Relation:
    row = db.query(Relation).filter(Relation.ontology_id == ontology_id, Relation.id == str(relation_id or "")).first()
    if not row:
        raise OntologyEditError("NOT_FOUND", f"关系不存在：{relation_id or '空'}")
    return row


def _find_rule(db: Session, ontology_id: str, rule_id: str | None) -> LogicRule:
    row = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id, LogicRule.id == str(rule_id or "")).first()
    if not row:
        raise OntologyEditError("NOT_FOUND", f"逻辑规则不存在：{rule_id or '空'}")
    return row


def _property_ref(entity_id: str, property_id: str) -> dict[str, str]:
    return {"entity_id": entity_id, "property_id": property_id}


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _validate_rule(db: Session, ontology_id: str, payload: dict[str, Any]) -> None:
    linked = [str(item) for item in (payload.get("linked_entities") or [])]
    entity_ids = {row.id for row in db.query(Entity).filter(Entity.ontology_id == ontology_id).all()}
    for entity_id in linked:
        if entity_id not in entity_ids:
            raise OntologyEditError("INVALID_RULE_REFERENCE", f"规则引用的实体类型不存在：{entity_id}")
    relations = {row.id: row for row in db.query(Relation).filter(Relation.ontology_id == ontology_id).all()}
    def validate_items(value: Any) -> None:
        for item in _walk_dicts(value):
            if "operator" in item and str(item["operator"]) not in RULE_OPERATORS:
                raise OntologyEditError("INVALID_RULE", f"规则比较符不支持：{item['operator']}")
            relation_id = item.get("relation_id") or item.get("relationship_id")
            if relation_id and str(relation_id) not in relations:
                raise OntologyEditError("INVALID_RULE_REFERENCE", f"规则引用的关系不存在：{relation_id}")
            entity_id = item.get("entity_id") or item.get("entity_type_id")
            property_id = item.get("property_id") or item.get("property")
            if entity_id:
                entity = _find_entity(db, ontology_id, str(entity_id))
                if property_id and not any(
                    str(prop.get("id") or prop.get("name")) == str(property_id)
                    for prop in _property_definitions(entity)
                ):
                    raise OntologyEditError("INVALID_RULE_REFERENCE", f"规则引用的属性不存在：{property_id}")

    validate_items(payload.get("condition") or payload.get("conditions") or {})
    validate_items(payload.get("effect") or {})


def validate_schema(db: Session, ontology_id: str) -> dict[str, Any]:
    entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    entity_ids = {row.id for row in entities}
    relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
    invalid_relations = []
    for relation in relations:
        if relation.source_entity not in entity_ids or relation.target_entity not in entity_ids:
            invalid_relations.append({"id": relation.id, "reason": "端点实体类型不存在"})
        try:
            props = relation.properties or {}
            if props.get("cardinality"):
                normalize_cardinality(props["cardinality"])
        except OntologyEditError as exc:
            invalid_relations.append({"id": relation.id, "reason": str(exc)})
    invalid_rules = []
    for rule in db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all():
        try:
            _validate_rule(db, ontology_id, _rule_payload(rule))
        except OntologyEditError as exc:
            invalid_rules.append({"id": rule.id, "reason": str(exc)})
    if invalid_relations or invalid_rules:
        raise OntologyEditError(
            "SCHEMA_INVALID",
            "本体校验失败，请先修复结构引用",
            details={"relations": invalid_relations, "rules": invalid_rules},
        )
    return {"entities": len(entities), "relations": len(relations), "rules": db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).count()}


def impact_for_change(db: Session, ontology_id: str, body: dict[str, Any]) -> dict[str, Any]:
    kind = _canonical_kind(body.get("target_kind") or body.get("kind"))
    operation = str(body.get("operation") or "").lower()
    if operation not in OPERATIONS:
        raise OntologyEditError("INVALID_OPERATION", "操作必须是 add、update 或 delete")
    target_id = str(body.get("target_id") or "")
    payload = body.get("payload") or {}
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if operation != "delete":
        return {"target_kind": kind, "operation": operation, "blockers": [], "warnings": [], "can_apply": True}
    if kind == "entity_type":
        entity = _find_entity(db, ontology_id, target_id)
        count = db.query(EntityInstance).filter(EntityInstance.ontology_id == ontology_id, EntityInstance.entity_id == entity.id).count()
        if count:
            blockers.append({"kind": "instances", "count": count, "message": f"仍有 {count} 个真实实例"})
        relation_count = db.query(Relation).filter(Relation.ontology_id == ontology_id, (Relation.source_entity == entity.id) | (Relation.target_entity == entity.id)).count()
        if relation_count:
            blockers.append({"kind": "relationships", "count": relation_count, "message": f"仍被 {relation_count} 条类型关系引用"})
        rule_count = 0
        for rule in db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all():
            if entity.id in (rule.linked_entities or []) or any(
                entity.id == item.get("entity_id")
                for item in _walk_dicts(rule.condition_json or {})
            ) or any(
                entity.id == item.get("entity_id")
                for item in _walk_dicts(rule.effect_json or {})
            ):
                rule_count += 1
        if rule_count:
            blockers.append({"kind": "rules", "count": rule_count, "message": f"仍被 {rule_count} 条逻辑规则引用"})
        evidence_count = db.query(EvidenceRef).filter(EvidenceRef.ontology_id == ontology_id, EvidenceRef.assertion_id == entity.id).count()
        if evidence_count:
            blockers.append({"kind": "evidence", "count": evidence_count, "message": f"仍有 {evidence_count} 条证据引用"})
    elif kind == "property":
        entity = _find_entity(db, ontology_id, payload.get("entity_id"))
        prop = next((item for item in _property_definitions(entity) if str(item.get("id") or item.get("name")) == target_id), None)
        if not prop:
            raise OntologyEditError("NOT_FOUND", f"属性不存在：{target_id}")
        count = 0
        for row in db.query(EntityInstance).filter(EntityInstance.ontology_id == ontology_id, EntityInstance.entity_id == entity.id).all():
            if isinstance(row.row_data, dict) and target_id in row.row_data and row.row_data.get(target_id) not in (None, ""):
                count += 1
        if count:
            blockers.append({"kind": "instances", "count": count, "message": f"仍有 {count} 个实例包含该字段"})
        for rule in db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all():
            if any(
                item.get("property_id") == target_id and item.get("entity_id") == entity.id
                for item in list(_walk_dicts(rule.condition_json or {})) + list(_walk_dicts(rule.effect_json or {}))
            ):
                blockers.append({"kind": "rule", "id": rule.id, "message": f"逻辑规则 {rule.name_cn} 引用了该属性"})
    elif kind == "relationship":
        relation = _find_relation(db, ontology_id, target_id)
        evidence_count = db.query(EvidenceRef).filter(EvidenceRef.ontology_id == ontology_id, EvidenceRef.assertion_id == relation.id).count()
        if evidence_count:
            blockers.append({"kind": "evidence", "count": evidence_count, "message": f"仍有 {evidence_count} 条证据引用"})
        for rule in db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all():
            if any(item.get("relation_id") == relation.id for item in _walk_dicts(rule.condition_json or {})) or any(item.get("relation_id") == relation.id for item in _walk_dicts(rule.effect_json or {})):
                blockers.append({"kind": "rule", "id": rule.id, "message": f"逻辑规则 {rule.name_cn} 引用了该关系"})
    elif kind == "logic_rule":
        rule = _find_rule(db, ontology_id, target_id)
        action_count = db.query(Action).filter(Action.ontology_id == ontology_id).all()
        action_count = sum(1 for action in action_count if rule.id in (action.linked_logic_ids or []))
        if action_count:
            blockers.append({"kind": "action", "count": action_count, "message": f"仍被 {action_count} 个后台动作引用"})
        evidence_count = db.query(EvidenceRef).filter(EvidenceRef.ontology_id == ontology_id, EvidenceRef.assertion_id == rule.id).count()
        if evidence_count:
            blockers.append({"kind": "evidence", "count": evidence_count, "message": f"仍有 {evidence_count} 条证据引用"})
    return {"target_kind": kind, "operation": operation, "target_id": target_id, "blockers": blockers, "warnings": warnings, "can_apply": not blockers}


def _ensure_safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not SAFE_ID.match(text):
        raise OntologyEditError("INVALID_ID", f"{label}必须以字母开头，且只能包含字母、数字、_、.、:、-")
    return text


def _human_formula(condition: Any, effect: Any) -> str:
    conditions = condition.get("all") if isinstance(condition, dict) else condition
    conditions = conditions if isinstance(conditions, list) else [condition]
    bits = []
    for item in conditions:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "relationship" or item.get("relation"):
            bits.append(str(item.get("label") or item.get("relation") or item.get("predicate") or "关系成立"))
        elif item.get("property") or item.get("property_id"):
            bits.append(f"{item.get('property') or item.get('property_id')} {item.get('operator', '=')} {item.get('value', '')}")
        else:
            bits.append(str(item.get("label") or item.get("entity_type") or "条件成立"))
    effect_label = effect.get("label") if isinstance(effect, dict) else None
    effect_name = (
        effect_label
        or (effect.get("predicate") if isinstance(effect, dict) else None)
        or "产生结论"
    )
    return f"如果 {' 且 '.join(bits) or '条件成立'}，那么 {effect_name}。"


def _apply_one(db: Session, ontology_id: str, kind: str, operation: str, target_id: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if kind == "entity_type":
        if operation == "add":
            entity_id = _ensure_safe_id(payload.get("canonical_id") or payload.get("id") or f"entity_{uuid.uuid4().hex[:10]}", "实体类型标识")
            if db.query(Entity).filter(Entity.ontology_id == ontology_id, (Entity.id == entity_id) | (Entity.canonical_id == entity_id)).first():
                raise OntologyEditError("DUPLICATE_ID", f"实体类型标识已存在：{entity_id}")
            entity = Entity(id=entity_id, ontology_id=ontology_id, name_cn=str(payload.get("name_cn") or payload.get("name") or entity_id), name_en=payload.get("name_en"), canonical_id=entity_id, type=payload.get("type") or "Entity", description=payload.get("description"), properties={"property_definitions": payload.get("properties") or payload.get("property_definitions") or []}, confidence=float(payload.get("confidence") or 1.0))
            db.add(entity); db.flush()
            return entity.id, {}, _entity_payload(entity)
        entity = _find_entity(db, ontology_id, target_id)
        before = _entity_payload(entity)
        if operation == "delete":
            db.delete(entity); db.flush(); return entity.id, before, {}
        for field in ("name_cn", "name_en", "name_abbr", "snomed_id", "description", "type", "confidence"):
            if field in payload and field != "canonical_id":
                setattr(entity, field, payload[field])
        if "properties" in payload or "property_definitions" in payload:
            incoming = payload.get("properties") or payload.get("property_definitions") or []
            if not isinstance(incoming, list):
                raise OntologyEditError(
                    "PROPERTY_OPERATION_REQUIRED",
                    "属性新增或删除请使用单独的属性操作",
                )
            current = {str(item.get("id") or item.get("name")): item for item in _property_definitions(entity)}
            incoming_keys = {
                str(item.get("id") or item.get("name"))
                for item in incoming
                if isinstance(item, dict) and (item.get("id") or item.get("name"))
            }
            if set(current) != incoming_keys:
                raise OntologyEditError(
                    "PROPERTY_OPERATION_REQUIRED",
                    "属性新增或删除请使用单独的属性操作",
                )
            for item in incoming:
                key = str(item.get("id") or item.get("name") or "")
                if key and key in current and str(item.get("source_field") or current[key].get("source_field") or "") != str(current[key].get("source_field") or ""):
                    raise OntologyEditError("IMMUTABLE_KEY", f"属性 {key} 的来源字段不可改名")
            _set_property_definitions(entity, incoming)
        db.flush(); return entity.id, before, _entity_payload(entity)

    if kind == "property":
        entity = _find_entity(db, ontology_id, payload.get("entity_id"))
        definitions = _property_definitions(entity)
        prop_id = target_id or str(payload.get("property_id") or payload.get("property", {}).get("id") or "")
        if operation == "add":
            item = copy.deepcopy(payload.get("property") or payload)
            for key in ("entity_id", "property_id", "target_id"):
                item.pop(key, None)
            prop_id = _ensure_safe_id(item.get("id") or item.get("name"), "属性标识")
            if any(str(row.get("id") or row.get("name")) == prop_id for row in definitions):
                raise OntologyEditError("DUPLICATE_ID", f"属性标识已存在：{prop_id}")
            item["id"] = prop_id; item.setdefault("name", prop_id); item.setdefault("type", "string")
            definitions.append(item); _set_property_definitions(entity, definitions); db.flush()
            return prop_id, {}, item
        match = next((row for row in definitions if str(row.get("id") or row.get("name")) == prop_id), None)
        if not match:
            raise OntologyEditError("NOT_FOUND", f"属性不存在：{prop_id}")
        before = copy.deepcopy(match)
        if operation == "delete":
            definitions = [row for row in definitions if row is not match]
        else:
            if "source_field" in payload and payload.get("source_field") != match.get("source_field"):
                raise OntologyEditError("IMMUTABLE_KEY", "来源字段不可修改")
            editable_fields = ("label", "name_cn", "name_en", "description", "type", "unit", "enum", "is_identifier", "confidence")
            # Legacy property definitions without an explicit ``id`` used
            # ``name`` as their technical key.  Keep that key immutable while
            # still allowing display labels to change.
            if match.get("id"):
                editable_fields = editable_fields + ("name",)
            for field in editable_fields:
                if field in payload: match[field] = payload[field]
        _set_property_definitions(entity, definitions); db.flush()
        return prop_id, before, {} if operation == "delete" else copy.deepcopy(match)

    if kind == "relationship":
        if operation == "add":
            source = _find_entity(db, ontology_id, payload.get("source_entity") or payload.get("source"))
            target = _find_entity(db, ontology_id, payload.get("target_entity") or payload.get("target"))
            props = copy.deepcopy(payload.get("properties") or {})
            if payload.get("cardinality"):
                props["cardinality"] = normalize_cardinality(payload["cardinality"])
            relation = Relation(id=str(uuid.uuid4()), ontology_id=ontology_id, source_entity=source.id, target_entity=target.id, type=str(payload.get("type") or payload.get("name") or "关联"), properties=props, confidence=float(payload.get("confidence") or 1.0))
            db.add(relation); db.flush(); return relation.id, {}, _relation_payload(relation)
        relation = _find_relation(db, ontology_id, target_id); before = _relation_payload(relation)
        if operation == "delete":
            db.delete(relation); db.flush(); return target_id, before, {}
        for field in ("type", "confidence"):
            if field in payload: setattr(relation, field, payload[field])
        if payload.get("source_entity"):
            relation.source_entity = _find_entity(db, ontology_id, payload["source_entity"]).id
        if payload.get("target_entity"):
            relation.target_entity = _find_entity(db, ontology_id, payload["target_entity"]).id
        props = copy.deepcopy(relation.properties or {}); props.update(payload.get("properties") or {})
        if payload.get("cardinality") or props.get("cardinality"):
            props["cardinality"] = normalize_cardinality(payload.get("cardinality") or props.get("cardinality"))
        relation.properties = props; db.flush(); return target_id, before, _relation_payload(relation)

    if kind == "logic_rule":
        if operation == "add":
            condition = copy.deepcopy(payload.get("condition") or payload.get("conditions") or ({"all": [{"kind": "relationship", "predicate": "LEGACY_RULE", "source": "?source", "target": "?target"}]} if payload.get("formula") else {"all": []}))
            effect = copy.deepcopy(payload.get("effect") or {"kind": "relationship", "predicate": "LEGACY_CONCLUSION", "source": "?source", "target": "?target"})
            rule = LogicRule(id=str(uuid.uuid4()), ontology_id=ontology_id, name_cn=str(payload.get("name_cn") or payload.get("name") or "未命名规则"), name_en=payload.get("name_en"), description=payload.get("description"), condition_json=condition, effect_json=effect, formula=str(payload.get("formula") or _human_formula(condition, effect)), evidence_json=copy.deepcopy(payload.get("evidence") or {}), confidence=float(payload.get("confidence") or 1.0), enabled=bool(payload.get("enabled", True)), status=str(payload.get("status") or "draft"))
            rule.linked_entities = list(payload.get("linked_entities") or [])
            _validate_rule(db, ontology_id, _rule_payload(rule)); db.add(rule); db.flush(); return rule.id, {}, _rule_payload(rule)
        rule = _find_rule(db, ontology_id, target_id); before = _rule_payload(rule)
        if operation == "delete":
            db.delete(rule); db.flush(); return target_id, before, {}
        condition = copy.deepcopy(payload.get("condition") or payload.get("conditions") or rule.condition_json or {})
        effect = copy.deepcopy(payload.get("effect") or rule.effect_json or {})
        for field in ("name_cn", "name_en", "description", "confidence", "enabled", "status"):
            if field in payload: setattr(rule, field, payload[field])
        rule.condition_json = condition; rule.effect_json = effect; rule.formula = str(payload.get("formula") or _human_formula(condition, effect))
        if "linked_entities" in payload: rule.linked_entities = list(payload.get("linked_entities") or [])
        _validate_rule(db, ontology_id, _rule_payload(rule)); db.flush(); return target_id, before, _rule_payload(rule)
    raise OntologyEditError("INVALID_TARGET_KIND", kind)


def apply_change(db: Session, ontology_id: str, body: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
    kind = _canonical_kind(body.get("target_kind") or body.get("kind"))
    operation = str(body.get("operation") or "").lower()
    if operation not in OPERATIONS:
        raise OntologyEditError("INVALID_OPERATION", "操作必须是 add、update 或 delete")
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).with_for_update().first()
    if not project:
        raise OntologyEditError("NOT_FOUND", "本体不存在")
    current = _current_revision(db, ontology_id)
    expected = body.get("base_revision_id")
    actual = current.id if current else None
    if expected is not None and str(expected) != str(actual):
        raise OntologyEditError("REVISION_CONFLICT", "页面基于旧修订，请刷新本体后重试", details={"expected": expected, "actual": actual})
    impact = impact_for_change(db, ontology_id, body)
    if operation == "delete" and not impact.get("can_apply"):
        raise OntologyEditError("CHANGE_BLOCKED", "删除被依赖引用阻断", details=impact)
    target_id, before, after = _apply_one(db, ontology_id, kind, operation, str(body.get("target_id") or ""), copy.deepcopy(body.get("payload") or {}))
    try:
        validation = validate_schema(db, ontology_id)
        next_revision_id = str(uuid.uuid4())
        revision = create_revision(db, ontology_id, parent_revision_id=actual, summary={"change_kind": kind, "operation": operation, "target_id": target_id}, commit=False, revision_id=next_revision_id)
        change = OntologyChange(id=str(uuid.uuid4()), ontology_id=ontology_id, base_revision_id=actual, result_revision_id=revision.id, target_kind=kind, operation=operation, target_id=target_id, before_json=before, after_json=after, impact_json=impact, validation_json={"ok": True, **validation}, status="applied", note=body.get("note"), created_by=user_id)
        db.add(change)
        db.commit(); db.refresh(revision); db.refresh(change)
    except Exception:
        db.rollback()
        raise
    audit = None
    try:
        from app.services.v2.audit_runner import queue_local_audit
        audit = queue_local_audit(db, ontology_id=ontology_id, revision_id=revision.id, construction_run_id=None)
    except Exception:
        audit = None
    return {"change": serialize_change(change), "revision": {"id": revision.id, "revision_no": revision.revision_no, "is_current": True}, "audit_task_id": getattr(audit, "id", None)}


def _normalize_batch_operations(operations: Any) -> list[dict[str, Any]]:
    """Normalize and cheaply validate a batch before touching the database."""
    if not isinstance(operations, list) or not operations:
        raise OntologyEditError("BATCH_EMPTY", "请至少选择一条模型建议")
    if len(operations) > 50:
        raise OntologyEditError("BATCH_TOO_LARGE", "一次最多导入 50 条模型建议")
    normalized: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, str]] = set()
    for index, item in enumerate(operations):
        if not isinstance(item, dict):
            raise OntologyEditError("BATCH_INVALID", f"第 {index + 1} 条建议不是对象")
        kind = _canonical_kind(item.get("target_kind") or item.get("kind"))
        operation = str(item.get("operation") or "").lower()
        if operation not in OPERATIONS:
            raise OntologyEditError("INVALID_OPERATION", f"第 {index + 1} 条操作必须是 add、update 或 delete")
        target_id = str(item.get("target_id") or "")
        payload = copy.deepcopy(item.get("payload") or {})
        if not isinstance(payload, dict):
            raise OntologyEditError("BATCH_INVALID", f"第 {index + 1} 条建议的 payload 必须是对象")
        # The same target cannot be changed twice in a single batch.  This
        # prevents an order-dependent result when a model emits duplicates.
        if operation != "add":
            key = (kind, target_id)
            if not target_id:
                raise OntologyEditError("BATCH_INVALID", f"第 {index + 1} 条 {kind} 操作缺少 target_id")
            if key in seen_targets:
                raise OntologyEditError("BATCH_CONFLICT", f"第 {index + 1} 条与前面的同一对象操作重复：{target_id}")
            seen_targets.add(key)
        normalized.append({"target_kind": kind, "operation": operation, "target_id": target_id, "payload": payload, "source_index": index})
    # Detect duplicate additions before the dry-run reaches the database.  A
    # property is scoped to its entity type; entity IDs are ontology-scoped.
    entity_adds: set[str] = set()
    property_adds: set[tuple[str, str]] = set()
    for index, item in enumerate(normalized):
        if item["operation"] != "add":
            continue
        payload = item["payload"]
        if item["target_kind"] == "entity_type":
            candidate = str(payload.get("canonical_id") or payload.get("id") or "")
            if candidate and candidate in entity_adds:
                raise OntologyEditError("BATCH_CONFLICT", f"批量建议重复新增实体类型：{candidate}")
            if candidate:
                entity_adds.add(candidate)
        elif item["target_kind"] == "property":
            entity_id = str(payload.get("entity_id") or "")
            prop = payload.get("property") if isinstance(payload.get("property"), dict) else payload
            candidate = str((prop or {}).get("id") or (prop or {}).get("name") or "")
            key = (entity_id, candidate)
            if candidate and key in property_adds:
                raise OntologyEditError("BATCH_CONFLICT", f"批量建议重复新增属性：{entity_id}.{candidate}")
            if candidate:
                property_adds.add(key)
    return normalized


def _batch_dry_run(db: Session, ontology_id: str, operations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply a batch inside a savepoint and roll it back after validation."""
    savepoint = db.begin_nested()
    results: list[dict[str, Any]] = []
    try:
        for index, item in enumerate(operations):
            impact = impact_for_change(db, ontology_id, item)
            if item["operation"] == "delete" and not impact.get("can_apply"):
                raise OntologyEditError(
                    "CHANGE_BLOCKED",
                    f"第 {index + 1} 条删除被依赖引用阻断",
                    details={"index": index, **impact},
                )
            target_id, before, after = _apply_one(
                db,
                ontology_id,
                item["target_kind"],
                item["operation"],
                item["target_id"],
                item["payload"],
            )
            results.append({
                "index": index,
                "target_kind": item["target_kind"],
                "operation": item["operation"],
                "target_id": target_id,
                "before": before,
                "after": after,
                "impact": impact,
            })
        validation = validate_schema(db, ontology_id)
    except Exception:
        savepoint.rollback()
        raise
    savepoint.rollback()
    return results, validation


def validate_batch_changes(db: Session, ontology_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Validate all selected suggestions together without persisting changes."""
    current = _current_revision(db, ontology_id)
    expected = body.get("base_revision_id")
    actual = current.id if current else None
    if expected is not None and str(expected) != str(actual):
        raise OntologyEditError(
            "REVISION_CONFLICT",
            "页面基于旧修订，请刷新本体后重试",
            details={"expected": expected, "actual": actual},
        )
    operations = _normalize_batch_operations(body.get("operations"))
    try:
        results, validation = _batch_dry_run(db, ontology_id, operations)
    except OntologyEditError:
        raise
    except Exception as exc:
        raise OntologyEditError("BATCH_INVALID", f"批量校验失败：{str(exc)[:500]}") from exc
    return {
        "ontology_id": ontology_id,
        "base_revision_id": actual,
        "can_apply": True,
        "operations": results,
        "validation": {"ok": True, **validation},
        "message": f"已通过 {len(results)} 条建议的整体校验，可批量导入",
    }


def apply_batch_changes(db: Session, ontology_id: str, body: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
    """Atomically apply selected suggestions as one immutable revision."""
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).with_for_update().first()
    if not project:
        raise OntologyEditError("NOT_FOUND", "本体不存在")
    current = _current_revision(db, ontology_id)
    expected = body.get("base_revision_id")
    actual = current.id if current else None
    if expected is not None and str(expected) != str(actual):
        raise OntologyEditError(
            "REVISION_CONFLICT",
            "页面基于旧修订，请刷新本体后重试",
            details={"expected": expected, "actual": actual},
        )
    operations = _normalize_batch_operations(body.get("operations"))
    before = snapshot_ontology(db, ontology_id)
    try:
        # Run the same savepoint validation immediately before the write.  The
        # project lock prevents a concurrent editor from changing the base
        # revision between the check and the actual transaction.
        _batch_dry_run(db, ontology_id, operations)
        applied: list[dict[str, Any]] = []
        for index, item in enumerate(operations):
            impact = impact_for_change(db, ontology_id, item)
            if item["operation"] == "delete" and not impact.get("can_apply"):
                raise OntologyEditError("CHANGE_BLOCKED", f"第 {index + 1} 条删除被依赖引用阻断", details={"index": index, **impact})
            target_id, item_before, item_after = _apply_one(
                db,
                ontology_id,
                item["target_kind"],
                item["operation"],
                item["target_id"],
                item["payload"],
            )
            applied.append({
                "index": index,
                "target_kind": item["target_kind"],
                "operation": item["operation"],
                "target_id": target_id,
                "before": item_before,
                "after": item_after,
                "impact": impact,
            })
        validation = validate_schema(db, ontology_id)
        after = snapshot_ontology(db, ontology_id)
        revision = create_revision(
            db,
            ontology_id,
            parent_revision_id=actual,
            summary={"change_kind": "batch", "operation_count": len(applied)},
            commit=False,
        )
        change = OntologyChange(
            id=str(uuid.uuid4()),
            ontology_id=ontology_id,
            base_revision_id=actual,
            result_revision_id=revision.id,
            target_kind="batch",
            operation="apply",
            target_id=str(uuid.uuid4()),
            before_json=before,
            after_json=after,
            impact_json={"operations": applied, "operation_count": len(applied)},
            validation_json={"ok": True, **validation},
            status="applied",
            note=body.get("note") or "批量导入模型建议",
            created_by=user_id,
        )
        db.add(change)
        db.commit()
        db.refresh(revision)
        db.refresh(change)
    except OntologyEditError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    audit = None
    try:
        from app.services.v2.audit_runner import queue_local_audit
        audit = queue_local_audit(db, ontology_id=ontology_id, revision_id=revision.id, construction_run_id=None)
    except Exception:
        audit = None
    return {
        "change": serialize_change(change),
        "revision": {"id": revision.id, "revision_no": revision.revision_no, "is_current": True},
        "operations": applied,
        "audit_task_id": getattr(audit, "id", None),
    }


def serialize_change(change: OntologyChange) -> dict[str, Any]:
    return {"id": change.id, "ontology_id": change.ontology_id, "base_revision_id": change.base_revision_id, "result_revision_id": change.result_revision_id, "target_kind": change.target_kind, "operation": change.operation, "target_id": change.target_id, "before": change.before_json or {}, "after": change.after_json or {}, "impact": change.impact_json or {}, "validation": change.validation_json or {}, "status": change.status, "note": change.note, "created_by": change.created_by, "created_at": change.created_at.isoformat() if change.created_at else None}


def editor_payload(db: Session, ontology_id: str) -> dict[str, Any]:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise OntologyEditError("NOT_FOUND", "本体不存在")
    entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).order_by(Entity.name_cn.asc()).all()
    relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).order_by(Relation.type.asc()).all()
    rules = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).order_by(LogicRule.name_cn.asc()).all()
    revision = _current_revision(db, ontology_id)
    return {"ontology_id": ontology_id, "data_class": project.data_class, "current_revision_id": revision.id if revision else None, "revision_no": revision.revision_no if revision else None, "entities": [{**_entity_payload(row), "property_definitions": _property_definitions(row)} for row in entities], "relationships": [_relation_payload(row) for row in relations], "logic_rules": [_rule_payload(row) for row in rules], "capabilities": {"edit_instances": False, "supported_kinds": ["entity_type", "property", "relationship", "logic_rule"], "cardinalities": sorted(CARDINALITIES)}}
