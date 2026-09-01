"""v2 Graph API — Nano schema compatibility plus FalkorDB instances"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.deps import get_current_user
from app.database import SessionLocal

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_neo4j():
    from app.services.v2.graph.neo4j_service import Neo4jService
    return Neo4jService()


def get_falkordb():
    from app.services.v2.graph.falkordb_service import FalkorDBService
    return FalkorDBService()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
    label_filter: str | None = None,
    view: str = Query("schema", pattern="^(schema|instances)$"),
    entity_type: str | None = None,
    seq_from: int | None = Query(None, ge=0),
    seq_to: int | None = Query(None, ge=0),
    relation_state: str = Query("all", pattern="^(all|current)$"),
    db: Session = Depends(get_db),
):
    """Return either the persisted Nano schema graph or industrial instances.

    ``view=instances`` is the teacher-facing path and is served exclusively
    from the per-ontology FalkorDB graph. The default schema view preserves
    existing Nano behavior for older ontologies.
    """
    limit = max(1, min(int(limit), 1000))
    if view == "instances":
        from app.models.ontology import OntologyProject
        if not db.query(OntologyProject.id).filter(OntologyProject.id == ontology_id).first():
            # Do not let a stale browser tab create an empty FalkorDB graph
            # for a deleted ontology merely by reading the instances view.
            raise HTTPException(404, "Ontology not found")
        svc = get_falkordb()
        if not svc.available:
            return {
                "nodes": [], "edges": [], "graph_backend": "falkordb",
                "available": False, "error": "FalkorDB unavailable",
            }
        try:
            return svc.get_graph_data(
                ontology_id,
                limit=limit,
                entity_type=entity_type or label_filter,
                seq_from=seq_from,
                seq_to=seq_to,
                relation_state=relation_state,
            )
        except Exception as exc:
            return {
                "nodes": [], "edges": [], "graph_backend": "falkordb",
                "available": False, "error": str(exc),
            }
    svc = get_neo4j()
    if not svc.available:
        data = _sqlite_graph_data(ontology_id, limit=limit, label_filter=label_filter)
        data["graph_backend"] = "sqlite-schema"
        return data
    try:
        data = svc.get_graph_data(ontology_id, limit=limit, label_filter=label_filter)
    except Exception:
        # 共享 driver 缓存期间 Neo4j 宕机 → 回退 SQLite 而非 500
        svc.close()
        data = _sqlite_graph_data(ontology_id, limit=limit, label_filter=label_filter)
        data["graph_backend"] = "sqlite-schema"
        return data
    svc.close()
    # Neo4j 可用但该 ontology 无数据（如简易 LLM 路线未同步写入）→ 回退 SQLite
    if not data.get("nodes"):
        data = _sqlite_graph_data(ontology_id, limit=limit, label_filter=label_filter)
        data["graph_backend"] = "sqlite-schema"
        return data
    data["neo4j_available"] = True
    data["graph_backend"] = "neo4j-legacy"
    return data


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
