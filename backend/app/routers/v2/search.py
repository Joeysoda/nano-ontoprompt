"""v2 Search API — 关键词/语义统一搜索"""
from __future__ import annotations
import json
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import cast, or_, String as SAString
from sqlalchemy.orm import Session
from app.deps import get_current_user, get_db
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.core import QueryCore, QueryPolicy, FalkorReadAdapter
from app.services.v2.object_query.authorization import build_query_policy, policies_from_projection, require_ontology_access
from app.services.v2.authorization_service import AuthorizationProjection, redact_record
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.errors import ObjectQueryError
from app.schemas.v2.object_query import BaseObjectSet, EmptyObjectSet, FilterObjectSet, OrFilter, TextFilter, PropertyRef, TypeRef, LoadObjectSetRequest, ExecutionContext, ReadOptions

def authorize_search(request: Request, ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    try:
        request.state.plan_a_search_access = require_ontology_access(db, ontology_id, str(user.id), user.role)
    except ObjectQueryError:
        raise HTTPException(status_code=404, detail='Ontology not found or inaccessible')


router = APIRouter(dependencies=[Depends(authorize_search)])


class SearchRequest(BaseModel):
    query: str
    mode: str = "keyword"  # keyword | semantic
    entity_type: str | None = None
    n_results: int = 10


class ObjectSearchRequest(BaseModel):
    type_name: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=1000)
    select: list[str] = Field(default_factory=list, max_length=100)
    page_size: int = Field(default=50, ge=1, le=200)


def _request_access(request: Request, db: Session, ontology_id: str, user) -> AuthorizationProjection:
    access = getattr(request.state, "plan_a_search_access", None)
    if access is None:
        access = require_ontology_access(db, ontology_id, str(user.id), user.role)
        request.state.plan_a_search_access = access
    return access


def _redact_search_results(results: list[dict], access: AuthorizationProjection) -> list[dict]:
    """Return only search metadata permitted by the same Plan A projection.

    Vector stores are candidate indexes, not an authorization boundary.  In
    particular, their ``document`` field can contain a hidden description, so
    it is deliberately rebuilt from the redacted property envelope instead of
    passed through unchanged.
    """
    safe_results: list[dict] = []
    for item in results:
        metadata = item.get("metadata") if isinstance(item, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        properties = metadata.get("properties") if isinstance(metadata.get("properties"), dict) else {}
        properties = {str(key): value for key, value in properties.items()}
        for key in ("name_cn", "name_en", "description"):
            if key in metadata:
                properties.setdefault(key, metadata[key])
        record = {
            "id": str(item.get("id") or metadata.get("id") or ""),
            "entity_type": metadata.get("entity_type") or metadata.get("type"),
            "properties": properties,
        }
        safe = redact_record(access, record)
        if safe is None:
            continue
        safe_properties = safe.get("properties") if isinstance(safe.get("properties"), dict) else {}
        safe_metadata = {
            "entity_type": safe.get("entity_type"),
            "properties": safe_properties,
        }
        for key in ("name_cn", "name_en"):
            if key in safe_properties:
                safe_metadata[key] = safe_properties[key]
        safe_item = {key: value for key, value in item.items() if key not in {"document", "metadata"}}
        safe_item["metadata"] = safe_metadata
        safe_results.append(safe_item)
    return safe_results


def _sql_keyword_search(db: Session, ontology_id: str, q: str, n: int, access: AuthorizationProjection | None = None) -> list[dict]:
    """SQL 关键词回退搜索 — ChromaDB 不可用时按名称/描述/属性模糊匹配"""
    pattern = f"%{q}%"
    rows = db.query(Entity).filter(
        Entity.ontology_id == ontology_id,
        or_(
            Entity.name_cn.ilike(pattern),
            Entity.name_en.ilike(pattern),
            Entity.description.ilike(pattern),
            cast(Entity.properties, SAString).ilike(pattern),
        ),
    ).limit(n).all()
    results = [
        {
            "id": e.id,
            "document": e.description or e.name_cn,
            "metadata": {
                "name_cn": e.name_cn,
                "name_en": e.name_en,
                "entity_type": e.type,
                "properties": e.properties or {},
            },
        }
        for e in rows
    ]
    return _redact_search_results(results, access) if access is not None else results


@router.get("/{ontology_id}/search/keyword")
def keyword_search(
    ontology_id: str,
    q: str = Query(..., description="搜索词"),
    n: int = Query(20, description="结果数"),
    db: Session = Depends(get_db),
    request: Request = None,
    user=Depends(get_current_user),
):
    """关键词搜索 — 优先 ChromaDB，不可用时回退 SQL"""
    from app.services.v2.vector.chroma_service import ChromaService
    access = _request_access(request, db, ontology_id, user)
    svc = ChromaService()
    if not svc.available:
        results = _sql_keyword_search(db, ontology_id, q, n, access)
        return {"results": results, "chroma_available": False, "query": q, "permission_digest": access.permission_digest}
    results = _redact_search_results(svc.keyword_search(ontology_id, q, n_results=n), access)
    return {"results": results, "chroma_available": True, "query": q, "permission_digest": access.permission_digest}


@router.get("/{ontology_id}/search/semantic")
def semantic_search(
    ontology_id: str,
    q: str = Query(..., description="搜索词"),
    n: int = Query(10, description="结果数"),
    entity_type: str | None = Query(None, description="实体类型过滤"),
    db: Session = Depends(get_db),
    request: Request = None,
    user=Depends(get_current_user),
):
    """语义搜索（向量相似度）"""
    from app.services.v2.vector.chroma_service import ChromaService
    access = _request_access(request, db, ontology_id, user)
    svc = ChromaService()
    if not svc.available:
        raise HTTPException(503, detail={"code": "search_unavailable", "message": "Semantic search is unavailable"})
    results = _redact_search_results(svc.semantic_search(ontology_id, q, n_results=n, entity_type=entity_type), access)
    return {"results": results, "chroma_available": True, "query": q, "permission_digest": access.permission_digest}


@router.post("/{ontology_id}/search")
def unified_search(ontology_id: str, body: SearchRequest, db: Session = Depends(get_db), request: Request = None, user=Depends(get_current_user)):
    """统一搜索端点"""
    from app.services.v2.vector.chroma_service import ChromaService
    access = _request_access(request, db, ontology_id, user)
    svc = ChromaService()
    if not svc.available:
        if body.mode == "keyword":
            results = _sql_keyword_search(db, ontology_id, body.query, body.n_results, access)
            return {"results": results, "chroma_available": False, "mode": body.mode, "permission_digest": access.permission_digest}
        raise HTTPException(503, detail={"code": "search_unavailable", "message": "Semantic search is unavailable"})

    if body.mode == "semantic":
        results = svc.semantic_search(
            ontology_id, body.query,
            n_results=body.n_results,
            entity_type=body.entity_type,
        )
    else:
        results = svc.keyword_search(ontology_id, body.query, n_results=body.n_results)

    return {"results": _redact_search_results(results, access), "chroma_available": True, "mode": body.mode, "permission_digest": access.permission_digest}


@router.post("/{ontology_id}/search/objects")
def object_instance_search(ontology_id: str, body: ObjectSearchRequest, db: Session = Depends(get_db), user=Depends(get_current_user), request: Request = None):
    """Deterministic object-instance search backed by the shared Query Core.

    Semantic Chroma search remains a separate candidate-discovery path; this
    endpoint always returns typed ontology instances and never accepts query
    language from the client.
    """
    graph_service = FalkorDBService()
    access = _request_access(request, db, ontology_id, user)
    if not graph_service.available:
        raise HTTPException(status_code=503, detail={"code": "graph_unavailable", "path": "execution", "message": "FalkorDB is unavailable", "details": {"retryable": True}})
    try:
        metadata = load_sql_metadata(db, ontology_id)
        type_meta = metadata.resolve_type("object", body.type_name, "type_name")
        properties = [prop for prop in type_meta.properties.values() if prop.searchable and prop.data_type == "string" and not prop.api_name.startswith("_")]
        base = BaseObjectSet(kind="base", type_ref=TypeRef(kind="object", api_name=body.type_name))
        if properties:
            where = OrFilter(kind="or", items=[TextFilter(kind="text", property=PropertyRef(api_name=prop.api_name), mode="contains", query=body.query) for prop in properties])
            expression = FilterObjectSet(kind="filter", input=base, where=where)
        else:
            expression = EmptyObjectSet(kind="empty", type_ref=TypeRef(kind="object", api_name=body.type_name))
        request = LoadObjectSetRequest(expression=expression, context=ExecutionContext(ontology_id=ontology_id),
            read=ReadOptions(select=[PropertyRef(api_name=name) for name in body.select], page_size=body.page_size))
        graph = graph_service._graph(ontology_id)
        core = QueryCore(
            metadata,
            FalkorReadAdapter(graph),
            build_query_policy(
                db,
                ontology_id,
                str(user.id),
                user.role,
                requested_fields=tuple(body.select),
                preloaded_policies=policies_from_projection(access),
            ),
        )
        return core.load(request)
    except ObjectQueryError as exc:
        from app.routers.v2.object_sets import http_error
        raise http_error(exc) from exc
