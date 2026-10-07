"""Data-plane capability and consistency status API.

PostgreSQL is authoritative. FalkorDB is reported as usable only when the
current or requested immutable snapshot points to a graph whose counts match
the snapshot manifest.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.user import User
from app.models.v2.temporal_replay import DataModelSnapshot
from app.schemas.v2.semantic_core import DataPlaneCapabilitiesContract, DataPlaneStatusContract
from app.services.v2.authorization_service import AuthorizationContext, resolve_projection
from app.services.v2.data_plane_service import (
    AdapterCapabilities,
    DataPlaneError,
    build_context,
    check_capability,
    make_manifest,
)
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.semantic_core_service import semantic_schema

router = APIRouter(dependencies=[Depends(get_current_user)])


def _database_ready(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        db.rollback()
        return False


def _projection_state(
    graph: FalkorDBService,
    ontology_id: str,
    snapshot: DataModelSnapshot | None,
    revision: OntologyRevision | None,
) -> tuple[str, str | None, dict[str, int] | None]:
    summary = revision.summary if revision and isinstance(revision.summary, dict) else {}
    rebuilt = summary.get("plan_a_projection_rebuild") if isinstance(summary, dict) else None
    use_rebuilt = isinstance(rebuilt, dict) and snapshot is not None and rebuilt.get("snapshot_id") == snapshot.id
    namespace = rebuilt.get("graph_namespace") if use_rebuilt else snapshot.graph_namespace if snapshot else None
    if not graph.available:
        return "unavailable", namespace, None
    if not namespace or not graph.graph_exists(ontology_id, namespace):
        return ("stale" if snapshot or rebuilt else "not_materialized"), namespace, None
    counts = graph.projection_counts(ontology_id, namespace)
    if counts is None:
        return "unavailable", namespace, None
    if use_rebuilt:
        expected = {"nodes": int(rebuilt.get("node_count") or 0), "edges": int(rebuilt.get("edge_count") or 0)}
    elif snapshot:
        expected = {"nodes": int(snapshot.node_count or 0), "edges": int(snapshot.edge_count or 0)}
    else:
        return "untracked", namespace, counts
    return ("ready" if counts == expected else "stale"), namespace, counts


def _capabilities(
    *,
    postgres_ready: bool,
    graph_ready: bool,
    has_snapshot: bool,
) -> list[dict[str, object]]:
    return [
        AdapterCapabilities(
            "postgres-temporal", "v2", postgres_ready, has_snapshot, has_snapshot,
            postgres_ready, postgres_ready, postgres_ready, postgres_ready, "temporal-sql",
        ).__dict__,
        AdapterCapabilities(
            "falkordb", "v2", graph_ready, False, False,
            graph_ready, graph_ready, graph_ready, False, "falkor-graph",
        ).__dict__,
        AdapterCapabilities(
            "legacy-neo4j", "legacy", False, False, False, False, False, False, False, None,
        ).__dict__,
    ]


def _load_access(db: Session, ontology_id: str, user: User):
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "本体不存在", "next_action": "检查 ontology_id", "context_id": uuid.uuid4().hex})
    revision = db.query(OntologyRevision).filter(OntologyRevision.id == project.current_revision_id).first() if project.current_revision_id else None
    if not revision:
        revision = db.query(OntologyRevision).filter(
            OntologyRevision.ontology_id == ontology_id,
            OntologyRevision.is_current.is_(True),
        ).order_by(OntologyRevision.revision_no.desc()).first()
    projection = resolve_projection(
        db,
        AuthorizationContext(
            ontology_id=ontology_id,
            principal_id=str(user.id),
            principal_role=user.role,
            metadata_revision_id=revision.id if revision else None,
        ),
    )
    if not projection.allowed:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "本体不存在", "next_action": "检查访问权限", "context_id": uuid.uuid4().hex})
    return project, revision, projection


@router.get("/ontologies/{ontology_id}/data-plane/capabilities", response_model=DataPlaneCapabilitiesContract)
def capabilities(ontology_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project, revision, _projection = _load_access(db, ontology_id, user)
    postgres_ready = _database_ready(db)
    snapshot = db.query(DataModelSnapshot).filter(
        DataModelSnapshot.id == project.current_data_snapshot_id,
        DataModelSnapshot.ontology_id == ontology_id,
        DataModelSnapshot.status == "published",
    ).first() if project.current_data_snapshot_id else None
    graph = FalkorDBService()
    graph_state, namespace, counts = _projection_state(graph, ontology_id, snapshot, revision)
    has_snapshot = db.query(DataModelSnapshot.id).filter(
        DataModelSnapshot.ontology_id == ontology_id,
        DataModelSnapshot.status == "published",
    ).first() is not None
    return {
        "ontology_id": ontology_id,
        "capabilities": _capabilities(
            postgres_ready=postgres_ready,
            graph_ready=graph_state == "ready",
            has_snapshot=has_snapshot,
        ),
        "adapters": {
            "postgres-temporal": {"adapter": "postgres-temporal", "status": "ready" if postgres_ready else "unavailable", "authoritative": True},
            "falkordb": {"adapter": "falkordb", "status": graph_state, "graph_namespace": namespace, "counts": counts, "authoritative": False},
        },
        "fallback_policy": "只有显式授权且语义等价时才允许降级；合法空结果与适配器不可用分开返回",
    }


@router.get("/ontologies/{ontology_id}/data-plane/status", response_model=DataPlaneStatusContract)
def status(
    ontology_id: str,
    mode: str = Query("live", pattern="^(live|pinned|snapshot)$"),
    snapshot_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project, current_revision, _initial_projection = _load_access(db, ontology_id, user)
    snapshot = None
    revision = current_revision
    if mode == "snapshot":
        snapshot = db.query(DataModelSnapshot).filter(
            DataModelSnapshot.id == snapshot_id,
            DataModelSnapshot.ontology_id == ontology_id,
            DataModelSnapshot.status == "published",
        ).first() if snapshot_id else None
        if not snapshot:
            raise HTTPException(status_code=404, detail={"code": "SNAPSHOT_NOT_FOUND", "message": "没有找到该本体的已发布快照", "next_action": "选择有效 snapshot_id", "context_id": uuid.uuid4().hex})
        if snapshot.schema_revision_id:
            revision = db.query(OntologyRevision).filter(OntologyRevision.id == snapshot.schema_revision_id).first() or revision
    elif mode == "pinned":
        snapshot = db.query(DataModelSnapshot).filter(
            DataModelSnapshot.id == project.current_data_snapshot_id,
            DataModelSnapshot.ontology_id == ontology_id,
            DataModelSnapshot.status == "published",
        ).first() if project.current_data_snapshot_id else None

    schema = semantic_schema(db, ontology_id, revision.id if revision else None)
    projection = resolve_projection(
        db,
        AuthorizationContext(
            ontology_id=ontology_id,
            principal_id=str(user.id),
            principal_role=user.role,
            metadata_revision_id=revision.id if revision else None,
        ),
    )
    if not projection.allowed:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "本体不存在", "next_action": "检查访问权限", "context_id": uuid.uuid4().hex})
    postgres_ready = _database_ready(db)
    graph = FalkorDBService()
    graph_state, namespace, counts = _projection_state(graph, ontology_id, snapshot, revision)
    postgres = AdapterCapabilities(
        "postgres-temporal", "v2", postgres_ready, bool(snapshot),
        bool(snapshot), postgres_ready, postgres_ready, postgres_ready, postgres_ready, "temporal-sql",
    )
    context_snapshot_id = snapshot.id if snapshot else None
    try:
        context = build_context(
            ontology_id,
            metadata_revision_id=revision.id if revision else schema.get("revision_id"),
            metadata_digest=revision.metadata_digest if revision else schema.get("metadata_digest"),
            source_manifest_id=snapshot.id if snapshot else None,
            source_version=snapshot.snapshot_hash if snapshot else None,
            projection_id=namespace,
            snapshot_id=context_snapshot_id,
            permission_digest=projection.permission_digest,
            consistency={"mode": mode, "snapshot_id": context_snapshot_id},
        )
        check_capability(postgres, context)
    except DataPlaneError as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code, "message": str(exc),
            "next_action": exc.next_action or "选择可用的一致性模式",
            "context_id": exc.context_id,
        }) from exc

    manifest = make_manifest(
        context,
        postgres,
        status="ready" if postgres_ready else "unavailable",
        permission_digest=projection.permission_digest,
        warnings=[] if postgres_ready else ["PostgreSQL 当前不可用"],
    )
    return {
        "ontology_id": ontology_id,
        "metadata_revision_id": context.metadata_revision_id,
        "metadata_digest": context.metadata_digest,
        "authoritative": "postgres",
        "source_snapshot": {
            "id": snapshot.id if snapshot else None,
            "version": snapshot.snapshot_hash if snapshot else None,
            "status": snapshot.status if snapshot else "live",
        },
        "projections": [
            {"adapter": "postgres-temporal", "status": "ready" if postgres_ready else "unavailable", "authoritative": True},
            {"adapter": "falkordb", "status": graph_state, "authoritative": False, "graph_namespace": namespace, "counts": counts},
            {"adapter": "legacy-neo4j", "status": "disabled", "authoritative": False},
        ],
        "fallback_policy": "语义不等价、投影缺失或版本不匹配时返回 degraded/unavailable，不把错误伪装成空结果",
        "execution_context": {
            "context_id": context.context_id,
            "consistency": {"mode": mode, "snapshot_id": context_snapshot_id},
            "permission_digest": projection.permission_digest,
        },
        "result_manifest": manifest.as_dict(),
    }
