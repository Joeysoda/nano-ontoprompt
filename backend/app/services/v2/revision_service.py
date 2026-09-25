"""Immutable ontology snapshots and small, reviewable revision diffs."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.action import Action
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.logic import LogicRule
from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.relation import Relation
from app.services.storage_service import get_storage_service


def snapshot_ontology(db: Session, ontology_id: str) -> dict[str, Any]:
    entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).order_by(Entity.id.asc()).all()
    id_to_name = {item.id: item.name_cn for item in entities}
    relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).order_by(Relation.id.asc()).all()
    rules = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).order_by(LogicRule.id.asc()).all()
    actions = db.query(Action).filter(Action.ontology_id == ontology_id).order_by(Action.id.asc()).all()
    instances = db.query(EntityInstance).filter(EntityInstance.ontology_id == ontology_id).order_by(EntityInstance.created_at.asc()).all()
    instance_counts: dict[str, int] = {}
    instance_samples: dict[str, list[dict[str, Any]]] = {}
    for item in instances:
        instance_counts[item.entity_id] = instance_counts.get(item.entity_id, 0) + 1
        samples = instance_samples.setdefault(item.entity_id, [])
        if len(samples) < 3:
            samples.append({"id": item.id, "row_identity": item.row_identity, "row_data": item.row_data or {}})
    return {
        "entities": [{"id": item.id, "name_cn": item.name_cn, "name_en": item.name_en, "name_abbr": item.name_abbr, "snomed_id": item.snomed_id, "canonical_id": item.canonical_id, "type": item.type, "description": item.description, "properties": item.properties or {}, "confidence": item.confidence, "version": item.version, "instance_count": instance_counts.get(item.id, 0), "instance_samples": instance_samples.get(item.id, [])} for item in entities],
        "relations": [{"id": item.id, "source_entity": item.source_entity, "target_entity": item.target_entity, "source_name": id_to_name.get(item.source_entity), "target_name": id_to_name.get(item.target_entity), "type": item.type, "properties": item.properties or {}, "confidence": item.confidence} for item in relations],
        "logic_rules": [{"id": item.id, "name_cn": item.name_cn, "name_en": item.name_en, "description": item.description, "formula": item.formula, "linked_entities": item.linked_entities, "condition": getattr(item, "condition_json", {}), "effect": getattr(item, "effect_json", {}), "evidence": getattr(item, "evidence_json", {}), "model_invocation_id": getattr(item, "model_invocation_id", None), "revision_id": getattr(item, "revision_id", None), "confidence": item.confidence, "version": item.version, "enabled": bool(item.enabled), "status": item.status} for item in rules],
        "actions": [{"id": item.id, "name_cn": item.name_cn, "name_en": item.name_en, "description": item.description, "execution_rule": item.execution_rule, "function_code": item.function_code, "linked_entities": item.linked_entities or [], "linked_logic_ids": item.linked_logic_ids or [], "confidence": item.confidence, "version": item.version, "enabled": bool(item.enabled), "status": item.status} for item in actions],
        "instance_count": len(instances),
    }


def create_revision(
    db: Session,
    ontology_id: str,
    *,
    source_run_id: str | None = None,
    snapshot: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    parent_revision_id: str | None = None,
    commit: bool = True,
    revision_id: str | None = None,
) -> OntologyRevision:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise ValueError("Ontology not found")
    if snapshot is None:
        snapshot = snapshot_ontology(db, ontology_id)
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    latest_no = db.query(OntologyRevision.revision_no).filter(OntologyRevision.ontology_id == ontology_id).order_by(OntologyRevision.revision_no.desc()).first()
    revision_no = int(latest_no[0]) + 1 if latest_no else 1
    current = db.query(OntologyRevision).filter(OntologyRevision.ontology_id == ontology_id, OntologyRevision.is_current.is_(True)).all()
    for item in current:
        item.is_current = False
        if item.status == "current":
            item.status = "superseded"
    storage_uri = get_storage_service().put_bytes("curated-datasets", f"ontologies/{ontology_id}/revisions/{revision_no}-{digest[:12]}.json", payload, content_type="application/json")
    revision = OntologyRevision(
        id=revision_id or str(uuid.uuid4()), ontology_id=ontology_id, revision_no=revision_no,
        parent_revision_id=parent_revision_id, source_run_id=source_run_id,
        graph_namespace=f"ontology:{ontology_id}:r{revision_no}", snapshot_uri=storage_uri,
        snapshot_json=snapshot, snapshot_hash=digest,
        summary=summary or {"entity_count": len(snapshot.get("entities", [])), "instance_count": int(snapshot.get("instance_count", 0)), "relation_count": len(snapshot.get("relations", [])), "logic_count": len(snapshot.get("logic_rules", [])), "action_count": len(snapshot.get("actions", []))},
        status="current", is_current=True, created_at=datetime.now(timezone.utc),
    )
    db.add(revision)
    project.current_revision_id = revision.id
    project.version = f"r{revision_no}"
    if commit:
        db.commit()
        db.refresh(revision)
    else:
        db.flush()
    return revision


def materialize_snapshot(db: Session, ontology_id: str, snapshot: dict[str, Any]) -> None:
    """Restore schema objects without deleting source-generated instances.

    This function deliberately does not commit.  Callers can materialise the
    schema, create a new immutable revision and append provenance in one
    transaction.  Removing an entity that still owns real instances is
    rejected instead of cascading those instances away.
    """
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise ValueError("Ontology not found")

    entity_rows = {str(item.get("id")): item for item in snapshot.get("entities", []) if item.get("id")}
    current_entities = {
        item.id: item for item in db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    }
    removing = set(current_entities) - set(entity_rows)
    if removing:
        used = (
            db.query(EntityInstance.entity_id)
            .filter(EntityInstance.ontology_id == ontology_id, EntityInstance.entity_id.in_(removing))
            .distinct()
            .all()
        )
        if used:
            names = [current_entities[row[0]].name_cn for row in used if row[0] in current_entities]
            raise ValueError(f"恢复会删除仍有真实实例的实体类型：{'、'.join(names)}")

    # Relations must be removed before an entity.  Instances remain untouched.
    db.query(Relation).filter(Relation.ontology_id == ontology_id).delete(synchronize_session=False)
    for entity_id in removing:
        db.delete(current_entities[entity_id])
    db.flush()

    allowed_entity_fields = {
        "name_cn", "name_en", "name_abbr", "snomed_id", "canonical_id", "type",
        "description", "properties", "confidence", "version",
    }
    for entity_id, row in entity_rows.items():
        entity = current_entities.get(entity_id)
        values = {key: row.get(key) for key in allowed_entity_fields if key in row}
        if entity is None:
            entity = Entity(id=entity_id, ontology_id=ontology_id, name_cn=str(row.get("name_cn") or entity_id))
            db.add(entity)
        for key, value in values.items():
            setattr(entity, key, value)
    db.flush()

    for row in snapshot.get("relations", []):
        if row.get("source_entity") not in entity_rows or row.get("target_entity") not in entity_rows:
            raise ValueError(f"关系 {row.get('type') or row.get('id')} 引用了不存在的实体类型")
        db.add(Relation(
            id=str(row.get("id") or uuid.uuid4()), ontology_id=ontology_id,
            source_entity=str(row["source_entity"]), target_entity=str(row["target_entity"]),
            type=str(row.get("type") or "RELATED"), properties=row.get("properties") or {},
            confidence=float(row.get("confidence") or 1.0),
        ))

    db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).delete(synchronize_session=False)
    for row in snapshot.get("logic_rules", []):
        rule = LogicRule(
            id=str(row.get("id") or uuid.uuid4()), ontology_id=ontology_id,
            name_cn=str(row.get("name_cn") or "未命名规则"), name_en=row.get("name_en"),
            description=row.get("description"), formula=row.get("formula"),
            condition_json=row.get("condition") or {}, effect_json=row.get("effect") or {},
            evidence_json=row.get("evidence") or {}, model_invocation_id=row.get("model_invocation_id"),
            revision_id=row.get("revision_id"), confidence=float(row.get("confidence") or 1.0),
            version=str(row.get("version") or "v0.1"), enabled=bool(row.get("enabled", True)),
            status=str(row.get("status") or "draft"),
        )
        rule.linked_entities = list(row.get("linked_entities") or [])
        db.add(rule)

    db.query(Action).filter(Action.ontology_id == ontology_id).delete(synchronize_session=False)
    for row in snapshot.get("actions", []):
        db.add(Action(
            id=str(row.get("id") or uuid.uuid4()), ontology_id=ontology_id,
            name_cn=str(row.get("name_cn") or "未命名动作"), name_en=row.get("name_en"),
            description=row.get("description"), execution_rule=row.get("execution_rule"),
            function_code=row.get("function_code"), linked_entities=row.get("linked_entities") or [],
            linked_logic_ids=row.get("linked_logic_ids") or [], confidence=float(row.get("confidence") or 1.0),
            version=str(row.get("version") or "v0.1"), enabled=bool(row.get("enabled", True)),
            status=str(row.get("status") or "draft"),
        ))
    db.flush()


def serialize_revision(revision: OntologyRevision) -> dict[str, Any]:
    return {
        "id": revision.id, "ontology_id": revision.ontology_id, "revision_no": revision.revision_no,
        "parent_revision_id": revision.parent_revision_id, "source_run_id": revision.source_run_id,
        "graph_namespace": revision.graph_namespace, "snapshot_uri": revision.snapshot_uri,
        "snapshot_hash": revision.snapshot_hash, "summary": revision.summary or {},
        "status": revision.status, "is_current": bool(revision.is_current),
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
    }


def compare_revisions(left: OntologyRevision, right: OntologyRevision) -> dict[str, Any]:
    def keyed(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
        return {str(item.get(key)): item for item in items}

    sections = [("entities", "id"), ("relations", "id"), ("logic_rules", "id"), ("actions", "id")]
    diff: dict[str, Any] = {}
    for name, key in sections:
        before = keyed((left.snapshot_json or {}).get(name, []), key)
        after = keyed((right.snapshot_json or {}).get(name, []), key)
        added = [after[item] for item in sorted(set(after) - set(before))]
        removed = [before[item] for item in sorted(set(before) - set(after))]
        changed = [{"before": before[item], "after": after[item]} for item in sorted(set(before) & set(after)) if before[item] != after[item]]
        diff[name] = {"added": added, "removed": removed, "changed": changed, "added_count": len(added), "removed_count": len(removed), "changed_count": len(changed)}
    return {"left": serialize_revision(left), "right": serialize_revision(right), "diff": diff}
