"""v2 Graph API — Nano schema compatibility plus FalkorDB instances"""
from __future__ import annotations
from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.deps import get_current_user, get_db
from app.database import SessionLocal
from app.models.user import User
from app.models.ontology import OntologyProject
from app.services.v2.authorization_service import AuthorizationContext, cached_permission_digest, load_applicable_policies, redact_link_record, resolve_projection, redact_record
from app.services.v2.data_plane_service import AdapterCapabilities, build_context, make_manifest
from app.services.v2.semantic_core_service import semantic_schema

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_neo4j():
    from app.services.v2.graph.neo4j_service import Neo4jService
    return Neo4jService()


def get_falkordb():
    from app.services.v2.graph.falkordb_service import FalkorDBService
    return FalkorDBService()


def _property_definitions(entity: Any) -> list[dict[str, Any]]:
    """Read both the v2 property contract and legacy entity JSON safely."""
    raw = entity.properties or {}
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        definitions = raw.get("property_definitions") or raw.get("properties")
        if isinstance(definitions, list):
            return [item for item in definitions if isinstance(item, dict)]
        # Legacy schemas use a {field: type/value} object.  Keep it visible
        # rather than rendering an empty inspector for older ontologies.
        return [
            {"id": str(key), "name": str(key), "label": str(key), "type": "string", "example": value}
            for key, value in raw.items()
            if key not in {"schema_version", "source_fields", "evidence", "color", "icon", "data_class"}
        ]
    return []


def _result_manifest(db: Session, ontology_id: str, *, user: User | None, adapter: str, adapter_version: str, status: str = "ready", result_count: int | None = None, warnings: list[str] | None = None) -> dict[str, Any]:
    """Build the common result envelope used by every visible graph query."""
    if not hasattr(db, "query"):
        # Direct service-level callers (and older tests) invoke route
        # functions without FastAPI dependency injection.  Keep the adapter
        # response useful without trying to resolve a metadata revision from
        # the ``Depends`` sentinel.
        return {
            "context_id": f"direct:{ontology_id}",
            "ontology_id": ontology_id,
            "metadata_revision_id": None,
            "metadata_digest": None,
            "permission_digest": None,
            "executor_version": "ontology-workbench-v2",
            "consistency": {"mode": "live", "snapshot_id": None, "allow_degraded": False},
            "adapter": adapter,
            "adapter_version": adapter_version,
            "status": status,
            "redacted_fields": [],
            "warnings": warnings or [],
            "result_count": result_count,
            "generated_at": None,
        }
    schema = semantic_schema(db, ontology_id)
    permission_digest = cached_permission_digest(db, ontology_id, str(user.id)) if isinstance(user, User) else None
    if isinstance(user, User):
        permission_digest = permission_digest or resolve_projection(
            db,
            AuthorizationContext(
                ontology_id=ontology_id,
                principal_id=str(user.id),
                principal_role=user.role,
                metadata_revision_id=schema.get("revision_id"),
            ),
        ).permission_digest
    context = build_context(
        ontology_id,
        metadata_revision_id=schema.get("revision_id"),
        metadata_digest=schema.get("metadata_digest"),
        permission_digest=permission_digest,
    )
    capabilities = AdapterCapabilities(
        adapter=adapter,
        adapter_version=adapter_version,
        supports_live=True,
        supports_pinned=True,
        supports_snapshot=True,
        supports_object_filter=True,
        supports_field_filter=True,
        supports_temporal_facts=adapter in {"postgres-temporal", "falkordb"},
        supports_aggregate=True,
        semantic_fallback_group=adapter,
    )
    return make_manifest(
        context,
        capabilities,
        status=status,
        permission_digest=permission_digest,
        warnings=warnings,
        result_count=result_count,
    ).as_dict()


def _authorize_graph_data(db: Session, ontology_id: str, data: dict[str, Any], user: User | None) -> dict[str, Any]:
    """Apply one object/property projection to a graph adapter response."""
    if not isinstance(user, User):
        return data
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    from app.models.entity import Entity

    semantic_id_by_legacy = {
        str(entity_id): str(resource_id)
        for entity_id, resource_id in db.query(Entity.id, Entity.semantic_resource_id).filter(
            Entity.ontology_id == ontology_id,
        ).all()
        if resource_id
    }
    nodes = list(data.get("nodes") or [])
    node_records = []
    object_types = []
    for node in nodes:
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        object_type = str(node.get("entity_id") or node.get("entity_type") or props.get("entity_id") or props.get("entity_type") or (node.get("labels") or ["Entity"])[0])
        object_types.append(object_type)
        node_records.append({
            "id": node.get("id"),
            "entity_id": object_type,
            "entity_type": object_type,
            "semantic_resource_id": node.get("semantic_resource_id") or props.get("semantic_resource_id") or semantic_id_by_legacy.get(object_type),
            "properties": props,
        })
    projection = resolve_projection(
        db,
        AuthorizationContext(
            ontology_id=ontology_id,
            principal_id=str(user.id),
            principal_role=user.role,
            metadata_revision_id=project.current_revision_id if project else None,
        ),
        records=node_records,
    )
    visible_nodes: list[dict[str, Any]] = []
    visible_ids: set[str] = set()
    for node, object_type, record in zip(nodes, object_types, node_records):
        props = record["properties"]
        redacted = redact_record(projection, record)
        if redacted is None:
            continue
        next_node = dict(node)
        next_node["properties"] = redacted.get("properties", props)
        visible_nodes.append(next_node)
        visible_ids.add(str(node.get("id")))
    edges = []
    for edge in (data.get("edges") or []):
        if str(edge.get("source")) not in visible_ids or str(edge.get("target")) not in visible_ids:
            continue
        # Link policies are evaluated against the already-visible endpoint
        # set. Relationships remain default-deny unless explicitly allowed;
        # endpoint visibility alone must not disclose an edge.
        link_properties = edge.get("properties") if isinstance(edge.get("properties"), dict) else {}
        link_id = str(link_properties.get("semantic_resource_id") or link_properties.get("api_name") or edge.get("type") or edge.get("label") or "")
        link_record = {"id": edge.get("id"), "link_type_id": link_id, "type": edge.get("type"), "properties": link_properties}
        redacted_link = redact_link_record(projection, link_record, link_type_id=link_id)
        if redacted_link is None:
            continue
        next_edge = dict(edge)
        next_edge["properties"] = redacted_link.get("properties", link_properties)
        edges.append(next_edge)
    data = {**data, "nodes": visible_nodes, "edges": edges}
    unrestricted_subject = bool(project and (str(project.created_by) == str(user.id) or str(user.role or "") == "admin"))
    if not unrestricted_subject:
        # Adapter totals may include hidden rows.  Do not leak them through
        # pagination metadata; a caller can ask for another page explicitly.
        data["total_instances"] = len(visible_nodes)
        data["total_edges"] = len(edges)
    data["summary"] = {**(data.get("summary") or {}), "instance_count": len([node for node in visible_nodes if node.get("node_kind") == "instance"]), "relationship_count": len(edges)}
    return data


def _canonical_ontology_data(db: Session, ontology_id: str, *, limit: int = 200, user: User | None = None) -> dict[str, Any]:
    """Return the published ontology vocabulary and inspector data.

    The workbench intentionally reads this plane directly instead of choosing
    a graph-store implementation.  FalkorDB remains useful for dense internal
    traversal, but the user-visible ontology must be complete whenever its
    construction transaction has been published.
    """
    from app.models.entity import Entity
    from app.models.entity_instance import EntityInstance
    from app.models.logic import LogicRule
    from app.models.relation import Relation
    from app.models.v2.construction import EvidenceRef

    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    shared_context = None
    shared_policies = None
    type_projection = None
    if isinstance(user, User):
        shared_context = AuthorizationContext(
            ontology_id=ontology_id,
            principal_id=str(user.id),
            principal_role=user.role,
            metadata_revision_id=project.current_revision_id if project else None,
        )
        shared_policies = load_applicable_policies(db, shared_context)
    entities = (
        db.query(Entity)
        .filter(Entity.ontology_id == ontology_id)
        .order_by(Entity.name_cn.asc(), Entity.name_en.asc())
        .limit(limit)
        .all()
    )
    if isinstance(user, User):
        type_records = [{
                "id": entity.id,
                "entity_id": entity.id,
                "entity_type": entity.id,
                "semantic_resource_id": entity.semantic_resource_id,
                "properties": {"name": entity.name_cn or entity.name_en or entity.id},
            } for entity in entities]
        type_projection = resolve_projection(
            db,
            shared_context,
            records=type_records,
            preloaded_policies=shared_policies,
        )
        visible_entities = []
        for entity, record in zip(entities, type_records):
            if redact_record(type_projection, record) is not None:
                visible_entities.append(entity)
        entities = visible_entities
    entity_ids = [item.id for item in entities]
    semantic_id_by_entity = {
        str(entity.id): entity.semantic_resource_id
        for entity in entities
        if entity.semantic_resource_id
    }
    instance_rows = []
    if entity_ids:
        instance_rows = db.query(EntityInstance).filter(
            EntityInstance.ontology_id == ontology_id,
            EntityInstance.entity_id.in_(entity_ids),
        ).all()
    instances_by_entity: dict[str, list[Any]] = defaultdict(list)
    instance_ids: list[str] = []
    redacted_instance_data: dict[str, dict[str, Any]] = {}
    for item in instance_rows:
        instances_by_entity[item.entity_id].append(item)
        instance_ids.append(item.id)
    if isinstance(user, User):
        instance_records = [{
            "id": row.id,
            "entity_id": row.entity_id,
            "entity_type": row.entity_id,
            "semantic_resource_id": row.object_type_resource_id or semantic_id_by_entity.get(str(row.entity_id)),
            "properties": row.row_data or {},
        } for row in instance_rows]
        instance_projection = resolve_projection(
            db,
            shared_context,
            records=instance_records,
            preloaded_policies=shared_policies,
        )
        visible_rows = []
        for row, record in zip(instance_rows, instance_records):
            redacted = redact_record(instance_projection, record)
            if redacted is not None:
                visible_rows.append(row)
                redacted_instance_data[str(row.id)] = redacted.get("properties", row.row_data or {})
        instances_by_entity = defaultdict(list)
        for row in visible_rows:
            instances_by_entity[row.entity_id].append(row)
        instance_rows = [row for rows in instances_by_entity.values() for row in rows]
        instance_ids = [row.id for row in instance_rows]
    evidence_by_assertion: Counter[str] = Counter()
    if instance_ids:
        for assertion_id, count in (
            db.query(EvidenceRef.assertion_id, func.count(EvidenceRef.id))
            .filter(EvidenceRef.ontology_id == ontology_id, EvidenceRef.assertion_id.in_(instance_ids))
            .group_by(EvidenceRef.assertion_id)
            .all()
        ):
            evidence_by_assertion[str(assertion_id)] = int(count)

    relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
    rules = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).order_by(LogicRule.name_cn.asc()).all()
    node_ids = set(entity_ids)
    nodes: list[dict[str, Any]] = []
    for entity in entities:
        raw = entity.properties or {}
        properties = _property_definitions(entity)
        examples = [redacted_instance_data.get(str(item.id), item.row_data or {}) for item in instances_by_entity.get(entity.id, [])[:3]]
        evidence_count = sum(evidence_by_assertion.get(item.id, 0) for item in instances_by_entity.get(entity.id, []))
        nodes.append({
            "id": entity.id,
            "labels": ["EntityType"],
            "entity_type": "EntityType",
            "properties": {
                "id": entity.id,
                "name": entity.name_cn or entity.name_en or entity.id,
                "name_cn": entity.name_cn or "",
                "name_en": entity.name_en or "",
                "description": entity.description or "",
                "confidence": entity.confidence if entity.confidence is not None else 1.0,
                "source_fields": raw.get("source_fields", []) if isinstance(raw, dict) else [],
                "evidence": raw.get("evidence", {}) if isinstance(raw, dict) else {},
                "property_definitions": properties,
                "instance_count": len(instances_by_entity.get(entity.id, [])),
                "instance_examples": examples,
                "evidence_count": evidence_count,
            },
        })
    edges = [
        {
            "id": relation.id,
            "source": relation.source_entity,
            "target": relation.target_entity,
            "type": relation.type or "关联",
            "label": (relation.properties or {}).get("name") or relation.type or "关联",
            "properties": {
                **(relation.properties or {}),
                "name": (relation.properties or {}).get("name") or relation.type or "关联",
                "cardinality": (relation.properties or {}).get("cardinality") or "one-to-many",
                "confidence": relation.confidence if relation.confidence is not None else 1.0,
            },
        }
        for relation in relations
        if relation.source_entity in node_ids and relation.target_entity in node_ids
    ]
    if isinstance(user, User) and type_projection is not None and not type_projection.unrestricted:
        visible_edges = []
        for edge in edges:
            link_properties = edge.get("properties") if isinstance(edge.get("properties"), dict) else {}
            link_id = str(link_properties.get("semantic_resource_id") or link_properties.get("api_name") or edge.get("type") or "")
            redacted_link = redact_link_record(
                type_projection,
                {"id": edge.get("id"), "link_type_id": link_id, "type": edge.get("type"), "properties": link_properties},
                link_type_id=link_id,
            )
            if redacted_link is None:
                continue
            visible_edge = dict(edge)
            visible_edge["properties"] = redacted_link.get("properties", link_properties)
            if visible_edge["properties"].get("name") is None:
                visible_edge["label"] = visible_edge.get("type")
            else:
                visible_edge["label"] = visible_edge["properties"].get("name")
            visible_edges.append(visible_edge)
        edges = visible_edges
    property_count = sum(len(_property_definitions(entity)) for entity in entities)
    unrestricted_subject = not isinstance(user, User) or (
        str(getattr(db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first(), "created_by", "")) == str(user.id)
        or str(user.role or "") == "admin"
    )
    if isinstance(user, User) and not unrestricted_subject and type_projection is not None:
        visible_property_count = 0
        visible_definitions_by_entity: dict[str, list[dict[str, Any]]] = {}
        entities_by_id = {str(entity.id): entity for entity in entities}
        for entity in entities:
            properties = _property_definitions(entity)
            allowed = type_projection.allowed_fields_by_object.get(str(entity.id), type_projection.allowed_fields)
            denied = type_projection.denied_fields | type_projection.denied_fields_by_object.get(str(entity.id), set())
            visible_properties = []
            for prop in properties:
                key = str(prop.get("api_name") or type_projection.resource_aliases.get(str(prop.get("resource_id") or "")) or prop.get("id") or prop.get("name") or "")
                key = type_projection.resource_aliases.get(key, key)
                if key and (allowed is None or key in allowed) and key not in denied:
                    visible_properties.append(prop)
            visible_definitions_by_entity[str(entity.id)] = visible_properties
            visible_property_count += len(visible_properties)
        property_count = visible_property_count
        # There is not yet a dedicated logic-rule policy scope. Fail closed
        # for non-owners rather than leaking formulas, conditions or evidence.
        rules = []
        for node in nodes:
            allowed = type_projection.allowed_fields_by_object.get(str(node["id"]), type_projection.allowed_fields)
            denied = type_projection.denied_fields | type_projection.denied_fields_by_object.get(str(node["id"]), set())
            entity = entities_by_id.get(str(node["id"]))
            raw = (entity.properties if entity else {}) or {}
            source_fields = raw.get("source_fields", []) if isinstance(raw, dict) else []
            node["properties"]["source_fields"] = [
                field for field in source_fields
                if allowed is None or type_projection.resource_aliases.get(str(field), str(field)) in allowed
            ]
            node["properties"]["source_fields"] = [
                field for field in node["properties"]["source_fields"]
                if type_projection.resource_aliases.get(str(field), str(field)) not in denied
            ]
            node["properties"]["evidence"] = {}
            node["properties"]["property_definitions"] = visible_definitions_by_entity.get(str(node["id"]), [])
    if isinstance(user, User) and not unrestricted_subject:
        visible_assertions = [str(item) for item in entity_ids + instance_ids]
        total_evidence = db.query(EvidenceRef).filter(
            EvidenceRef.ontology_id == ontology_id,
            EvidenceRef.assertion_id.in_(visible_assertions),
        ).count() if visible_assertions else 0
    else:
        total_evidence = db.query(EvidenceRef).filter(EvidenceRef.ontology_id == ontology_id).count()
    result = {
        "ontology_id": ontology_id,
        "nodes": nodes,
        "edges": edges,
        "logic_rules": [
            {
                "id": rule.id,
                "name": rule.name_cn or rule.name_en or rule.id,
                "name_cn": rule.name_cn or "",
                "name_en": rule.name_en or "",
                "description": rule.description or "",
                "formula": rule.formula or "",
                "condition": getattr(rule, "condition_json", None) or {},
                "effect": getattr(rule, "effect_json", None) or {},
                "linked_entities": rule.linked_entities or [],
                "evidence": getattr(rule, "evidence_json", None) or {},
                "confidence": rule.confidence if rule.confidence is not None else 1.0,
                "model_invocation_id": getattr(rule, "model_invocation_id", None),
            }
            for rule in rules
        ],
        "summary": {
            "entity_type_count": len(nodes),
            "property_count": property_count,
            "relationship_count": len(edges),
            "logic_rule_count": len(rules),
            "instance_count": len(instance_rows),
            "evidence_count": total_evidence,
        },
        "available": True,
        "graph_backend": "published-ontology",
    }
    result["result_manifest"] = _result_manifest(
        db,
        ontology_id,
        user=user,
        adapter="postgres-semantic",
        adapter_version="v2",
        result_count=len(instance_rows),
    )
    return result


class CypherRequest(BaseModel):
    query: str
    params: dict = {}


class TemporalImportRequest(BaseModel):
    rows: list[dict] = []
    adapter: str | None = None
    construction_run_id: str | None = None
    time_kind: str = "ordinal"
    sequence_column: str | None = "event_seq"
    event_time_column: str | None = None
    valid_from_column: str | None = None
    valid_to_column: str | None = None
    entity_id_column: str = "unit_id"
    entity_type: str = "Equipment"
    observation_type: str = "SensorReading"
    reading_id_prefix: str = "reading"


@router.get("/{ontology_id}/graph")
def get_graph(
    ontology_id: str,
    # Keep a plain default so direct service-level callers/tests do not receive
    # FastAPI's ``Query`` object; clamp explicitly for both HTTP and Python use.
    limit: int = 200,
    offset: int = Query(0, ge=0),
    label_filter: str | None = None,
    view: str = Query("ontology", pattern="^(ontology|schema|instances)$"),
    entity_type: str | None = None,
    episode_id: str | None = None,
    seq_from: int | None = Query(None, ge=0),
    seq_to: int | None = Query(None, ge=0),
    relation_state: str = Query("all", pattern="^(all|current)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the published ontology, legacy schema graph, or internal instances.

    ``view=instances`` is the teacher-facing path and is served exclusively
    from the per-ontology FalkorDB graph. The default schema view preserves
    existing Nano behavior for older ontologies.
    """
    limit = max(1, min(int(limit), 1000))
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    if view == "ontology":
        if not hasattr(db, "query"):
            owned_db = SessionLocal()
            try:
                return _canonical_ontology_data(owned_db, ontology_id, limit=limit, user=user)
            finally:
                owned_db.close()
        return _canonical_ontology_data(db, ontology_id, limit=limit, user=user)
    if view == "instances":
        from app.models.ontology import OntologyProject
        # Direct Python callers/tests do not have FastAPI dependency
        # resolution and therefore pass the default ``Depends`` sentinel.
        # HTTP requests still receive a real Session and keep the stale-tab
        # protection that prevents a read from creating a phantom graph.
        if hasattr(db, "query") and not db.query(OntologyProject.id).filter(OntologyProject.id == ontology_id).first():
            # Do not let a stale browser tab create an empty FalkorDB graph
            # for a deleted ontology merely by reading the instances view.
            raise HTTPException(404, "Ontology not found")
        svc = get_falkordb()
        if not svc.available:
            data = {
                "nodes": [], "edges": [], "graph_backend": "falkordb",
                "available": False, "error": "FalkorDB unavailable",
            }
            data["result_manifest"] = _result_manifest(db, ontology_id, user=user, adapter="falkordb", adapter_version="v2", status="unavailable", warnings=["FalkorDB unavailable"])
            return data
        try:
            graph_kwargs: dict[str, Any] = {
                "limit": limit,
                "entity_type": entity_type or label_filter,
                "seq_from": seq_from,
                "seq_to": seq_to,
                "relation_state": relation_state,
            }
            # Published dynamic runs are isolated in their own graph
            # namespace.  Keep the legacy instances endpoint pointed at the
            # current immutable snapshot instead of the old ontology graph.
            try:
                from app.models.v2.temporal_replay import DataModelSnapshot
                project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
                snapshot = db.query(DataModelSnapshot).filter(DataModelSnapshot.id == getattr(project, "current_data_snapshot_id", None)).first() if getattr(project, "current_data_snapshot_id", None) else None
                if snapshot:
                    graph_kwargs["replay_id"] = snapshot.replay_id
                    graph_kwargs["graph_namespace"] = snapshot.graph_namespace
            except Exception:
                pass
            if offset:
                graph_kwargs["offset"] = offset
            if episode_id:
                graph_kwargs["episode_id"] = episode_id
            data = svc.get_graph_data(ontology_id, **graph_kwargs)
            data = _authorize_graph_data(db, ontology_id, data, user)
            data["result_manifest"] = _result_manifest(db, ontology_id, user=user, adapter="falkordb", adapter_version="v2", result_count=len(data.get("nodes") or []))
            return data
        except Exception as exc:
            data = {
                "nodes": [], "edges": [], "graph_backend": "falkordb",
                "available": False, "error": str(exc),
            }
            data["result_manifest"] = _result_manifest(db, ontology_id, user=user, adapter="falkordb", adapter_version="v2", status="unavailable", warnings=[str(exc)])
            return data
    svc = get_neo4j()
    if not svc.available:
        data = _sqlite_graph_data(ontology_id, limit=limit, label_filter=label_filter)
        data["graph_backend"] = "sqlite-schema"
        data = _authorize_graph_data(db, ontology_id, data, user)
        data["result_manifest"] = _result_manifest(
            db,
            ontology_id,
            user=user,
            adapter="postgres-semantic",
            adapter_version="v2",
            status="degraded",
            result_count=len(data.get("nodes") or []),
            warnings=["legacy graph adapter unavailable; used relational compatibility projection"],
        )
        return data
    try:
        data = svc.get_graph_data(ontology_id, limit=limit, label_filter=label_filter)
    except Exception:
        # 共享 driver 缓存期间 Neo4j 宕机 → 回退 SQLite 而非 500
        svc.close()
        data = _sqlite_graph_data(ontology_id, limit=limit, label_filter=label_filter)
        data["graph_backend"] = "sqlite-schema"
        data = _authorize_graph_data(db, ontology_id, data, user)
        data["result_manifest"] = _result_manifest(
            db,
            ontology_id,
            user=user,
            adapter="postgres-semantic",
            adapter_version="v2",
            status="degraded",
            result_count=len(data.get("nodes") or []),
            warnings=["legacy graph adapter failed; used relational compatibility projection"],
        )
        return data
    svc.close()
    # Neo4j 可用但该 ontology 无数据（如简易 LLM 路线未同步写入）→ 回退 SQLite
    if not data.get("nodes"):
        data = _sqlite_graph_data(ontology_id, limit=limit, label_filter=label_filter)
        data["graph_backend"] = "sqlite-schema"
        data = _authorize_graph_data(db, ontology_id, data, user)
        data["result_manifest"] = _result_manifest(
            db,
            ontology_id,
            user=user,
            adapter="postgres-semantic",
            adapter_version="v2",
            status="degraded",
            result_count=len(data.get("nodes") or []),
            warnings=["legacy graph adapter returned no nodes; used relational compatibility projection"],
        )
        return data
    data["neo4j_available"] = True
    data["graph_backend"] = "neo4j-legacy"
    data = _authorize_graph_data(db, ontology_id, data, user)
    data["result_manifest"] = _result_manifest(db, ontology_id, user=user, adapter="legacy-neo4j", adapter_version="legacy", result_count=len(data.get("nodes") or []))
    return data


def _data_model_type_groups(
    canonical: dict[str, Any],
    nodes: list[dict[str, Any]],
    type_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Combine the published type catalogue with the currently visible data.

    Entity type identifiers in older FalkorDB runs are human readable names,
    while SQL entities use UUIDs.  ``filter`` deliberately carries the value
    accepted by the instance query so the UI can filter without guessing.
    """
    live_counts = type_counts is not None
    graph_counts = Counter(type_counts) if live_counts else Counter(str(node.get("entity_type") or "Entity") for node in nodes)
    relation_counts: Counter[str] = Counter()
    for edge in canonical.get("edges", []):
        relation_counts[str(edge.get("source") or "")] += 1
        relation_counts[str(edge.get("target") or "")] += 1
    groups: list[dict[str, Any]] = []
    used_filters: set[str] = set()
    for type_node in canonical.get("nodes", []):
        props = type_node.get("properties") or {}
        candidates = [
            props.get("name_en"),
            props.get("name"),
            props.get("name_cn"),
            type_node.get("id"),
        ]
        filter_value = next((str(item) for item in candidates if item and str(item) in graph_counts), None)
        if filter_value is None and candidates:
            filter_value = str(next((item for item in candidates if item), type_node.get("id") or "Entity"))
        if filter_value in used_filters:
            continue
        used_filters.add(filter_value or "")
        groups.append({
            "id": type_node.get("id"),
            "name": props.get("name") or props.get("name_cn") or props.get("name_en") or type_node.get("id"),
            "name_cn": props.get("name_cn") or "",
            "name_en": props.get("name_en") or "",
            "description": props.get("description") or "",
            "filter": filter_value,
            "property_count": int(props.get("property_definitions") and len(props.get("property_definitions") or []) or 0),
            "relationship_count": int(relation_counts.get(str(type_node.get("id") or ""), 0)),
            "instance_count": int(graph_counts.get(filter_value, 0) if live_counts else graph_counts.get(filter_value, props.get("instance_count") or 0)),
            "evidence_count": int(props.get("evidence_count") or 0),
        })
    # A published type may not exist in an old SQL catalogue.  Keep the real
    # instance type visible instead of dropping data from the workbench.
    for filter_value, count in sorted(graph_counts.items()):
        if filter_value in used_filters:
            continue
        groups.append({
            "id": filter_value,
            "name": filter_value,
            "name_cn": "",
            "name_en": filter_value,
            "description": "",
            "filter": filter_value,
            "property_count": 0,
            "relationship_count": 0,
            "instance_count": count,
            "evidence_count": 0,
        })
    return groups


def _augment_multimodal_data_model(
    db: Session,
    ontology_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Add sample-to-asset evidence edges to legacy multimodal graph data.

    Existing installations wrote media/inspection nodes to FalkorDB but did
    not create a sample node.  The SQL sample is the source of truth, so this
    small projection repairs the visible relationship without mutating the
    historical graph or duplicating media bytes.
    """
    from app.models.v2.dataset import MediaItem, MultimodalSample
    from app.models.v2.construction import EvidenceRef

    nodes = list(data.get("nodes") or [])
    edges = list(data.get("edges") or [])
    media_nodes = [node for node in nodes if (node.get("node_kind") == "instance" and (node.get("entity_type") in {"MediaAsset", "MediaItem", "media"} or (node.get("properties") or {}).get("sample_id")))]
    sample_ids = {
        str((node.get("properties") or {}).get("sample_id"))
        for node in media_nodes
        if (node.get("properties") or {}).get("sample_id")
    }
    if not sample_ids:
        sample_ids = {
            str(value)
            for (value,) in db.query(EvidenceRef.source_sample_id)
            .filter(EvidenceRef.ontology_id == ontology_id, EvidenceRef.source_sample_id.isnot(None))
            .distinct()
            .all()
        }
    if not sample_ids:
        return data
    samples = db.query(MultimodalSample).filter(MultimodalSample.id.in_(sample_ids)).all()
    sample_by_id = {str(sample.id): sample for sample in samples}
    media_ids = {str(node.get("id")) for node in media_nodes if node.get("id")}
    media_rows = db.query(MediaItem).filter(MediaItem.sample_id.in_(list(sample_by_id))).all()
    existing_node_ids = {str(node.get("id")) for node in nodes}
    existing_edge_ids = {str(edge.get("id")) for edge in edges}
    for sample_id, sample in sample_by_id.items():
        sample_node_id = f"sample:{sample_id}"
        if sample_node_id not in existing_node_ids:
            nodes.append({
                "id": sample_node_id,
                "labels": ["MultimodalSample"],
                "entity_type": "MultimodalSample",
                "node_kind": "sample",
                "properties": {
                    "sample_id": sample_id,
                    "sample_key": sample.sample_key,
                    "scene_id": sample.scene_id,
                    "split": sample.split,
                    "label": sample.label,
                    "labels": sample.labels or [],
                    "metadata": sample.metadata_json or {},
                },
            })
            existing_node_ids.add(sample_node_id)
    for media in media_rows:
        media_node_id = f"media:{media.id}"
        # Prefer the existing constructed node; otherwise create a compact
        # evidence node that points at the immutable asset record.
        if media_node_id not in existing_node_ids:
            nodes.append({
                "id": media_node_id,
                "labels": ["MediaAsset"],
                "entity_type": "MediaAsset",
                "node_kind": "media",
                "properties": {
                    "media_id": str(media.id),
                    "sample_id": str(media.sample_id),
                    "asset_role": media.asset_role,
                    "media_type": media.media_type,
                    "original_name": media.original_name,
                    "source_path": media.source_path,
                    "checksum": media.checksum,
                    "mime_type": media.mime_type,
                },
            })
            existing_node_ids.add(media_node_id)
        edge_id = f"sample:{media.sample_id}:HAS_ASSET:{media_node_id}"
        if edge_id not in existing_edge_ids:
            edges.append({
                "id": edge_id,
                "source": f"sample:{media.sample_id}",
                "target": media_node_id,
                "type": "HAS_ASSET",
                "label": "包含资产",
                "edge_kind": "evidence",
                "properties": {
                    "asset_role": media.asset_role,
                    "media_type": media.media_type,
                    "checksum": media.checksum,
                    "source_path": media.source_path,
                },
            })
            existing_edge_ids.add(edge_id)
    data["nodes"] = nodes
    data["edges"] = edges
    data["multimodal"] = {
        "sample_count": len(sample_by_id),
        "asset_count": len(media_rows),
        "sample_ids": sorted(sample_by_id),
    }
    return data


@router.get("/{ontology_id}/data-model")
def get_data_model(
    ontology_id: str,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    entity_type: str | None = None,
    episode_id: str | None = None,
    at: str | None = None,
    mode: str = Query("cumulative", pattern="^(cumulative|window)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the real data model (instances + instance relationships).

    The endpoint is intentionally separate from ``/graph``: the latter keeps
    its published vocabulary contract, while this route is paginated and can
    apply temporal position and episode filters without changing the stored
    ontology.
    """
    from app.models.ontology import OntologyProject

    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 200
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    if not isinstance(mode, str) or mode not in {"cumulative", "window"}:
        mode = "cumulative"

    ontology = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not ontology:
        raise HTTPException(404, "本体不存在")
    # A published dynamic stream owns an immutable graph namespace.  Resolve
    # it before querying FalkorDB so the ordinary data-model page does not
    # silently fall back to the legacy ontology graph after a snapshot is
    # published.  Legacy projects have a null pointer and retain old behavior.
    snapshot_namespace: str | None = None
    snapshot_replay_id: str | None = None
    try:
        from app.models.v2.temporal_replay import DataModelSnapshot
        snapshot = db.query(DataModelSnapshot).filter(DataModelSnapshot.id == getattr(ontology, "current_data_snapshot_id", None)).first() if getattr(ontology, "current_data_snapshot_id", None) else None
        if snapshot:
            snapshot_namespace = snapshot.graph_namespace
            snapshot_replay_id = snapshot.replay_id
    except Exception:
        snapshot = None
    canonical = _canonical_ontology_data(db, ontology_id, limit=1000, user=user)
    data_class = str(getattr(ontology, "data_class", None) or "regular")
    service = get_falkordb()
    timeline: dict[str, Any] | None = None
    resolved_at = at
    seq_from: int | None = None
    seq_to: int | None = None
    if data_class == "temporal":
        try:
            try:
                timeline = service.temporal_timeline(ontology_id, episode_id=episode_id, limit=1000, graph_namespace=snapshot_namespace)
            except TypeError:
                timeline = service.temporal_timeline(ontology_id, episode_id=episode_id, limit=1000)
        except Exception as exc:
            timeline = {"available": False, "error": str(exc), "dates": [], "buckets": [], "episodes": []}
        # A published event stream can still provide its Ordinal axis while
        # FalkorDB is offline (or while a legacy client is using the SQL
        # projection).  Keep the timeline truthful instead of disabling the
        # temporal controls just because the optional graph cache is down.
        if snapshot_replay_id and not (timeline or {}).get("dates"):
            try:
                from app.models.v2.temporal_replay import TemporalStreamEvent
                event_query = db.query(
                    TemporalStreamEvent.ordinal,
                    TemporalStreamEvent.episode_id,
                ).filter(
                    TemporalStreamEvent.replay_id == snapshot_replay_id,
                    TemporalStreamEvent.status == "committed",
                )
                if episode_id:
                    event_query = event_query.filter(TemporalStreamEvent.episode_id == episode_id)
                event_rows = event_query.order_by(
                    TemporalStreamEvent.ordinal.asc(),
                    TemporalStreamEvent.source_sequence.asc(),
                ).all()
                ordinal_values = [str(value) for value, _episode in event_rows if value is not None]
                unique_ordinals = list(dict.fromkeys(ordinal_values))
                timeline = {
                    "available": True,
                    "dates": unique_ordinals,
                    "buckets": [{"timestamp": value, "count": ordinal_values.count(value)} for value in unique_ordinals],
                    "episodes": sorted({str(value) for _ordinal, value in event_rows if value is not None}),
                    "graph_namespace": snapshot_namespace,
                }
            except Exception:
                pass
        dates = [str(value) for value in (timeline or {}).get("dates", []) if value is not None]
        if not resolved_at and dates:
            # Ordinal is the only temporal value used by FactoryNet.  Keep it
            # as a string in the API so no date-looking value is fabricated.
            resolved_at = dates[-1]
        try:
            point = int(float(resolved_at)) if resolved_at not in (None, "") else None
        except (TypeError, ValueError):
            point = None
        if point is not None:
            seq_to = point
            if mode == "window":
                seq_from = point
    if not service.available:
        graph_data = None
        # PostgreSQL is authoritative for a published event stream.  Keep
        # the data-model page useful when FalkorDB is temporarily offline by
        # using the same SQL fact projection as the dynamic workbench.
        if snapshot_replay_id:
            try:
                from app.models.v2.temporal_replay import TemporalReplay
                from app.services.v2.temporal_stream_service import stream_graph
                snapshot_replay = db.query(TemporalReplay).filter(TemporalReplay.id == snapshot_replay_id).first()
                if snapshot_replay:
                    graph_data = stream_graph(
                        db,
                        snapshot_replay,
                        limit=limit,
                        offset=offset,
                        entity_type=entity_type,
                        episode_id=episode_id,
                        at=(float(resolved_at) if resolved_at not in (None, "") else None),
                        mode=mode,
                        relation_state="all",
                    )
            except Exception:
                graph_data = None
        if graph_data is None:
            graph_data = {
                "nodes": [],
                "edges": [],
                "returned": 0,
                "total_instances": 0,
                "offset": offset,
                "next_offset": None,
                "available": False,
                "graph_backend": "falkordb",
                "error": "FalkorDB unavailable",
            }
    else:
        try:
            try:
                graph_data = service.get_graph_data(
                    ontology_id,
                    limit=limit,
                    offset=offset,
                    entity_type=entity_type,
                    episode_id=episode_id,
                    seq_from=seq_from,
                    seq_to=seq_to,
                    replay_id=snapshot_replay_id,
                    at=(float(resolved_at) if resolved_at not in (None, "") else None),
                    graph_namespace=snapshot_namespace,
                    mode=mode,
                )
            except TypeError:
                graph_data = service.get_graph_data(
                    ontology_id,
                    limit=limit,
                    offset=offset,
                    entity_type=entity_type,
                    episode_id=episode_id,
                    seq_from=seq_from,
                    seq_to=seq_to,
                    replay_id=snapshot_replay_id,
                    at=(float(resolved_at) if resolved_at not in (None, "") else None),
                    graph_namespace=snapshot_namespace,
                )
        except Exception as exc:
            graph_data = None
            if snapshot_replay_id:
                try:
                    from app.models.v2.temporal_replay import TemporalReplay
                    from app.services.v2.temporal_stream_service import stream_graph
                    snapshot_replay = db.query(TemporalReplay).filter(TemporalReplay.id == snapshot_replay_id).first()
                    if snapshot_replay:
                        graph_data = stream_graph(
                            db,
                            snapshot_replay,
                            limit=limit,
                            offset=offset,
                            entity_type=entity_type,
                            episode_id=episode_id,
                            at=(float(resolved_at) if resolved_at not in (None, "") else None),
                            mode=mode,
                            relation_state="all",
                        )
                except Exception:
                    graph_data = None
            if graph_data is None:
                graph_data = {
                    "nodes": [],
                    "edges": [],
                    "returned": 0,
                    "total_instances": 0,
                    "offset": offset,
                    "next_offset": None,
                    "available": False,
                    "graph_backend": "falkordb",
                    "error": str(exc),
                }
    graph_data = _authorize_graph_data(db, ontology_id, graph_data, user)
    # Attach evidence counts without leaking internal storage keys into the
    # visible property list.  Source fields remain available on selection.
    from app.models.v2.construction import EvidenceRef
    evidence_rows = db.query(EvidenceRef).filter(EvidenceRef.ontology_id == ontology_id).all()
    evidence_by_assertion: Counter[str] = Counter()
    edge_evidence_by_assertion: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in evidence_rows:
        evidence_item = {
            "id": item.id,
            "kind": item.assertion_kind,
            "source_file": item.source_file,
            "source_row_id": item.source_row_id,
            "source_sample_id": item.source_sample_id,
            "source_media_id": item.source_media_id,
            "extractor": item.extractor,
            "model_name": item.model_name,
            "confidence": item.confidence,
            "evidence_text": item.evidence_text,
        }
        if item.assertion_id:
            evidence_by_assertion[str(item.assertion_id)] += 1
            if item.assertion_kind == "edge":
                edge_evidence_by_assertion[str(item.assertion_id)].append(evidence_item)
        for source_key in (item.source_sample_id, item.source_media_id, item.source_row_id):
            if source_key:
                evidence_by_source[str(source_key)].append(evidence_item)
    for node in graph_data.get("nodes", []):
        props = node.setdefault("properties", {})
        node["evidence_count"] = int(evidence_by_assertion.get(str(node.get("id")), 0))
        source_key = props.get("sample_id") or props.get("media_id") or props.get("source_row_id") or node.get("id")
        if source_key and str(source_key) in evidence_by_source:
            node["evidence"] = evidence_by_source[str(source_key)][:8]
        if not node.get("evidence") and evidence_rows:
            # Older construction runs use a readable assertion id rather than
            # the FalkorDB instance id.  Match stable source identifiers such
            # as EQ001/R-EQ001-001 to keep the row/file evidence locatable.
            tokens: set[str] = set()
            if props.get("reading_id") not in (None, ""):
                tokens.add(f"reading_id-{str(props['reading_id']).casefold()}")
            elif props.get("equipment_id") not in (None, ""):
                tokens.add(f"equipment_id-{str(props['equipment_id']).casefold()}")
            for key in ("sample_key", "row_identity"):
                value = props.get(key)
                if value not in (None, "") and len(str(value)) >= 3:
                    tokens.add(str(value).casefold())
            if not tokens:
                tokens.add(str(node.get("id") or "").split(":")[-1].casefold())
            matches = []
            for item in evidence_rows:
                assertion = str(item.assertion_id or "").casefold()
                if any(token and token in assertion for token in tokens):
                    matches.append({
                        "id": item.id,
                        "kind": item.assertion_kind,
                        "source_file": item.source_file,
                        "source_row_id": item.source_row_id,
                        "source_sample_id": item.source_sample_id,
                        "source_media_id": item.source_media_id,
                        "extractor": item.extractor,
                        "model_name": item.model_name,
                        "confidence": item.confidence,
                    })
            if matches:
                node["evidence"] = matches[:8]
                node["evidence_count"] = len(matches)
    for edge in graph_data.get("edges", []):
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        relation_type = str(edge.get("type") or edge.get("label") or "关联")
        edge_tokens = {
            str(edge.get("id") or ""),
            f"{source}:{relation_type}:{target}",
            f"{source}:{relation_type}:{target}:{(edge.get('properties') or {}).get('valid_from', '')}",
        }
        edge_matches: list[dict[str, Any]] = []
        for token in edge_tokens:
            if token:
                edge_matches.extend(edge_evidence_by_assertion.get(token, []))
        edge["evidence"] = edge_matches[:8]
        edge["evidence_count"] = len(edge_matches)
    if data_class == "multimodal":
        graph_data = _augment_multimodal_data_model(db, ontology_id, graph_data)
    visible_node_count = len(graph_data.get("nodes", []))
    visible_edge_count = len(graph_data.get("edges", []))
    total_node_count = max(int(graph_data.get("total_instances", 0) or 0), visible_node_count)
    total_edge_count = max(int(graph_data.get("total_edges", 0) or 0), visible_edge_count)
    type_counts: dict[str, int] | None
    unrestricted_subject = not isinstance(user, User) or str(getattr(ontology, "created_by", "")) == str(user.id) or str(user.role or "") == "admin"
    if service.available and unrestricted_subject:
        try:
            try:
                type_counts = service.get_instance_type_counts(
                    ontology_id,
                    entity_type=entity_type,
                    episode_id=episode_id,
                    seq_from=seq_from,
                    seq_to=seq_to,
                    graph_namespace=snapshot_namespace,
                )
            except TypeError:
                type_counts = service.get_instance_type_counts(
                    ontology_id,
                    entity_type=entity_type,
                    episode_id=episode_id,
                    seq_from=seq_from,
                    seq_to=seq_to,
                )
        except Exception:
            type_counts = None
    else:
        type_counts = None
    groups = _data_model_type_groups(canonical, graph_data.get("nodes", []), type_counts)
    response: dict[str, Any] = {
        "ontology_id": ontology_id,
        "data_class": data_class,
        "type_groups": groups,
        "nodes": graph_data.get("nodes", []),
        "edges": graph_data.get("edges", []),
        "total_nodes": total_node_count,
        "total_edges": total_edge_count,
        "pagination": {
            "offset": int(graph_data.get("offset", offset) or offset),
            "limit": int(limit),
            "returned": len(graph_data.get("nodes", [])),
            "total": total_node_count,
            "next_offset": graph_data.get("next_offset") if graph_data.get("next_offset") is not None and graph_data.get("next_offset") < total_node_count else None,
        },
        "available": bool(graph_data.get("available", False)),
        "graph_backend": graph_data.get("graph_backend", "falkordb"),
        "graph_namespace": snapshot_namespace or graph_data.get("graph_namespace"),
        "data_snapshot_id": getattr(snapshot, "id", None) if snapshot else None,
    }
    if graph_data.get("error"):
        response["error"] = graph_data["error"]
    if data_class == "temporal":
        timeline = timeline or {}
        dates = [str(value) for value in (timeline.get("dates") or [])]
        response["time"] = {
            "kind": "ordinal",
            "mode": mode,
            "current": resolved_at,
            "min": dates[0] if dates else None,
            "max": dates[-1] if dates else None,
            "dates": dates,
            "buckets": timeline.get("buckets") or [],
            "episodes": timeline.get("episodes") or [],
            "episode_id": episode_id,
        }
    else:
        response["time"] = None
    response["result_manifest"] = _result_manifest(
        db,
        ontology_id,
        user=user,
        adapter="falkordb" if service.available else "postgres-temporal",
        adapter_version="v2",
        status="ready" if graph_data.get("available", True) else "unavailable",
        result_count=int(graph_data.get("returned") or len(graph_data.get("nodes") or [])),
        warnings=[str(graph_data.get("error"))] if graph_data.get("error") else None,
    )
    return response


def _sqlite_graph_data(ontology_id: str, limit: int = 200, label_filter: str | None = None) -> dict:
    from app.models.entity import Entity
    from app.models.relation import Relation

    db = SessionLocal()
    try:
        query = db.query(Entity).filter(Entity.ontology_id == ontology_id)
        if label_filter:
            query = query.filter(Entity.type == label_filter)
        entities = query.limit(limit).all()
        entity_ids = {e.id for e in entities}
        relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
        edges = [
            {
                "id": r.id,
                "source": r.source_entity,
                "target": r.target_entity,
                "type": r.type or "RELATED",
                "properties": r.properties or {},
            }
            for r in relations
            if r.source_entity in entity_ids and r.target_entity in entity_ids
        ]
        nodes = [
            {
                "id": e.id,
                "labels": [e.type or "OntologyEntity"],
                "properties": {
                    **(e.properties or {}),
                    "id": e.id,
                    "source_id": e.id,
                    "ontology_id": ontology_id,
                    "name_cn": e.name_cn or "",
                    "name_en": e.name_en or "",
                    "name": e.name_cn or e.name_en or e.id,
                    "type": e.type or "",
                    "description": e.description or "",
                    "confidence": e.confidence or 1.0,
                    "version": e.version or "v0.1",
                },
            }
            for e in entities
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "neo4j_available": False,
            "fallback": "sqlite",
        }
    finally:
        db.close()


@router.get("/{ontology_id}/search")
def search_ontology(
    ontology_id: str,
    q: str = Query("", max_length=200),
    entity_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Search the published ontology without exposing graph query syntax.

    Supplying ``entity_id`` deliberately narrows the scope to the selected
    entity's attributes; otherwise names, attributes, relations, rules and
    provenance are searchable across the ontology.
    """
    data = _canonical_ontology_data(db, ontology_id, limit=1000, user=user)
    needle = q.strip().casefold()
    if not needle:
        return {"query": q, "scope": "entity_properties" if entity_id else "ontology", "results": [], "groups": {}, "result_manifest": data.get("result_manifest")}

    def matches(*values: Any) -> bool:
        return any(needle in str(value or "").casefold() for value in values)

    groups: dict[str, list[dict[str, Any]]] = {"实体": [], "属性": [], "关系": [], "逻辑规则": [], "来源": []}
    nodes_by_id = {node["id"]: node for node in data["nodes"]}
    scoped = [nodes_by_id[entity_id]] if entity_id and entity_id in nodes_by_id else data["nodes"]
    for node in scoped:
        props = node["properties"]
        if not entity_id and matches(props.get("name"), props.get("name_cn"), props.get("name_en"), props.get("description")):
            groups["实体"].append({"kind": "entity", "id": node["id"], "label": props.get("name"), "description": props.get("description")})
        for prop in props.get("property_definitions", []):
            if matches(prop.get("name"), prop.get("label"), prop.get("description"), prop.get("source_field"), prop.get("type")):
                groups["属性"].append({"kind": "property", "id": prop.get("id") or prop.get("name"), "entity_id": node["id"], "label": prop.get("label") or prop.get("name"), "description": prop.get("description") or prop.get("source_field") or ""})
        if not entity_id:
            for source in props.get("source_fields", []):
                if matches(source):
                    groups["来源"].append({"kind": "source", "id": node["id"], "entity_id": node["id"], "label": str(source), "description": props.get("name")})
    if not entity_id:
        for edge in data["edges"]:
            props = edge.get("properties") or {}
            if matches(edge.get("label"), edge.get("type"), props.get("description"), props.get("cardinality"), props.get("source_fields")):
                groups["关系"].append({"kind": "relationship", "id": edge["id"], "source": edge["source"], "target": edge["target"], "label": edge.get("label"), "description": props.get("description") or ""})
        for rule in data["logic_rules"]:
            if matches(rule.get("name"), rule.get("description"), rule.get("formula"), rule.get("condition"), rule.get("effect"), rule.get("evidence")):
                groups["逻辑规则"].append({"kind": "logic_rule", "id": rule["id"], "label": rule.get("name"), "description": rule.get("description") or rule.get("formula") or ""})
    groups = {key: value[:40] for key, value in groups.items() if value}
    results = [item for value in groups.values() for item in value]
    return {"query": q, "scope": "entity_properties" if entity_id else "ontology", "results": results, "groups": groups, "result_manifest": data.get("result_manifest")}


@router.get("/{ontology_id}/entities")
def list_ontology_entities(ontology_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Read-only entity-type catalogue with real instance counts."""
    data = _canonical_ontology_data(db, ontology_id, limit=1000, user=user)
    relation_counts: Counter[str] = Counter()
    for edge in data["edges"]:
        relation_counts[edge["source"]] += 1
        relation_counts[edge["target"]] += 1
    return {
        "entities": [
            {
                "id": node["id"],
                "name": node["properties"].get("name"),
                "name_cn": node["properties"].get("name_cn"),
                "name_en": node["properties"].get("name_en"),
                "description": node["properties"].get("description"),
                "properties": node["properties"].get("property_definitions", []),
                "property_count": len(node["properties"].get("property_definitions", [])),
                "relationship_count": relation_counts.get(node["id"], 0),
                "instance_count": node["properties"].get("instance_count", 0),
                "evidence_count": node["properties"].get("evidence_count", 0),
                "confidence": node["properties"].get("confidence", 1.0),
                "source_fields": node["properties"].get("source_fields", []),
            }
            for node in data["nodes"]
        ],
        "summary": data["summary"],
        "result_manifest": data.get("result_manifest"),
    }


@router.get("/{ontology_id}/entities/{entity_id}/instances")
def list_entity_instances(
    ontology_id: str,
    entity_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.models.entity import Entity
    from app.models.entity_instance import EntityInstance
    from app.models.v2.construction import EvidenceRef

    entity = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
    if not entity:
        raise HTTPException(404, "本体实体不存在")
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    query = db.query(EntityInstance).filter(EntityInstance.ontology_id == ontology_id, EntityInstance.entity_id == entity_id).order_by(EntityInstance.created_at.desc())
    redacted_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(user, User):
        all_rows = query.all()
        records = [{
            "id": row.id,
            "entity_id": row.entity_id,
            "object_type_resource_id": row.object_type_resource_id or entity.semantic_resource_id,
            "semantic_resource_id": row.object_type_resource_id or entity.semantic_resource_id,
            "properties": row.row_data or {},
        } for row in all_rows]
        projection = resolve_projection(
            db,
            AuthorizationContext(
                ontology_id=ontology_id,
                principal_id=str(user.id),
                principal_role=user.role,
                metadata_revision_id=project.current_revision_id if project else None,
                object_type_id=entity_id,
            ),
            records=records,
        )
        visible = []
        for row in all_rows:
            redacted = redact_record(projection, {"id": row.id, "entity_id": row.entity_id, "properties": row.row_data or {}})
            if redacted is not None:
                visible.append(row)
                redacted_by_id[str(row.id)] = redacted
        total = len(visible)
        rows = visible[offset:offset + limit]
    else:
        total = query.count()
        rows = query.offset(offset).limit(limit).all()
    instance_ids = [row.id for row in rows]
    evidence: dict[str, int] = {}
    if instance_ids:
        evidence = dict(
            db.query(EvidenceRef.assertion_id, func.count(EvidenceRef.id))
            .filter(EvidenceRef.ontology_id == ontology_id, EvidenceRef.assertion_id.in_(instance_ids))
            .group_by(EvidenceRef.assertion_id)
            .all()
        )
    return {
        "entity": {"id": entity.id, "name": entity.name_cn or entity.name_en or entity.id},
        "total": total,
        "offset": offset,
        "limit": limit,
        "instances": [
            {
                "id": row.id,
                "row_identity": row.row_identity,
                "row_data": redacted_by_id.get(str(row.id), {}).get("properties", row.row_data or {}) if isinstance(user, User) else (row.row_data or {}),
                "revision_id": getattr(row, "revision_id", None),
                "evidence_count": int(evidence.get(row.id, 0)),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "result_manifest": _result_manifest(db, ontology_id, user=user, adapter="postgres-semantic", adapter_version="v2", result_count=len(rows)),
    }


@router.get("/{ontology_id}/graph/quality")
def graph_quality(ontology_id: str, source: str = Query("schema", pattern="^(schema|instances)$")):
    if source == "instances":
        return get_falkordb().quality(ontology_id)
    from app.models.entity import Entity
    from app.models.relation import Relation
    from collections import Counter

    db = SessionLocal()
    try:
        entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
        relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
        entity_ids = {e.id for e in entities}
        connected_ids = {r.source_entity for r in relations} | {r.target_entity for r in relations}
        orphan_relations = [
            r.id for r in relations
            if r.source_entity not in entity_ids or r.target_entity not in entity_ids
        ]
        isolated = [e.id for e in entities if e.id not in connected_ids]
        names = [e.name_cn for e in entities if e.name_cn]
        duplicate_names = {name: count for name, count in Counter(names).items() if count > 1}
        object_types = Counter(e.type or "Entity" for e in entities)
        relation_types = Counter(r.type or "RELATED" for r in relations)
        node_count = len(entities)
        edge_count = len(relations)
        duplicate_name_instances = sum(duplicate_names.values())
        quality_score = 1.0
        if node_count:
            quality_score -= min(0.4, len(isolated) / node_count * 0.4)
            quality_score -= min(0.25, duplicate_name_instances / node_count * 0.25)
        if edge_count:
            quality_score -= min(0.25, len(orphan_relations) / edge_count * 0.25)
        return {
            "ontology_id": ontology_id,
            "graph_backend": "sqlite-schema",
            "available": True,
            "node_count": node_count,
            "edge_count": edge_count,
            "isolated_node_count": len(isolated),
            "orphan_relation_count": len(orphan_relations),
            "duplicate_display_name_count": duplicate_name_instances,
            "object_type_counts": dict(object_types),
            "relation_type_counts": dict(relation_types),
            "relation_density": round(edge_count / node_count, 4) if node_count else 0,
            "quality_score": round(max(0.0, quality_score), 4),
            "samples": {
                "isolated_node_ids": isolated[:10],
                "orphan_relation_ids": orphan_relations[:10],
                "duplicate_display_names": dict(list(duplicate_names.items())[:10]),
            },
        }
    finally:
        db.close()


@router.get("/{ontology_id}/integrations/status")
def integration_status(ontology_id: str):
    falkor = get_falkordb()
    from app.services.v2.vector.chroma_service import ChromaService
    chroma = ChromaService()
    return {
        "ontology_id": ontology_id,
        "falkordb": {"available": falkor.available, "host": falkor.host, "port": falkor.port},
        "chroma": {"available": chroma.available, "entity_count": chroma.count(ontology_id)},
    }


@router.get("/{ontology_id}/graph/temporal/coverage")
def temporal_coverage(ontology_id: str, production_line_id: str):
    """Return current and historical COVERS edges for one production line."""
    return get_falkordb().coverage(ontology_id, production_line_id)


@router.post("/{ontology_id}/graph/temporal/import")
def import_temporal_rows(ontology_id: str, body: TemporalImportRequest):
    """Normalize and import a bounded temporal row batch into FalkorDB.

    This is intentionally an explicit, deterministic API: the server never
    invents timestamps or entity identities when a row is malformed.
    """
    from app.services.v2.temporal_service import (
        TemporalConfig,
        build_observation_instances,
        normalize_temporal_rows,
    )

    falkor = get_falkordb()
    if not falkor.available:
        return {"available": False, "graph_backend": "falkordb", "error": "FalkorDB unavailable"}
    rows = body.rows
    time_kind = body.time_kind
    if body.adapter:
        from app.services.v2.datasets.temporal_adapters import get_adapter
        adapter = get_adapter(body.adapter)
        rows = adapter.normalize(rows)
        time_kind = adapter.time_kind
    config = TemporalConfig(
        time_kind=time_kind,
        sequence_column=body.sequence_column,
        event_time_column=body.event_time_column,
        valid_from_column=body.valid_from_column,
        valid_to_column=body.valid_to_column,
    )
    normalized, issues = normalize_temporal_rows(rows, config)
    nodes, edges = build_observation_instances(
        normalized,
        entity_id_column=body.entity_id_column,
        entity_type=body.entity_type,
        observation_type=body.observation_type,
        reading_id_prefix=body.reading_id_prefix,
    )
    node_count = falkor.upsert_instances(ontology_id, nodes)
    edge_count = falkor.upsert_relations(ontology_id, edges)
    evidence_count = 0
    if body.construction_run_id:
        from app.models.v2.construction import ConstructionRun
        from app.services.v2.construction_service import add_evidence, update_run
        db = SessionLocal()
        try:
            run = db.query(ConstructionRun).filter(
                ConstructionRun.id == body.construction_run_id,
                ConstructionRun.ontology_id == ontology_id,
            ).first()
            if not run:
                raise HTTPException(404, "Construction run not found")
            update_run(db, run, status="completed", progress={"completed": len(normalized), "total": len(rows)}, metrics={"nodes_upserted": node_count, "edges_upserted": edge_count, "temporal_issues": len(issues)})
            for index, row in enumerate(normalized):
                add_evidence(db, run=run, assertion_id=f"row:{index}", assertion_kind="mapping", extractor="rule", source_row=index, source_dataset_version="input", confidence=1.0, confidence_method="deterministic_temporal_normalization", evidence_text=str({k: row.get(k) for k in ("event_seq", "event_time", "valid_from", "valid_to")}))
                evidence_count += 1
        finally:
            db.close()
    return {
        "available": True,
        "graph_backend": "falkordb",
        "time_kind": time_kind,
        "nodes_upserted": node_count,
        "edges_upserted": edge_count,
        "evidence_refs": evidence_count,
        "row_count": len(rows),
        "issues": issues,
    }


@router.get("/{ontology_id}/graph/temporal/relations")
def temporal_relations(
    ontology_id: str,
    relation_type: str | None = None,
    subject_id: str | None = None,
    object_id: str | None = None,
    event_seq: int | None = Query(None, ge=0),
    event_time: str | None = None,
    at: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    relation_state: str = Query("all", pattern="^(all|current)$"),
    limit: int = 200,
):
    """Query temporal relations without exposing arbitrary write Cypher."""
    return get_falkordb().get_temporal_relations(
        ontology_id,
        relation_type=relation_type,
        subject_id=subject_id,
        object_id=object_id,
        event_seq=event_seq,
        event_time=event_time,
        at=at,
        date_from=date_from,
        date_to=date_to,
        relation_state=relation_state,
        limit=max(1, min(int(limit), 1000)),
    )


@router.post("/{ontology_id}/graph/cypher")
def run_cypher(ontology_id: str, body: CypherRequest):
    """执行 Cypher 查询 (只读校验 + 强制 ontology_id 过滤)"""
    from app.services.v2.graph.cypher_builder import validate_readonly_cypher

    error = validate_readonly_cypher(body.query)
    if error:
        raise HTTPException(400, error)

    svc = get_neo4j()
    if not svc.available:
        return {"results": [], "neo4j_available": False}
    params = dict(body.params or {})
    params["ontology_id"] = ontology_id  # 供查询中的 $ontology_id 使用, 防跨本体读取
    results = svc.run_cypher(body.query, params)
    svc.close()
    return {"results": results, "neo4j_available": True}


@router.get("/{ontology_id}/graph/neighbors/{node_id}")
def get_neighbors(ontology_id: str, node_id: str, depth: int = 1):
    """查询节点邻居"""
    svc = get_neo4j()
    if not svc.available:
        return {"nodes": [], "edges": [], "neo4j_available": False}
    query = f"""
    MATCH (n)-[r*1..{min(depth, 5)}]-(m)
    WHERE elementId(n) = $node_id AND n.ontology_id = $ontology_id
    RETURN n, r, m LIMIT 100
    """
    results = svc.run_cypher(query, {"node_id": node_id, "ontology_id": ontology_id})
    svc.close()
    return {"results": results, "neo4j_available": True}


# ── 自然语言查询 ──────────────────────────────────────────────────────

class NLQueryRequest(BaseModel):
    question: str
    schema: dict = {}


@router.post("/{ontology_id}/graph/ask")
def nl_query(ontology_id: str, body: NLQueryRequest):
    """自然语言 → Cypher → 图数据"""
    from app.services.v2.graph.nl2cypher import NL2CypherService
    nl_svc = NL2CypherService()
    plan = nl_svc.translate(body.question, body.schema)

    svc = get_neo4j()
    if not svc.available:
        return {"results": [], "cypher": plan.cypher, "explanation": plan.explanation, "neo4j_available": False}

    try:
        results = svc.run_cypher(plan.cypher, {"ontology_id": ontology_id})
        svc.close()
        return {
            "results": results,
            "cypher": plan.cypher,
            "explanation": plan.explanation,
            "confidence": plan.confidence,
            "neo4j_available": True,
        }
    except Exception as e:
        svc.close()
        return {"results": [], "cypher": plan.cypher, "error": str(e), "neo4j_available": True}


# ── 高级图分析 ─────────────────────────────────────────────────────────

@router.get("/{ontology_id}/graph/path")
def graph_path(ontology_id: str, src: str, tgt: str):
    """两节点间最短路径"""
    from app.services.v2.graph.graph_analytics import GraphAnalyticsService
    svc = GraphAnalyticsService()
    return svc.shortest_path(ontology_id, src, tgt)


@router.get("/{ontology_id}/graph/degree/{node_id}")
def node_degree(ontology_id: str, node_id: str):
    """查询节点度数（入度 + 出度）"""
    from app.services.v2.graph.graph_analytics import GraphAnalyticsService
    svc = GraphAnalyticsService()
    return svc.node_degree(ontology_id, node_id)


@router.get("/{ontology_id}/graph/top-nodes")
def top_nodes(ontology_id: str, limit: int = 10):
    """返回连接数最多的 Top-N 节点"""
    from app.services.v2.graph.graph_analytics import GraphAnalyticsService
    svc = GraphAnalyticsService()
    return {"nodes": svc.top_connected_nodes(ontology_id, limit)}


@router.post("/{ontology_id}/graph/sync")
def sync_graph(ontology_id: str):
    """将 SQLite 实体/关系全量同步到 Neo4j"""
    from app.database import SessionLocal
    from app.models.entity import Entity
    from app.models.relation import Relation

    neo = get_neo4j()
    if not neo.available:
        return {"synced": False, "reason": "Neo4j unavailable"}

    db = SessionLocal()
    try:
        entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
        relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()

        # Build entity id -> neo4j label map (use type as label, fallback Entity)
        entity_label_map: dict[str, str] = {}

        # Batch upsert entities
        batch = []
        for e in entities:
            label = (e.type or "Entity").replace(" ", "_")
            entity_label_map[e.id] = label
            props = {
                **(e.properties or {}),
                "id": e.id,           # SQLite UUID 优先，覆盖 properties 里的 id
                "source_id": e.id,
                "ontology_id": ontology_id,
                "name_cn": e.name_cn or "",
                "name": e.name_cn or "",
                "name_en": e.name_en or "",
                "type": e.type or "",
                "description": e.description or "",
                "confidence": e.confidence or 1.0,
                "version": e.version or "v0.1",
            }
            # Use generic label for batch
            batch.append(props)

        # Upsert all as generic "OntologyEntity" first (fast batch)
        synced_entities = neo.batch_upsert_entities("OntologyEntity", batch, key_field="id")

        # Upsert relations
        synced_relations = 0
        for r in relations:
            src_label = entity_label_map.get(r.source_entity, "OntologyEntity")
            tgt_label = entity_label_map.get(r.target_entity, "OntologyEntity")
            rel_type = (r.type or "RELATED").upper().replace(" ", "_").replace("-", "_")
            ok = neo.upsert_relation(
                "OntologyEntity", r.source_entity,
                "OntologyEntity", r.target_entity,
                rel_type,
                props={"ontology_id": ontology_id, "confidence": r.confidence or 1.0},
            )
            if ok:
                synced_relations += 1

        neo.close()
        return {
            "synced": True,
            "entities": synced_entities,
            "relations": synced_relations,
            "ontology_id": ontology_id,
        }
    finally:
        db.close()
