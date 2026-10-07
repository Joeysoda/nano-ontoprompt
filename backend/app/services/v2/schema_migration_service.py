"""Impact analysis and reversible semantic schema migrations."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.v2.schema_migration import SchemaDependency, SchemaMigrationInstruction, SchemaMigrationPlan, SchemaMigrationRun
from app.models.v2.semantic_core import OntologySemanticResource, OntologySemanticResourceVersion, OntologySourceMapping
from app.models.v2.dynamic_ontology import WhatIfScenario
from app.models.v2.temporal_replay import DataModelSnapshot, TemporalFact
from app.services.v2 import revision_service
from app.services.v2.semantic_core_service import SAFE_API_NAME, SemanticCoreError, _digest, ensure_semantic_metadata, semantic_schema


SUPPORTED_INSTRUCTIONS = {
    "rename_api_name",
    "rename_display_name",
    "cast_property",
    "drop_property",
    "replace_source",
    "move_edits",
    "rebuild_projection",
}
CASTS = {
    ("integer", "long"), ("integer", "double"), ("integer", "string"),
    ("long", "integer"), ("long", "double"), ("long", "string"),
    ("double", "integer"), ("double", "long"), ("double", "string"),
    ("boolean", "string"), ("date", "string"), ("timestamp", "string"),
    ("string", "integer"), ("string", "long"), ("string", "double"),
    ("string", "boolean"), ("string", "date"), ("string", "timestamp"),
}


class SchemaMigrationError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.context_id = str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _current_revision(db: Session, ontology_id: str) -> OntologyRevision:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise SchemaMigrationError("NOT_FOUND", "本体不存在")
    revision = db.query(OntologyRevision).filter(OntologyRevision.id == project.current_revision_id).first() if project.current_revision_id else None
    revision = revision or db.query(OntologyRevision).filter(OntologyRevision.ontology_id == ontology_id, OntologyRevision.is_current.is_(True)).order_by(OntologyRevision.revision_no.desc()).first()
    if not revision:
        raise SchemaMigrationError("REVISION_REQUIRED", "本体还没有可迁移的修订")
    return revision


def _target_row(db: Session, ontology_id: str, revision_id: str, instruction: dict[str, Any]) -> OntologySemanticResourceVersion | None:
    target_id = str(instruction.get("resource_id") or instruction.get("target_id") or "")
    query = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.revision_id == revision_id,
    )
    if target_id:
        row = query.filter(OntologySemanticResourceVersion.resource_id == target_id).first()
        if row:
            return row
    api_name = str(instruction.get("api_name") or instruction.get("payload", {}).get("api_name") or "")
    if api_name:
        return query.filter(OntologySemanticResourceVersion.api_name == api_name).first()
    return None


def _dependencies(db: Session, ontology_id: str, revision_id: str, target: OntologySemanticResourceVersion) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    versions = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.revision_id == revision_id,
    ).all()
    for row in versions:
        references: list[tuple[str, str | None]] = [
            ("parent", row.parent_resource_id),
            ("source", row.source_resource_id),
            ("target", row.target_resource_id),
            ("interface", row.interface_resource_id),
            ("value_type", row.value_type_resource_id),
            ("struct", row.struct_resource_id),
        ]
        if any(ref == target.resource_id for _, ref in references):
            rows.append({"kind": row.kind, "id": row.resource_id, "reason": "规范资源引用"})
        linked = (row.constraints_json or {}).get("linked_entities", []) if isinstance(row.constraints_json, dict) else []
        if target.resource_id in {str(item) for item in linked}:
            rows.append({"kind": row.kind, "id": row.resource_id, "reason": "逻辑规则引用"})
    if target.kind == "object_type":
        # A canonical object type resource can be linked to a legacy Entity
        # row by semantic_resource_id.  Instances make a drop destructive.
        legacy_ids = [item.id for item in db.query(Entity).filter(Entity.ontology_id == ontology_id, Entity.semantic_resource_id == target.resource_id).all()]
        if legacy_ids:
            count = db.query(EntityInstance.id).filter(EntityInstance.ontology_id == ontology_id, EntityInstance.entity_id.in_(legacy_ids)).count()
            if count:
                rows.append({"kind": "entity_instance", "id": target.resource_id, "reason": f"存在 {count} 个真实实例"})
    elif target.kind == "property" and target.parent_resource_id:
        # A property drop is destructive even when no other semantic resource
        # points at it: source-owned instances may still carry real values.
        entity = db.query(Entity).filter(
            Entity.ontology_id == ontology_id,
            Entity.semantic_resource_id == target.parent_resource_id,
        ).first()
        legacy_property_id = str((target.provenance_json or {}).get("legacy_property_id") or target.api_name)
        if entity:
            count = 0
            for item in db.query(EntityInstance).filter(
                EntityInstance.ontology_id == ontology_id,
                EntityInstance.entity_id == entity.id,
            ).all():
                payload = item.row_data if isinstance(item.row_data, dict) else {}
                if payload.get(legacy_property_id) not in (None, ""):
                    count += 1
            if count:
                rows.append({"kind": "entity_instance_property", "id": target.resource_id, "reason": f"仍有 {count} 个实例包含属性值"})
    return rows


def _property_keys(row: OntologySemanticResourceVersion) -> list[str]:
    provenance = row.provenance_json if isinstance(row.provenance_json, dict) else {}
    return list(dict.fromkeys(str(value) for value in (
        provenance.get("legacy_property_id"), row.api_name, row.source_field,
    ) if value))


def _cast_value(value: Any, target_type: str) -> Any:
    """Convert one stored property value using strict, predictable rules."""
    if value is None:
        return None
    target_type = target_type.lower()
    if target_type in {"string", "text"}:
        return str(value)
    if target_type in {"integer", "long"}:
        if isinstance(value, bool):
            raise ValueError("boolean is not an integer value")
        return int(value)
    if target_type == "double":
        if isinstance(value, bool):
            raise ValueError("boolean is not a numeric value")
        return float(value)
    if target_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        raise ValueError("expected true/false, yes/no, or 1/0")
    if target_type == "date":
        return date.fromisoformat(str(value)).isoformat()
    if target_type == "timestamp":
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    raise ValueError(f"unsupported target type: {target_type}")


def _property_instances(db: Session, ontology_id: str, row: OntologySemanticResourceVersion) -> list[EntityInstance]:
    from app.services.v2.semantic_core_service import _legacy_property_parent

    entity, _definition = _legacy_property_parent(db, ontology_id, row.resource_id)
    if not entity:
        return []
    return db.query(EntityInstance).filter(
        EntityInstance.ontology_id == ontology_id,
        EntityInstance.entity_id == entity.id,
    ).all()


def _cast_preview(db: Session, ontology_id: str, row: OntologySemanticResourceVersion, target_type: str) -> dict[str, Any]:
    keys = _property_keys(row)
    values = []
    failures = []
    for instance in _property_instances(db, ontology_id, row):
        payload = instance.row_data if isinstance(instance.row_data, dict) else {}
        for key in keys:
            if key not in payload or payload[key] is None:
                continue
            values.append((instance.id, key, payload[key]))
            try:
                _cast_value(payload[key], target_type)
            except (TypeError, ValueError, OverflowError) as exc:
                failures.append({"instance_id": instance.id, "field": key, "reason": str(exc)[:200]})
    return {"value_count": len(values), "failures": failures[:20], "failure_count": len(failures)}


def _replace_json_value(value: Any, old: str, new: str) -> tuple[Any, int]:
    if isinstance(value, dict):
        replaced = {}
        count = 0
        for key, item in value.items():
            replaced_key = new if str(key) == old else key
            count += int(replaced_key != key)
            replaced_item, nested = _replace_json_value(item, old, new)
            replaced[replaced_key] = replaced_item
            count += nested
        return replaced, count
    if isinstance(value, list):
        replaced = []
        count = 0
        for item in value:
            replaced_item, nested = _replace_json_value(item, old, new)
            replaced.append(replaced_item)
            count += nested
        return replaced, count
    if value == old:
        return new, 1
    return value, 0


def _apply_move_edits(db: Session, ontology_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("from_resource_id") or payload.get("source_resource_id") or "")
    target = str(payload.get("to_resource_id") or payload.get("target_resource_id") or "")
    if not source or not target or source == target:
        raise SchemaMigrationError("MIGRATION_INVALID", "move_edits 需要不同的 from_resource_id 和 to_resource_id")
    changed = []
    reference_count = 0
    for scenario in db.query(WhatIfScenario).filter(WhatIfScenario.ontology_id == ontology_id).all():
        before = {
            "assumptions_json": copy.deepcopy(scenario.assumptions_json or []),
            "rule_overrides_json": copy.deepcopy(scenario.rule_overrides_json or {}),
        }
        assumptions, a_count = _replace_json_value(before["assumptions_json"], source, target)
        overrides, o_count = _replace_json_value(before["rule_overrides_json"], source, target)
        if a_count or o_count:
            scenario.assumptions_json = assumptions
            scenario.rule_overrides_json = overrides
            changed.append({"scenario_id": scenario.id, "before": before})
            reference_count += a_count + o_count
    return {"source_resource_id": source, "target_resource_id": target, "moved_reference_count": reference_count, "scenarios": changed}


def _move_edit_preview(db: Session, ontology_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("from_resource_id") or payload.get("source_resource_id") or "")
    target = str(payload.get("to_resource_id") or payload.get("target_resource_id") or "")
    if not source or not target or source == target:
        raise SchemaMigrationError("MIGRATION_INVALID", "move_edits 需要不同的 from_resource_id 和 to_resource_id")
    count = 0
    scenario_count = 0
    for scenario in db.query(WhatIfScenario).filter(WhatIfScenario.ontology_id == ontology_id).all():
        _, assumptions = _replace_json_value(scenario.assumptions_json or [], source, target)
        _, overrides = _replace_json_value(scenario.rule_overrides_json or {}, source, target)
        if assumptions or overrides:
            count += assumptions + overrides
            scenario_count += 1
    return {"reference_count": count, "scenario_count": scenario_count}


def _snapshot_instance_row(backups: dict[str, dict[str, Any]], instance: EntityInstance) -> dict[str, Any]:
    return backups.setdefault(instance.id, {"instance_id": instance.id, "row_data": copy.deepcopy(instance.row_data or {})})


def _transform_property_values(
    db: Session,
    ontology_id: str,
    row: OntologySemanticResourceVersion,
    *,
    mode: str,
    target_type: str | None = None,
    backups: dict[str, dict[str, Any]],
) -> int:
    keys = _property_keys(row)
    changed = 0
    for instance in _property_instances(db, ontology_id, row):
        original = instance.row_data if isinstance(instance.row_data, dict) else {}
        updated = copy.deepcopy(original)
        touched = False
        for key in keys:
            if key not in updated or updated[key] is None:
                continue
            _snapshot_instance_row(backups, instance)
            if mode == "cast":
                updated[key] = _cast_value(updated[key], str(target_type or "string"))
            elif mode == "drop":
                archive = updated.setdefault("__retired_properties", {})
                if not isinstance(archive, dict):
                    raise SchemaMigrationError("MIGRATION_BLOCKED", "实例的 __retired_properties 不是对象，无法安全归档")
                archive.setdefault(row.resource_id, {})[key] = updated.pop(key)
            else:
                raise SchemaMigrationError("MIGRATION_INVALID", f"未知属性迁移模式：{mode}")
            touched = True
            changed += 1
        if touched:
            instance.row_data = updated
    return changed


def _rebuild_graph_projection(db: Session, ontology_id: str, namespace: str) -> dict[str, Any]:
    """Rebuild a FactoryNet temporal graph from its PostgreSQL fact ledger."""
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    snapshot_id = getattr(project, "current_data_snapshot_id", None) if project else None
    snapshot = db.query(DataModelSnapshot).filter(
        DataModelSnapshot.id == snapshot_id,
        DataModelSnapshot.ontology_id == ontology_id,
        DataModelSnapshot.status == "published",
    ).first() if snapshot_id else None
    if not snapshot:
        raise SchemaMigrationError(
            "PROJECTION_SOURCE_UNAVAILABLE",
            "PostgreSQL 中没有已发布的数据快照，无法安全重建图投影",
            details={"next_action": "先发布权威数据快照，再重试 rebuild_projection"},
        )
    facts = db.query(TemporalFact).filter(TemporalFact.replay_id == snapshot.replay_id).order_by(
        TemporalFact.valid_from_ordinal.asc(), TemporalFact.id.asc(),
    ).all()
    if len(facts) != int(snapshot.fact_count or 0):
        raise SchemaMigrationError(
            "PROJECTION_SOURCE_INCONSISTENT",
            "PostgreSQL 快照记录的事实数与事实账本不一致",
            details={"snapshot_id": snapshot.id, "expected": int(snapshot.fact_count or 0), "actual": len(facts)},
        )
    from app.services.v2.graph.falkordb_service import FalkorDBService

    graph = FalkorDBService()
    if not graph.available:
        raise SchemaMigrationError(
            "ADAPTER_UNAVAILABLE", "FalkorDB 当前不可用，图投影没有重建",
            details={"next_action": "恢复 FalkorDB 后使用同一迁移 run 重试"},
        )
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for fact in facts:
        subject = str(fact.subject_id)
        subject_node = nodes.setdefault(subject, {"id": subject, "entity_type": subject.split(":")[1] if ":" in subject else "Entity", "properties": {"_snapshot_id": snapshot.id}})
        value = fact.object_value
        if isinstance(value, dict):
            value = value.get("value", json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
        if fact.object_id:
            object_id = str(fact.object_id)
            nodes.setdefault(object_id, {"id": object_id, "entity_type": object_id.split(":")[1] if ":" in object_id else "Entity", "properties": {"_snapshot_id": snapshot.id}})
            edges.append({
                "source": subject,
                "target": object_id,
                "type": str(fact.predicate),
                "properties": {
                    "_fact_id": fact.id,
                    "_replay_id": snapshot.replay_id,
                    "_snapshot_id": snapshot.id,
                    "state_key": fact.state_key or "",
                    "valid_from_ordinal": float(fact.valid_from_ordinal),
                    "valid_to_ordinal": float(fact.valid_to_ordinal) if fact.valid_to_ordinal is not None else None,
                    "status": fact.status,
                    "value": value if isinstance(value, (str, int, float, bool)) else json.dumps(value, ensure_ascii=False, default=str) if value is not None else None,
                },
            })
        else:
            subject_node["properties"].setdefault("facts", {})[str(fact.predicate)] = value
            subject_node["properties"]["_fact_ids"] = subject_node["properties"].get("_fact_ids", []) + [fact.id]
    for node in nodes.values():
        properties = node["properties"]
        if isinstance(properties.get("facts"), dict):
            properties["facts_json"] = json.dumps(properties.pop("facts"), ensure_ascii=False, sort_keys=True, default=str)
        if isinstance(properties.get("_fact_ids"), list):
            properties["_fact_ids"] = json.dumps(properties["_fact_ids"], ensure_ascii=False)
    # FalkorDB graph properties do not need explicit null values; omitting
    # them makes rebuilds portable across server/client versions and keeps
    # missing valid-to/value fields distinct from serialized JSON payloads.
    for edge in edges:
        edge["properties"] = {
            key: value for key, value in edge["properties"].items()
            if value is not None
        }
    node_count = graph.upsert_instances(ontology_id, list(nodes.values()), graph_namespace=namespace)
    edge_count = graph.upsert_relations(ontology_id, edges, graph_namespace=namespace)
    if node_count != len(nodes) or edge_count != len(edges):
        raise SchemaMigrationError(
            "PROJECTION_REBUILD_INCOMPLETE", "FalkorDB 写入数量与 PostgreSQL 事实不一致",
            details={"nodes_expected": len(nodes), "nodes_written": node_count, "edges_expected": len(edges), "edges_written": edge_count},
        )
    return {
        "snapshot_id": snapshot.id,
        "snapshot_hash": snapshot.snapshot_hash,
        "replay_id": snapshot.replay_id,
        "source_fact_count": len(facts),
        "node_count": node_count,
        "edge_count": edge_count,
        "graph_namespace": namespace,
    }


def _serialize_plan(plan: SchemaMigrationPlan, instructions: list[SchemaMigrationInstruction] | None = None) -> dict[str, Any]:
    rows = instructions or []
    return {
        "id": plan.id,
        "ontology_id": plan.ontology_id,
        "base_revision_id": plan.base_revision_id,
        "target_revision_id": plan.target_revision_id,
        "status": plan.status,
        "phase": plan.phase,
        "impact": plan.impact_json or {},
        "shadow_namespace": plan.shadow_namespace,
        "result": plan.result_json or {},
        "error": plan.error,
        "created_by": plan.created_by,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
        "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
        "instructions": [
            {"id": row.id, "sequence_no": row.sequence_no, "kind": row.instruction_kind, "resource_id": row.resource_id, "payload": row.payload_json or {}, "status": row.status, "error": row.error}
            for row in rows
        ],
    }


def _serialize_run(run: SchemaMigrationRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "id": run.id,
        "plan_id": run.plan_id,
        "ontology_id": run.ontology_id,
        "status": run.status,
        "phase": run.phase,
        "progress": run.progress,
        "target_revision_id": run.target_revision_id,
        "result": run.result_json or {},
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def build_dependencies(db: Session, ontology_id: str, revision_id: str) -> int:
    """Rebuild the explicit dependency index for one immutable revision."""
    db.query(SchemaDependency).filter(SchemaDependency.ontology_id == ontology_id, SchemaDependency.revision_id == revision_id).delete(synchronize_session=False)
    rows = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.ontology_id == ontology_id,
        OntologySemanticResourceVersion.revision_id == revision_id,
    ).all()
    count = 0
    for row in rows:
        refs = [("parent", row.parent_resource_id), ("source", row.source_resource_id), ("target", row.target_resource_id), ("interface", row.interface_resource_id), ("value_type", row.value_type_resource_id), ("struct", row.struct_resource_id)]
        for label, target_id in refs:
            if not target_id:
                continue
            db.add(SchemaDependency(ontology_id=ontology_id, revision_id=revision_id, source_kind=row.kind, source_id=row.resource_id, target_kind=label, target_id=str(target_id), reason=f"{row.kind} 引用 {label}"))
            count += 1
    db.flush()
    return count


def _resolve_plan(db: Session, identifier: str) -> SchemaMigrationPlan | None:
    """Accept the public run id as well as the internal plan id."""
    plan = db.query(SchemaMigrationPlan).filter(SchemaMigrationPlan.id == identifier).first()
    if plan:
        return plan
    run = db.query(SchemaMigrationRun).filter(SchemaMigrationRun.id == identifier).first()
    return db.query(SchemaMigrationPlan).filter(SchemaMigrationPlan.id == run.plan_id).first() if run else None


def dry_run(db: Session, ontology_id: str, body: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
    base = _current_revision(db, ontology_id)
    expected = body.get("base_revision_id")
    if expected and str(expected) != str(base.id):
        raise SchemaMigrationError("REVISION_CONFLICT", "迁移基于旧修订，请刷新后重试", details={"expected": expected, "actual": base.id})
    ensure_semantic_metadata(db, ontology_id, base.id)
    instructions = body.get("instructions") or []
    if not isinstance(instructions, list) or not instructions:
        raise SchemaMigrationError("MIGRATION_EMPTY", "至少需要一条迁移指令")
    if len(instructions) > 500:
        raise SchemaMigrationError("MIGRATION_TOO_LARGE", "一次最多处理 500 条迁移指令")
    impact_items: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(instructions):
        if not isinstance(raw, dict):
            raise SchemaMigrationError("MIGRATION_INVALID", f"第 {index + 1} 条迁移指令不是对象")
        kind = str(raw.get("kind") or raw.get("instruction_kind") or "")
        if kind not in SUPPORTED_INSTRUCTIONS:
            raise SchemaMigrationError("MIGRATION_INVALID", f"不支持的迁移指令：{kind}")
        target = _target_row(db, ontology_id, base.id, raw)
        if kind not in {"rebuild_projection", "move_edits"} and not target:
            raise SchemaMigrationError("MIGRATION_TARGET_NOT_FOUND", f"第 {index + 1} 条指令找不到目标资源")
        payload = copy.deepcopy(raw.get("payload") or raw)
        extra_impact: dict[str, Any] = {}
        instruction_blockers: list[dict[str, Any]] = []
        if target and kind == "cast_property":
            if target.kind != "property":
                raise SchemaMigrationError("MIGRATION_INVALID", "cast_property 只能作用于 property")
            before = str(target.base_type or "string").lower()
            after = str(payload.get("to_type") or payload.get("base_type") or "").lower()
            if (before, after) not in CASTS:
                raise SchemaMigrationError("UNSUPPORTED_CAST", f"不允许从 {before} 转换为 {after}")
            payload["from_type"] = before
            payload["to_type"] = after
            cast_info = _cast_preview(db, ontology_id, target, after)
            extra_impact["cast"] = cast_info
            if cast_info["failure_count"]:
                instruction_blockers.append({"kind": "uncastable_values", **cast_info})
        if target and kind == "drop_property":
            if target.kind != "property":
                raise SchemaMigrationError("MIGRATION_INVALID", "drop_property 只能作用于 property")
            payload["archive_values"] = bool(payload.get("archive_values", False))
            keys = _property_keys(target)
            value_count = sum(
                1
                for instance in _property_instances(db, ontology_id, target)
                for key in keys
                if isinstance(instance.row_data, dict) and instance.row_data.get(key) not in (None, "")
            )
            extra_impact["archived_instance_value_count"] = value_count
        if target and kind == "rename_api_name":
            new_name = str(payload.get("new_api_name") or payload.get("api_name") or "").strip()
            if not SAFE_API_NAME.fullmatch(new_name):
                raise SchemaMigrationError("INVALID_API_NAME", f"新的 API name 无效：{new_name}")
            duplicate = db.query(OntologySemanticResourceVersion).filter(
                OntologySemanticResourceVersion.ontology_id == ontology_id,
                OntologySemanticResourceVersion.revision_id == base.id,
                OntologySemanticResourceVersion.api_name == new_name,
                OntologySemanticResourceVersion.resource_id != target.resource_id,
            ).first()
            if duplicate:
                raise SchemaMigrationError("DUPLICATE_API_NAME", f"迁移后的 API name 已存在：{new_name}")
            payload["new_api_name"] = new_name
        if target and kind == "replace_source":
            if target.kind != "property":
                raise SchemaMigrationError("MIGRATION_INVALID", "replace_source 只能作用于 property")
            source_field = str(payload.get("source_field") or "").strip()
            if not source_field:
                raise SchemaMigrationError("MIGRATION_INVALID", "replace_source 必须提供非空 source_field")
            payload["source_field"] = source_field
            extra_impact["previous_mappings"] = db.query(OntologySourceMapping).filter(
                OntologySourceMapping.revision_id == base.id,
                OntologySourceMapping.resource_id == target.resource_id,
            ).count()
        if kind == "move_edits":
            move_info = _move_edit_preview(db, ontology_id, payload)
            referenced_ids = {
                str(payload.get("from_resource_id") or payload.get("source_resource_id")),
                str(payload.get("to_resource_id") or payload.get("target_resource_id")),
            }
            existing_ids = {
                item[0] for item in db.query(OntologySemanticResourceVersion.resource_id).filter(
                    OntologySemanticResourceVersion.ontology_id == ontology_id,
                    OntologySemanticResourceVersion.revision_id == base.id,
                    OntologySemanticResourceVersion.resource_id.in_(referenced_ids),
                ).all()
            }
            if existing_ids != referenced_ids:
                raise SchemaMigrationError("MIGRATION_TARGET_NOT_FOUND", "move_edits 引用的规范资源不存在于当前修订")
            if not move_info["reference_count"]:
                raise SchemaMigrationError("MIGRATION_TARGET_NOT_FOUND", "没有找到可迁移的 What-If 编辑引用")
            extra_impact["edit_references"] = move_info
        if target and target.kind == "property" and kind == "drop_property":
            dependents = _dependencies(db, ontology_id, base.id, target)
        else:
            dependents = _dependencies(db, ontology_id, base.id, target) if target else []
        blockers = []
        if kind == "drop_property":
            blockers.extend(
                item for item in dependents
                if item.get("kind") != "entity_instance_property" or not payload.get("archive_values")
            )
        blockers.extend(instruction_blockers)
        impact_item = {
            "sequence_no": index,
            "instruction_kind": kind,
            "resource_id": target.resource_id if target else None,
            "api_name": target.api_name if target else None,
            "resource_kind": target.kind if target else None,
            "dependencies": dependents,
            "blockers": blockers,
            "requires_shadow_projection": kind in {"cast_property", "drop_property", "replace_source", "rebuild_projection"},
            "reversible": True,
            "can_apply": not blockers,
        }
        impact_item.update(extra_impact)
        impact_items.append(impact_item)
        normalized.append({"kind": kind, "resource_id": target.resource_id if target else None, "payload": payload})
    plan = SchemaMigrationPlan(
        id=str(uuid.uuid4()), ontology_id=ontology_id, base_revision_id=base.id,
        status="validated", phase="impact", impact_json={"items": impact_items, "base_metadata_digest": base.metadata_digest, "can_apply": not any(item.get("blockers") for item in impact_items)},
        shadow_namespace=f"schema-shadow:{ontology_id}:{uuid.uuid4().hex}", created_by=user_id,
    )
    db.add(plan)
    db.flush()
    for index, item in enumerate(normalized):
        db.add(SchemaMigrationInstruction(plan_id=plan.id, sequence_no=index, instruction_kind=item["kind"], resource_id=item["resource_id"], payload_json=item["payload"], status="planned"))
    run = SchemaMigrationRun(plan_id=plan.id, ontology_id=ontology_id, status="queued", phase="impact", progress=0, created_by=user_id)
    db.add(run)
    build_dependencies(db, ontology_id, base.id)
    db.commit()
    rows = db.query(SchemaMigrationInstruction).filter(SchemaMigrationInstruction.plan_id == plan.id).order_by(SchemaMigrationInstruction.sequence_no.asc()).all()
    result = _serialize_plan(plan, rows)
    result["run"] = _serialize_run(run)
    return result


def _clone_revision(db: Session, base: OntologyRevision, *, shadow_namespace: str, status: str = "shadow") -> OntologyRevision:
    latest = db.query(OntologyRevision.revision_no).filter(OntologyRevision.ontology_id == base.ontology_id).order_by(OntologyRevision.revision_no.desc()).first()
    revision = OntologyRevision(
        id=str(uuid.uuid4()), ontology_id=base.ontology_id, revision_no=int(latest[0] or 0) + 1 if latest else 1,
        parent_revision_id=base.id, source_run_id=base.source_run_id, graph_namespace=shadow_namespace,
        snapshot_uri=None, snapshot_json=copy.deepcopy(base.snapshot_json or {}), snapshot_hash=base.snapshot_hash,
        summary={"schema_migration_shadow": True, "base_revision_id": base.id}, status=status, is_current=False,
        metadata_digest=base.metadata_digest, metadata_schema_version=base.metadata_schema_version,
    )
    db.add(revision)
    db.flush()
    rows = db.query(OntologySemanticResourceVersion).filter(OntologySemanticResourceVersion.revision_id == base.id).all()
    cloned_versions: dict[str, OntologySemanticResourceVersion] = {}
    for row in rows:
        data = {column.name: getattr(row, column.name) for column in OntologySemanticResourceVersion.__table__.columns if column.name not in {"id", "revision_id", "created_at"}}
        data["metadata_json"] = copy.deepcopy(row.metadata_json or {})
        data.update({"id": str(uuid.uuid4()), "revision_id": revision.id, "created_at": _now()})
        clone = OntologySemanticResourceVersion(**data)
        db.add(clone)
        cloned_versions[str(row.resource_id)] = clone
    mappings = db.query(OntologySourceMapping).filter(OntologySourceMapping.revision_id == base.id).all()
    mapping_id_map: dict[str, str] = {}
    for mapping in mappings:
        data = {column.name: getattr(mapping, column.name) for column in OntologySourceMapping.__table__.columns if column.name not in {"id", "revision_id", "created_at"}}
        new_id = str(uuid.uuid4())
        data.update({"id": new_id, "revision_id": revision.id, "created_at": _now()})
        db.add(OntologySourceMapping(**data))
        mapping_id_map[str(mapping.id)] = new_id
    for original in rows:
        if original.kind != "source_mapping":
            continue
        clone = cloned_versions[str(original.resource_id)]
        metadata = copy.deepcopy(clone.metadata_json or {})
        old_mapping_id = str(metadata.get("source_mapping_row_id") or "")
        if old_mapping_id in mapping_id_map:
            metadata["source_mapping_row_id"] = mapping_id_map[old_mapping_id]
            clone.metadata_json = metadata
    db.flush()
    return revision


def _apply_legacy_projection_for_instruction(
    db: Session,
    ontology_id: str,
    row: OntologySemanticResourceVersion,
    instruction_kind: str,
    payload: dict[str, Any],
) -> None:
    """Keep legacy Entity/Relation/JSON readers aligned with a migration."""
    from app.models.logic import LogicRule
    from app.services.v2.semantic_core_service import _legacy_entity_for_resource, _legacy_property_parent, _legacy_relation_for_resource

    if row.kind == "object_type":
        entity = _legacy_entity_for_resource(db, ontology_id, row.resource_id)
        if entity:
            if instruction_kind == "rename_api_name":
                entity.canonical_id = str(payload.get("new_api_name") or payload.get("api_name") or entity.canonical_id)
            elif instruction_kind == "rename_display_name":
                entity.name_cn = payload.get("name_cn") or payload.get("display_name") or entity.name_cn
        return
    if row.kind == "property":
        entity, definition = _legacy_property_parent(db, ontology_id, row.resource_id)
        if not entity or not definition:
            return
        definitions = copy.deepcopy((entity.properties or {}).get("property_definitions", [])) if isinstance(entity.properties, dict) else []
        prop_id = str(definition.get("id") or definition.get("name"))
        for item in definitions:
            if str(item.get("id") or item.get("name")) != prop_id:
                continue
            if instruction_kind == "drop_property":
                definitions.remove(item)
            elif instruction_kind == "rename_api_name":
                item["api_name"] = str(payload.get("new_api_name") or payload.get("api_name") or item.get("api_name") or item.get("id"))
            elif instruction_kind == "cast_property":
                item["type"] = payload.get("to_type") or payload.get("base_type") or item.get("type")
            elif instruction_kind == "replace_source":
                item["source_field"] = payload.get("source_field") or item.get("source_field")
            elif instruction_kind == "rename_display_name":
                display_name = payload.get("display_name") or payload.get("name") or item.get("name")
                item["name"] = display_name
                if "label" in item:
                    item["label"] = display_name
            break
        entity.properties = {**(entity.properties if isinstance(entity.properties, dict) else {}), "property_definitions": definitions}
        return
    if row.kind == "link_type":
        relation = _legacy_relation_for_resource(db, ontology_id, row.resource_id)
        if relation:
            if instruction_kind == "rename_api_name":
                relation.type = str(payload.get("new_api_name") or payload.get("api_name") or relation.type)
                relation.properties = {**(relation.properties or {}), "api_name": relation.type}
            elif instruction_kind == "rename_display_name":
                relation.properties = {**(relation.properties or {}), "name": payload.get("display_name") or payload.get("name") or relation.properties.get("name")}
        return
    if row.kind == "logic_rule":
        legacy_rule_id = str((row.constraints_json or {}).get("legacy_rule_id") or row.resource_id)
        rule = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id, LogicRule.id == legacy_rule_id).first()
        if rule and instruction_kind == "rename_display_name":
            rule.name_cn = payload.get("display_name") or payload.get("name") or rule.name_cn


def apply_plan(db: Session, plan_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    plan = db.query(SchemaMigrationPlan).filter(SchemaMigrationPlan.id == plan_id).with_for_update().first()
    if not plan:
        raise SchemaMigrationError("NOT_FOUND", "迁移计划不存在")
    if plan.status not in {"validated", "failed", "running"}:
        raise SchemaMigrationError("MIGRATION_STATE", f"当前迁移状态不能应用：{plan.status}")
    base = db.query(OntologyRevision).filter(OntologyRevision.id == plan.base_revision_id).first()
    current = _current_revision(db, plan.ontology_id)
    if not base or current.id != base.id:
        raise SchemaMigrationError("REVISION_CONFLICT", "本体当前修订已变化，请重新生成迁移预检")
    plan.status = "running"
    plan.phase = "shadow_materialization"
    plan.error = None
    run = db.query(SchemaMigrationRun).filter(SchemaMigrationRun.plan_id == plan.id).order_by(SchemaMigrationRun.created_at.desc()).first()
    if run:
        run.status = "running"
        run.phase = "shadow_materialization"
        run.progress = 10
        run.started_at = run.started_at or _now()
    # Persist the resumable checkpoint before any object-store or graph side
    # effects. A process restart can safely replay the same shadow namespace.
    db.commit()
    try:
        target = _clone_revision(db, base, shadow_namespace=plan.shadow_namespace or f"schema-shadow:{plan.id}")
        journal = copy.deepcopy(plan.execution_json or {})
        journal.setdefault("instance_rows", {})
        journal.setdefault("scenario_changes", [])
        journal.setdefault("projection_rebuilds", [])
        journal.setdefault("changed_property_values", 0)
        instructions = db.query(SchemaMigrationInstruction).filter(SchemaMigrationInstruction.plan_id == plan.id).order_by(SchemaMigrationInstruction.sequence_no.asc()).all()
        versions = {row.resource_id: row for row in db.query(OntologySemanticResourceVersion).filter(OntologySemanticResourceVersion.revision_id == target.id).all()}
        for item in instructions:
            impact_item = next((entry for entry in (plan.impact_json or {}).get("items", []) if int(entry.get("sequence_no", -1)) == int(item.sequence_no)), {})
            if impact_item.get("blockers"):
                raise SchemaMigrationError("MIGRATION_BLOCKED", "迁移仍有未处理的依赖引用", details={"blockers": impact_item["blockers"], "instruction_id": item.id})
            row = versions.get(item.resource_id) if item.resource_id else None
            payload = item.payload_json or {}
            if item.instruction_kind == "rebuild_projection":
                rebuild_result = _rebuild_graph_projection(db, plan.ontology_id, target.graph_namespace or str(plan.shadow_namespace))
                journal["projection_rebuilds"].append(rebuild_result)
                target.summary = {
                    **(target.summary or {}),
                    "plan_a_projection_rebuild": rebuild_result,
                }
                item.status = "applied"
                continue
            if item.instruction_kind == "move_edits":
                moved = _apply_move_edits(db, plan.ontology_id, payload)
                journal["scenario_changes"].extend(moved["scenarios"])
                item.status = "applied"
                continue
            if not row:
                raise SchemaMigrationError("MIGRATION_TARGET_NOT_FOUND", "影子修订中找不到迁移目标")
            _apply_legacy_projection_for_instruction(db, plan.ontology_id, row, item.instruction_kind, payload)
            if item.instruction_kind == "rename_api_name":
                new_name = str(payload.get("new_api_name") or payload.get("api_name") or "")
                if not SAFE_API_NAME.fullmatch(new_name):
                    raise SchemaMigrationError("INVALID_API_NAME", "新的 API name 无效")
                row.api_name = new_name
                resource = db.query(OntologySemanticResource).filter(OntologySemanticResource.id == row.resource_id).first()
                if resource:
                    resource.api_name = new_name
            elif item.instruction_kind == "rename_display_name":
                row.display_name = str(payload.get("display_name") or payload.get("name") or row.display_name or row.api_name)
                row.name_cn = payload.get("name_cn", row.name_cn)
                row.name_en = payload.get("name_en", row.name_en)
            elif item.instruction_kind == "cast_property":
                journal["changed_property_values"] += _transform_property_values(
                    db, plan.ontology_id, row, mode="cast",
                    target_type=str(payload.get("to_type") or payload.get("base_type") or "string"),
                    backups=journal["instance_rows"],
                )
                row.base_type = str(payload.get("to_type") or payload.get("base_type"))
            elif item.instruction_kind == "drop_property":
                journal["changed_property_values"] += _transform_property_values(
                    db, plan.ontology_id, row, mode="drop", backups=journal["instance_rows"],
                )
                db.delete(row)
                resource = db.query(OntologySemanticResource).filter(OntologySemanticResource.id == row.resource_id).first()
                if resource:
                    resource.retired_in_revision_id = target.id
            elif item.instruction_kind == "replace_source":
                merged = dict(row.provenance_json or {})
                merged.update({"source_dataset_id": payload.get("source_dataset_id"), "source_version_id": payload.get("source_version_id"), "source_field": payload.get("source_field")})
                row.provenance_json = merged
                row.source_field = str(payload["source_field"])
                replaced_mappings = db.query(OntologySemanticResourceVersion).filter(
                    OntologySemanticResourceVersion.revision_id == target.id,
                    OntologySemanticResourceVersion.kind == "source_mapping",
                ).all()
                for mapping_version in replaced_mappings:
                    if str((mapping_version.metadata_json or {}).get("mapped_resource_id") or "") != str(row.resource_id):
                        continue
                    mapping_resource = db.query(OntologySemanticResource).filter(
                        OntologySemanticResource.id == mapping_version.resource_id,
                    ).first()
                    if mapping_resource:
                        mapping_resource.retired_in_revision_id = target.id
                    db.delete(mapping_version)
                db.query(OntologySourceMapping).filter(
                    OntologySourceMapping.revision_id == target.id,
                    OntologySourceMapping.resource_id == row.resource_id,
                ).delete(synchronize_session=False)
                evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
                db.add(OntologySourceMapping(
                    id=str(uuid.uuid4()), ontology_id=plan.ontology_id,
                    revision_id=target.id, resource_id=row.resource_id,
                    source_dataset_id=payload.get("source_dataset_id"),
                    source_version_id=payload.get("source_version_id"),
                    source_table=payload.get("source_table"),
                    source_field=str(payload["source_field"]),
                    mapping_kind=str(payload.get("mapping_kind") or "field"),
                    mapping_status="confirmed",
                    evidence_json={**evidence, "migration_plan_id": plan.id, "replaced": True},
                ))
            item.status = "applied"
        db.flush()
        # Recompute both canonical and compatibility snapshots only after all
        # instructions have succeeded.  The legacy projection is updated in
        # the same transaction, so a reader never sees a new pointer with an
        # old Entity/Relation/LogicRule snapshot.
        compatibility_snapshot = revision_service.snapshot_ontology(db, plan.ontology_id)
        compatibility_payload = json.dumps(compatibility_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        compatibility_hash = hashlib.sha256(compatibility_payload).hexdigest()
        target.snapshot_json = compatibility_snapshot
        target.snapshot_hash = compatibility_hash
        target.snapshot_uri = revision_service.get_storage_service().put_bytes(
            "curated-datasets",
            f"ontologies/{plan.ontology_id}/revisions/{target.revision_no}-{compatibility_hash[:12]}.json",
            compatibility_payload,
            content_type="application/json",
        )
        remaining = db.query(OntologySemanticResourceVersion).filter(OntologySemanticResourceVersion.revision_id == target.id).all()
        target.metadata_digest = _digest(remaining)
        target.metadata_schema_version = "semantic-core-v1"
        build_dependencies(db, plan.ontology_id, target.id)
        target.status = "current"
        old_current = db.query(OntologyRevision).filter(OntologyRevision.ontology_id == plan.ontology_id, OntologyRevision.is_current.is_(True)).all()
        for old in old_current:
            old.is_current = False
            if old.status == "current":
                old.status = "superseded"
        target.is_current = True
        project = db.query(OntologyProject).filter(OntologyProject.id == plan.ontology_id).first()
        project.current_revision_id = target.id
        project.version = f"r{target.revision_no}"
        plan.target_revision_id = target.id
        plan.status = "applied"
        plan.phase = "pointer_switch"
        plan.result_json = {
            "target_revision_id": target.id,
            "metadata_digest": target.metadata_digest,
            "resource_count": len(remaining),
            "shadow_namespace": target.graph_namespace,
            "changed_property_value_count": journal["changed_property_values"],
            "moved_edit_scenario_count": len(journal["scenario_changes"]),
            "projection_rebuilds": journal["projection_rebuilds"],
        }
        plan.execution_json = journal
        plan.completed_at = _now()
        if run:
            run.status = "completed"
            run.phase = "pointer_switch"
            run.progress = 100
            run.target_revision_id = target.id
            run.result_json = plan.result_json
            run.completed_at = plan.completed_at
        db.commit()
        db.refresh(plan)
        result = _serialize_plan(plan, instructions)
        result["run"] = _serialize_run(run)
        return result
    except Exception as exc:
        db.rollback()
        plan = db.query(SchemaMigrationPlan).filter(SchemaMigrationPlan.id == plan_id).first()
        if plan:
            plan.status = "failed"
            plan.phase = "failed"
            plan.error = str(exc)[:1000]
            run = db.query(SchemaMigrationRun).filter(SchemaMigrationRun.plan_id == plan.id).order_by(SchemaMigrationRun.created_at.desc()).first()
            if run:
                run.status = "failed"
                run.phase = "failed"
                run.error = str(exc)[:1000]
            db.commit()
        if isinstance(exc, SchemaMigrationError):
            raise
        raise SchemaMigrationError("MIGRATION_FAILED", str(exc)[:1000]) from exc


def reconcile_plan(db: Session, plan_id: str) -> dict[str, Any]:
    plan = _resolve_plan(db, plan_id)
    if not plan:
        raise SchemaMigrationError("NOT_FOUND", "迁移计划不存在")
    if not plan.target_revision_id:
        if plan.status == "running":
            return apply_plan(db, plan.id, user_id=plan.created_by)
        result = _serialize_plan(plan, db.query(SchemaMigrationInstruction).filter(SchemaMigrationInstruction.plan_id == plan.id).all())
        result["run"] = _serialize_run(db.query(SchemaMigrationRun).filter(SchemaMigrationRun.plan_id == plan.id).order_by(SchemaMigrationRun.created_at.desc()).first())
        return result
    target = db.query(OntologyRevision).filter(OntologyRevision.id == plan.target_revision_id).first()
    current = _current_revision(db, plan.ontology_id)
    if not target or current.id != target.id:
        raise SchemaMigrationError("RECONCILE_TARGET_NOT_CURRENT", "迁移目标修订不是当前修订，无法确认切换状态")
    versions = db.query(OntologySemanticResourceVersion).filter(OntologySemanticResourceVersion.revision_id == target.id).all()
    actual_digest = _digest(versions)
    if target.metadata_digest and actual_digest != target.metadata_digest:
        raise SchemaMigrationError("RECONCILE_DIGEST_MISMATCH", "当前本体元数据校验和与目标修订不一致")
    rebuilt = (plan.execution_json or {}).get("projection_rebuilds") or []
    projection_ok = True
    if rebuilt:
        from app.services.v2.graph.falkordb_service import FalkorDBService

        projection_ok = FalkorDBService().graph_exists(plan.ontology_id, target.graph_namespace)
        if not projection_ok:
            raise SchemaMigrationError("RECONCILE_PROJECTION_MISSING", "迁移记录要求图投影存在，但 FalkorDB 中没有找到目标投影")
    plan.result_json = {
        **(plan.result_json or {}),
        "reconciled": True,
        "semantic_resource_count": len(versions),
        "metadata_digest_verified": actual_digest,
        "projection_verified": projection_ok if rebuilt else None,
    }
    plan.phase = "reconciled"
    db.commit()
    instructions = db.query(SchemaMigrationInstruction).filter(SchemaMigrationInstruction.plan_id == plan.id).order_by(SchemaMigrationInstruction.sequence_no.asc()).all()
    result = _serialize_plan(plan, instructions)
    result["run"] = _serialize_run(db.query(SchemaMigrationRun).filter(SchemaMigrationRun.plan_id == plan.id).order_by(SchemaMigrationRun.created_at.desc()).first())
    return result


def revert_plan(db: Session, plan_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    plan = _resolve_plan(db, plan_id)
    if not plan or not plan.target_revision_id:
        raise SchemaMigrationError("NOT_FOUND", "没有可撤回的迁移结果")
    current = _current_revision(db, plan.ontology_id)
    if current.id != plan.target_revision_id:
        raise SchemaMigrationError("REVISION_CONFLICT", "当前本体已基于其他修订继续变化，请不要静默覆盖")
    base = db.query(OntologyRevision).filter(OntologyRevision.id == plan.base_revision_id).first()
    if not base:
        raise SchemaMigrationError("NOT_FOUND", "迁移基线修订不存在")
    # Restore the compatibility read projection as well as the canonical
    # pointer.  Without this step an old semantic revision would be current
    # while legacy Entity/Relation/LogicRule readers still saw migrated data.
    try:
        revision_service.materialize_snapshot(db, plan.ontology_id, base.snapshot_json or {})
    except ValueError as exc:
        db.rollback()
        raise SchemaMigrationError("REVERT_BLOCKED", str(exc)) from exc
    journal = plan.execution_json or {}
    for backup in (journal.get("instance_rows") or {}).values():
        instance = db.query(EntityInstance).filter(EntityInstance.id == backup.get("instance_id")).first()
        if instance:
            instance.row_data = copy.deepcopy(backup.get("row_data") or {})
    for changed in reversed(journal.get("scenario_changes") or []):
        scenario = db.query(WhatIfScenario).filter(WhatIfScenario.id == changed.get("scenario_id")).first()
        if scenario:
            before = changed.get("before") or {}
            scenario.assumptions_json = copy.deepcopy(before.get("assumptions_json") or [])
            scenario.rule_overrides_json = copy.deepcopy(before.get("rule_overrides_json") or {})
    restored = _clone_revision(db, base, shadow_namespace=base.graph_namespace or f"schema-revert:{plan.id}", status="current")
    # Resource identity rows are shared across revisions.  A migration may
    # have changed an API name or marked a dropped resource as retired; point
    # restoration must put those shared flags back in sync with the restored
    # immutable definitions instead of leaving a current version whose global
    # identity still describes the abandoned target revision.
    restored_versions = db.query(OntologySemanticResourceVersion).filter(
        OntologySemanticResourceVersion.revision_id == restored.id,
    ).all()
    restored_by_resource = {row.resource_id: row for row in restored_versions}
    for resource in db.query(OntologySemanticResource).filter(
        OntologySemanticResource.ontology_id == plan.ontology_id,
    ).all():
        version = restored_by_resource.get(resource.id)
        if version:
            resource.api_name = version.api_name
            resource.retired_in_revision_id = None
    restored_snapshot = revision_service.snapshot_ontology(db, plan.ontology_id)
    restored_payload = json.dumps(restored_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    restored_hash = hashlib.sha256(restored_payload).hexdigest()
    restored.snapshot_json = restored_snapshot
    restored.snapshot_hash = restored_hash
    restored.snapshot_uri = revision_service.get_storage_service().put_bytes(
        "curated-datasets",
        f"ontologies/{plan.ontology_id}/revisions/{restored.revision_no}-{restored_hash[:12]}.json",
        restored_payload,
        content_type="application/json",
    )
    restored.metadata_digest = _digest(db.query(OntologySemanticResourceVersion).filter(OntologySemanticResourceVersion.revision_id == restored.id).all())
    build_dependencies(db, plan.ontology_id, restored.id)
    old = db.query(OntologyRevision).filter(OntologyRevision.ontology_id == plan.ontology_id, OntologyRevision.is_current.is_(True)).all()
    for row in old:
        row.is_current = False
        if row.status == "current":
            row.status = "superseded"
    restored.is_current = True
    project = db.query(OntologyProject).filter(OntologyProject.id == plan.ontology_id).first()
    project.current_revision_id = restored.id
    project.version = f"r{restored.revision_no}"
    plan.status = "reverted"
    plan.phase = "reverted"
    plan.result_json = {**(plan.result_json or {}), "reverted_to_revision_id": restored.id, "reverted_by": user_id}
    plan.completed_at = _now()
    # Keep a durable, user-visible audit record without changing the source
    # revision itself.  The current pointer is the newly materialized clone.
    from app.models.v2.dynamic_ontology import OntologyChange
    db.add(OntologyChange(
        id=str(uuid.uuid4()), ontology_id=plan.ontology_id,
        base_revision_id=current.id, result_revision_id=restored.id,
        target_kind="schema_migration", operation="revert", target_id=plan.id,
        before_json={"revision_id": current.id}, after_json={"revision_id": restored.id},
        impact_json={"reverted_plan_id": plan.id, "base_revision_id": base.id},
        validation_json={"ok": True}, status="applied", created_by=user_id,
    ))
    db.commit()
    instructions = db.query(SchemaMigrationInstruction).filter(SchemaMigrationInstruction.plan_id == plan.id).order_by(SchemaMigrationInstruction.sequence_no.asc()).all()
    result = _serialize_plan(plan, instructions)
    result["run"] = _serialize_run(db.query(SchemaMigrationRun).filter(SchemaMigrationRun.plan_id == plan.id).order_by(SchemaMigrationRun.created_at.desc()).first())
    return result


__all__ = ["SchemaMigrationError", "dry_run", "apply_plan", "reconcile_plan", "revert_plan", "build_dependencies"]
