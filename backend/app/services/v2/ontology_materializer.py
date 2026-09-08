"""Canonical, revision-friendly materialisation for workbench ontology builds.

The workbench has two persistence planes:

* SQL stores the user-visible ontology (entity types, relationships, rules and
  row/sample instances); and
* FalkorDB stores the denser instance network used for evidence traversal.

Earlier construction paths wrote only the second plane for regular and
multimodal data.  This module is deliberately shared by every construction
worker so the ontology page, entity catalogue and local audit always see the
same committed concepts.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.logic import LogicRule
from app.models.relation import Relation
from app.models.v2.construction import ConstructionRun, EvidenceRef


PROPERTY_TYPES = {"string", "integer", "decimal", "date", "datetime", "boolean", "enum", "json"}
CARDINALITIES = {"one-to-one", "one-to-many", "many-to-one", "many-to-many"}


def _slug(value: Any, fallback: str = "item") -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "-", str(value or "").strip()).strip("-").lower()
    return text or fallback


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = ":".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{_slug(parts[-1] if parts else prefix)}:{digest}"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _display_name(item: dict[str, Any], fallback: str) -> str:
    return str(item.get("name_cn") or item.get("name") or item.get("label") or item.get("target") or fallback).strip()


def _property_type(value: Any) -> str:
    normalised = str(value or "string").strip().lower()
    aliases = {"number": "decimal", "float": "decimal", "int": "integer", "object": "json", "array": "json"}
    normalised = aliases.get(normalised, normalised)
    return normalised if normalised in PROPERTY_TYPES else "string"


@dataclass
class MappingValidation:
    mapping: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass
class MaterializationResult:
    entity_type_count: int = 0
    relation_count: int = 0
    rule_count: int = 0
    instance_count: int = 0
    evidence_count: int = 0
    entity_ids: dict[str, str] = field(default_factory=dict)


def _legacy_to_v2(mapping: dict[str, Any], *, data_class: str) -> dict[str, Any]:
    """Convert prior ``suggestions``/``classes`` payloads without losing data.

    The conversion is only a compatibility layer.  New M3 calls are prompted
    for the explicit v2 contract, while old drafts remain buildable.
    """
    if mapping.get("schema_version") == "ontology-mapping-v2" or mapping.get("entity_types"):
        return dict(mapping)

    raw = mapping.get("raw") if isinstance(mapping.get("raw"), dict) else mapping
    suggestions = list(mapping.get("confirmed") or raw.get("confirmed") or mapping.get("suggestions") or raw.get("suggestions") or [])
    classes = list(raw.get("classes") or [])
    properties = list(raw.get("properties") or [])
    relations = list(raw.get("relations") or [])
    rules = list(raw.get("logic_rules") or raw.get("rules") or [])

    for item in suggestions:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").lower()
        if kind == "class":
            classes.append(item)
        elif kind == "property":
            properties.append(item)
        elif kind == "relation":
            relations.append(item)
        elif kind in {"rule", "logic_rule"}:
            rules.append(item)

    # A private/deterministic draft needs a minimal honest vocabulary.  It is
    # derived from source roles instead of invented business semantics.
    if not classes:
        fallback = {
            # These are source-structure concepts, not inferred business
            # semantics.  They make a private/manual build inspectable while
            # keeping the real business vocabulary under user/model control.
            "regular": ["SourceDataset", "Record"],
            "temporal": ["Episode", "Observation"],
            "multimodal": ["MultimodalSample", "MediaAsset"],
        }[data_class]
        classes = [{"id": _slug(name), "name": name, "description": f"来自已选 {data_class} 数据的记录类型"} for name in fallback]
    if not relations:
        known_names = {str(item.get("id") or item.get("name") or "").casefold() for item in classes if isinstance(item, dict)}
        if data_class == "regular":
            if "sourcedataset" not in known_names:
                classes.insert(0, {"id": "source-dataset", "name": "SourceDataset", "description": "已选数据集的来源容器"})
            if "record" not in known_names:
                classes.append({"id": "record", "name": "Record", "description": "来源中的真实记录"})
            relations = [{"name": "containsRecord", "from": "SourceDataset", "to": "Record", "cardinality": "one-to-many", "description": "数据集包含来源记录"}]
        elif data_class == "temporal":
            if "episode" not in known_names:
                classes.insert(0, {"id": "episode", "name": "Episode", "description": "来源中的时序片段"})
            if "observation" not in known_names:
                classes.append({"id": "observation", "name": "Observation", "description": "来源中的时序观测"})
            relations = [{"name": "hasObservation", "from": "Episode", "to": "Observation", "cardinality": "one-to-many", "description": "时序片段包含相对顺序观测"}]
        elif data_class == "multimodal":
            relations = [{"name": "hasAsset", "from": "MultimodalSample", "to": "MediaAsset", "cardinality": "one-to-many", "description": "一个样例包含多个证据资产"}]

    return {
        "schema_version": "ontology-mapping-v2",
        "entity_types": classes,
        "properties": properties,
        "relationships": relations,
        "logic_rules": rules,
        "source": mapping.get("source") or raw.get("source") or {},
    }


def normalise_mapping(mapping: dict[str, Any] | None, *, data_class: str) -> MappingValidation:
    payload = _legacy_to_v2(dict(mapping or {}), data_class=data_class)
    errors: list[str] = []
    warnings: list[str] = []

    entity_types: list[dict[str, Any]] = []
    ids: set[str] = set()
    names: set[str] = set()
    for index, raw in enumerate(_as_list(payload.get("entity_types"))):
        item = dict(raw) if isinstance(raw, dict) else {"name": str(raw)}
        name = _display_name(item, f"Entity {index + 1}")
        entity_id = _slug(item.get("id") or item.get("name_en") or item.get("name") or name, f"entity-{index + 1}")
        if entity_id in ids:
            errors.append(f"重复实体 ID：{entity_id}")
            continue
        if name.casefold() in names:
            errors.append(f"重复实体名称：{name}")
            continue
        ids.add(entity_id); names.add(name.casefold())
        entity_types.append({
            "id": entity_id,
            "name": item.get("name") or name,
            "name_cn": item.get("name_cn") or name,
            "name_en": item.get("name_en") or (item.get("name") if re.match(r"^[A-Za-z][A-Za-z0-9 _-]*$", str(item.get("name") or "")) else None),
            "description": item.get("description") or "",
            "source_fields": [str(value) for value in _as_list(item.get("source_fields") or item.get("source") or []) if value not in (None, "")],
            "identifier_property": item.get("identifier_property") or item.get("identifier"),
            "color": item.get("color"),
            "icon": item.get("icon"),
            "confidence": float(item.get("confidence", 1.0) or 1.0),
            "evidence": item.get("evidence") or {},
        })
    if not entity_types:
        errors.append("映射没有有效实体类型")

    property_items: list[dict[str, Any]] = []
    for index, raw in enumerate(_as_list(payload.get("properties"))):
        item = dict(raw) if isinstance(raw, dict) else {"name": str(raw)}
        entity_ref = _slug(item.get("entity_type") or item.get("entity") or item.get("class_id") or item.get("class") or entity_types[0]["id"] if entity_types else "")
        if entity_ref not in ids:
            # Legacy mapping frequently uses the human-readable class name.
            candidate = next((entity["id"] for entity in entity_types if str(entity["name"]).casefold() == str(item.get("entity_type") or item.get("class") or "").casefold()), None)
            entity_ref = candidate or entity_ref
        if entity_ref not in ids:
            errors.append(f"属性 {item.get('name') or item.get('target') or index + 1} 引用了不存在的实体")
            continue
        name = str(item.get("name") or item.get("target") or item.get("source") or f"property_{index + 1}")
        property_items.append({
            "id": _slug(item.get("id") or name, f"property-{index + 1}"),
            "entity_type": entity_ref,
            "name": name,
            "label": item.get("label") or item.get("name_cn") or name,
            "type": _property_type(item.get("type") or item.get("data_type")),
            "is_identifier": bool(item.get("is_identifier") or item.get("identifier")),
            "unit": item.get("unit"),
            "values": _as_list(item.get("values")),
            "description": item.get("description") or "",
            "source_field": item.get("source_field") or item.get("source"),
            "confidence": float(item.get("confidence", 1.0) or 1.0),
            "evidence": item.get("evidence") or {},
        })

    # At least one identifier belongs to every entity in the inspector.
    by_entity: dict[str, list[dict[str, Any]]] = {item["id"]: [] for item in entity_types}
    for item in property_items:
        by_entity[item["entity_type"]].append(item)
    for entity in entity_types:
        entries = by_entity[entity["id"]]
        identifier = entity.get("identifier_property")
        if identifier:
            for prop in entries:
                if prop["id"] == _slug(identifier) or prop["name"] == identifier:
                    prop["is_identifier"] = True
        if entries and not any(prop["is_identifier"] for prop in entries):
            entries[0]["is_identifier"] = True
            warnings.append(f"{entity['name_cn']} 未声明标识属性，已将 {entries[0]['name']} 作为标识属性")

    relationship_items: list[dict[str, Any]] = []
    for index, raw in enumerate(_as_list(payload.get("relationships") or payload.get("relations"))):
        item = dict(raw) if isinstance(raw, dict) else {"name": str(raw)}
        source = _slug(item.get("from") or item.get("source_entity") or item.get("source") or "")
        target = _slug(item.get("to") or item.get("target_entity") or item.get("target") or "")
        lookup = {str(entity["name"]).casefold(): entity["id"] for entity in entity_types}
        lookup.update({str(entity["name_cn"]).casefold(): entity["id"] for entity in entity_types})
        source = lookup.get(str(item.get("from") or item.get("source_entity") or item.get("source") or "").casefold(), source)
        target = lookup.get(str(item.get("to") or item.get("target_entity") or item.get("target") or "").casefold(), target)
        name = str(item.get("name") or item.get("target_relation") or item.get("relation") or f"relatesTo{index + 1}")
        if source not in ids or target not in ids:
            errors.append(f"关系 {name} 引用了不存在的实体")
            continue
        cardinality = str(item.get("cardinality") or "one-to-many")
        if cardinality not in CARDINALITIES:
            errors.append(f"关系 {name} 的基数无效：{cardinality}")
            continue
        relationship_items.append({
            "id": _slug(item.get("id") or name, f"relation-{index + 1}"),
            "name": name,
            "from": source,
            "to": target,
            "cardinality": cardinality,
            "description": item.get("description") or "",
            "attributes": [entry for entry in _as_list(item.get("attributes")) if isinstance(entry, dict)],
            "source_fields": [str(value) for value in _as_list(item.get("source_fields") or item.get("source_field") or []) if value],
            "confidence": float(item.get("confidence", 1.0) or 1.0),
            "evidence": item.get("evidence") or {},
        })

    rule_items: list[dict[str, Any]] = []
    for index, raw in enumerate(_as_list(payload.get("logic_rules") or payload.get("rules"))):
        item = dict(raw) if isinstance(raw, dict) else {"name": str(raw)}
        name = _display_name(item, f"规则 {index + 1}")
        linked = []
        for ref in _as_list(item.get("linked_entities") or item.get("entities")):
            key = _slug(ref)
            if key not in ids:
                key = next((entity["id"] for entity in entity_types if str(entity["name"]).casefold() == str(ref).casefold() or str(entity["name_cn"]).casefold() == str(ref).casefold()), key)
            if key in ids:
                linked.append(key)
        condition = item.get("if") or item.get("condition") or item.get("condition_json") or []
        effect = item.get("then") or item.get("effect") or item.get("effect_json") or {}
        if not condition or not effect:
            errors.append(f"规则 {name} 缺少 IF 或 THEN")
            continue
        rule_items.append({
            "id": _slug(item.get("id") or name, f"rule-{index + 1}"),
            "name": name,
            "name_cn": item.get("name_cn") or name,
            "name_en": item.get("name_en"),
            "description": item.get("description") or "",
            "formula": item.get("formula") or f"IF {json.dumps(condition, ensure_ascii=False)} THEN {json.dumps(effect, ensure_ascii=False)}",
            "condition": condition,
            "effect": effect,
            "linked_entities": linked,
            "confidence": float(item.get("confidence", 1.0) or 1.0),
            "evidence": item.get("evidence") or {},
        })

    payload.update({
        "schema_version": "ontology-mapping-v2",
        "entity_types": entity_types,
        "properties": property_items,
        "relationships": relationship_items,
        "logic_rules": rule_items,
    })
    return MappingValidation(payload, errors, warnings)


def mapping_suggestions(mapping: dict[str, Any], *, extractor: str = "m3") -> list[dict[str, Any]]:
    """Flatten v2 mapping for the existing editable mapping-card UI.

    ``extractor`` is a provenance label, not a cosmetic default.  Private
    mappings must remain visibly deterministic rather than being presented as
    an M3 result.
    """
    items: list[dict[str, Any]] = []
    rule_source = "模型读取计划" if extractor == "m3" else "已选来源字段"
    for entity in mapping.get("entity_types") or []:
        items.append({"kind": "class", "source": ", ".join(entity.get("source_fields") or []) or "source profile", "target": entity.get("name_cn") or entity.get("name"), "confidence": entity.get("confidence", 1.0), "extractor": extractor})
    for prop in mapping.get("properties") or []:
        items.append({"kind": "property", "source": prop.get("source_field") or "source profile", "target": prop.get("label") or prop.get("name"), "confidence": prop.get("confidence", 1.0), "extractor": extractor})
    for relation in mapping.get("relationships") or []:
        items.append({"kind": "relation", "source": relation.get("from"), "target": relation.get("to"), "target_relation": relation.get("name"), "confidence": relation.get("confidence", 1.0), "extractor": extractor})
    for rule in mapping.get("logic_rules") or []:
        items.append({"kind": "logic_rule", "source": rule_source, "target": rule.get("name_cn") or rule.get("name"), "confidence": rule.get("confidence", 1.0), "extractor": extractor})
    return items


def _instance_identity(item: dict[str, Any], index: int) -> str:
    for key in ("id", "reading_id", "sample_id", "sample_key", "equipment_id", "episode_id", "row_id"):
        if item.get(key) not in (None, ""):
            return f"{key}:{item[key]}"
    return f"row:{index + 1}"


def materialize_ontology(
    db: Session,
    *,
    run: ConstructionRun,
    mapping: dict[str, Any],
    instances: Iterable[dict[str, Any]],
    data_class: str,
    source_file: str | None = None,
    source_version: str | None = None,
    instance_entity: str | None = None,
    model_name: str | None = None,
    require_rule: bool = False,
) -> MaterializationResult:
    """Write a validated mapping and its concrete instances in one SQL unit.

    ``run`` is intentionally the provenance boundary.  Retrying the same run
    upserts stable identifiers; a new run produces a new revision afterwards.
    """
    validation = normalise_mapping(mapping, data_class=data_class)
    if validation.errors:
        raise ValueError("; ".join(validation.errors))
    payload = validation.mapping
    if require_rule and not payload["logic_rules"]:
        raise ValueError("内置案例需要至少一条可校验的 MiniMax 逻辑规则")
    if not payload["relationships"]:
        raise ValueError("本体至少需要一条有效关系")

    result = MaterializationResult()
    entity_ids: dict[str, str] = {}
    properties_by_entity: dict[str, list[dict[str, Any]]] = {}
    for prop in payload["properties"]:
        properties_by_entity.setdefault(prop["entity_type"], []).append(prop)

    for definition in payload["entity_types"]:
        entity_id = f"ontology:{run.ontology_id}:type:{definition['id']}"
        stored = db.get(Entity, entity_id)
        property_defs = properties_by_entity.get(definition["id"], [])
        stored_properties = [
            {
                "id": prop["id"], "name": prop["name"], "label": prop["label"], "type": prop["type"],
                "isIdentifier": prop["is_identifier"], "unit": prop["unit"], "values": prop["values"],
                "description": prop["description"], "source_field": prop["source_field"], "confidence": prop["confidence"],
                "evidence": prop["evidence"],
            }
            for prop in property_defs
        ]
        properties: dict[str, Any] = {
            "schema_version": "ontology-mapping-v2",
            "property_definitions": stored_properties,
            "source_fields": definition["source_fields"],
            "color": definition.get("color"),
            "icon": definition.get("icon"),
            "evidence": definition.get("evidence") or {},
            "data_class": data_class,
        }
        if stored is None:
            stored = Entity(
                id=entity_id, ontology_id=run.ontology_id, name_cn=definition["name_cn"],
                name_en=definition.get("name_en"), name_abbr=(definition.get("name_en") or definition["id"])[:12],
                canonical_id=f"ontology:{definition['id']}", type="EntityType",
                description=definition.get("description") or None, properties=properties,
                confidence=definition["confidence"], version="v2",
            )
            db.add(stored)
        else:
            stored.name_cn = definition["name_cn"]; stored.name_en = definition.get("name_en")
            stored.description = definition.get("description") or None; stored.type = "EntityType"
            stored.properties = properties; stored.confidence = definition["confidence"]; stored.version = "v2"
        entity_ids[definition["id"]] = entity_id
        result.entity_type_count += 1
    db.flush()

    for definition in payload["relationships"]:
        relation_id = f"ontology:{run.ontology_id}:relation:{definition['id']}:{definition['from']}:{definition['to']}"
        stored = db.get(Relation, relation_id)
        properties = {
            "schema_version": "ontology-mapping-v2", "name": definition["name"],
            "description": definition.get("description") or "", "cardinality": definition["cardinality"],
            "attributes": definition.get("attributes") or [], "source_fields": definition.get("source_fields") or [],
            "evidence": definition.get("evidence") or {}, "data_class": data_class,
        }
        if stored is None:
            stored = Relation(id=relation_id, ontology_id=run.ontology_id, source_entity=entity_ids[definition["from"]], target_entity=entity_ids[definition["to"]], type=definition["name"], properties=properties, confidence=definition["confidence"])
            db.add(stored)
        else:
            stored.source_entity = entity_ids[definition["from"]]; stored.target_entity = entity_ids[definition["to"]]
            stored.type = definition["name"]; stored.properties = properties; stored.confidence = definition["confidence"]
        result.relation_count += 1

    for definition in payload["logic_rules"]:
        rule_id = f"ontology:{run.ontology_id}:rule:{definition['id']}"
        stored = db.get(LogicRule, rule_id)
        if stored is None:
            stored = LogicRule(id=rule_id, ontology_id=run.ontology_id, name_cn=definition["name_cn"], name_en=definition.get("name_en"), description=definition.get("description") or None, formula=definition["formula"], confidence=definition["confidence"], version="v2", enabled=True, status="published")
            db.add(stored)
        else:
            stored.name_cn = definition["name_cn"]; stored.name_en = definition.get("name_en")
            stored.description = definition.get("description") or None; stored.formula = definition["formula"]
            stored.confidence = definition["confidence"]; stored.status = "published"; stored.version = "v2"
        stored.linked_entities = [entity_ids[item] for item in definition["linked_entities"] if item in entity_ids]
        # New columns are populated when the migration is present; keeping the
        # guards allows legacy test databases to retain read compatibility.
        if hasattr(stored, "condition_json"):
            stored.condition_json = definition["condition"]
            stored.effect_json = definition["effect"]
            stored.evidence_json = definition["evidence"]
            stored.model_invocation_id = None
        result.rule_count += 1

    chosen = _slug(instance_entity or "")
    if chosen not in entity_ids:
        chosen = next((entity["id"] for entity in payload["entity_types"] if entity["id"].casefold() in {"observation", "record", "multimodalsample", "sample"}), payload["entity_types"][0]["id"])
    class_id = entity_ids[chosen]
    for index, row in enumerate(instances):
        row = dict(row)
        identity = _instance_identity(row, index)
        instance_id = _stable_id(f"ontology:{run.ontology_id}:instance", class_id, identity)
        stored = db.get(EntityInstance, instance_id)
        if stored is None:
            stored = EntityInstance(id=instance_id, entity_id=class_id, ontology_id=run.ontology_id, row_identity=identity, row_data=row)
            db.add(stored)
        else:
            stored.entity_id = class_id; stored.row_identity = identity; stored.row_data = row
        if hasattr(stored, "revision_id"):
            stored.revision_id = None
        result.instance_count += 1
        text = json.dumps(row, ensure_ascii=False, default=str)[:8000]
        evidence_id = _stable_id(f"evidence:{run.id}", "instance", instance_id)
        if db.get(EvidenceRef, evidence_id) is None:
            db.add(EvidenceRef(
                id=evidence_id, construction_run_id=run.id, ontology_id=run.ontology_id,
                assertion_id=instance_id, assertion_kind="node", source_dataset_id=run.dataset_id,
                source_version=source_version, source_file=source_file,
                source_row_id=str(row.get("_source_row_index", index + 1)), extractor="rule",
                model_name=model_name, confidence=1.0, confidence_method="source_row_or_asset",
                evidence_text=text, content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            ))
            result.evidence_count += 1
    db.flush()
    result.entity_ids = entity_ids
    return result


def attach_revision_to_materialized_rows(db: Session, *, run: ConstructionRun, revision_id: str) -> None:
    """Attach the final revision after snapshot creation without separate UI state."""
    assertion_ids = [
        str(item[0])
        for item in db.query(EvidenceRef.assertion_id)
        .filter(EvidenceRef.construction_run_id == run.id)
        .all()
        if item[0]
    ]
    db.query(EvidenceRef).filter(EvidenceRef.construction_run_id == run.id, EvidenceRef.revision_id.is_(None)).update({EvidenceRef.revision_id: revision_id}, synchronize_session=False)
    if hasattr(EntityInstance, "revision_id"):
        query = db.query(EntityInstance).filter(EntityInstance.ontology_id == run.ontology_id, EntityInstance.revision_id.is_(None))
        if assertion_ids:
            query = query.filter(EntityInstance.id.in_(assertion_ids))
        else:
            # A construction with no row/sample evidence must not silently
            # claim historical instances from another run.
            return
        query.update({EntityInstance.revision_id: revision_id}, synchronize_session=False)
    db.flush()
