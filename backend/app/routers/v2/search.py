"""v2 Search API — 关键词/语义统一搜索"""
from __future__ import annotations
import json
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import cast, or_, String as SAString
from sqlalchemy.orm import Session
from app.deps import get_current_user, get_db
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.core import QueryCore, QueryPolicy, FalkorReadAdapter
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.errors import ObjectQueryError
from app.schemas.v2.object_query import BaseObjectSet, EmptyObjectSet, FilterObjectSet, OrFilter, TextFilter, PropertyRef, TypeRef, LoadObjectSetRequest, ExecutionContext, ReadOptions

def authorize_search(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ontology = db.get(OntologyProject, ontology_id)
    if ontology is None or (user.role != 'admin' and ontology.created_by != user.id):
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


def _sql_keyword_search(db: Session, ontology_id: str, q: str, n: int) -> list[dict]:
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
    return [
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


@router.get("/{ontology_id}/search/keyword")
def keyword_search(
    ontology_id: str,
    q: str = Query(..., description="搜索词"),
    n: int = Query(20, description="结果数"),
    db: Session = Depends(get_db),
):
    """关键词搜索 — 优先 ChromaDB，不可用时回退 SQL"""
    from app.services.v2.vector.chroma_service import ChromaService
    svc = ChromaService()
    if not svc.available:
        results = _sql_keyword_search(db, ontology_id, q, n)
        return {"results": results, "chroma_available": False, "query": q}
    results = svc.keyword_search(ontology_id, q, n_results=n)
    return {"results": results, "chroma_available": True, "query": q}


@router.get("/{ontology_id}/search/semantic")
def semantic_search(
    ontology_id: str,
    q: str = Query(..., description="搜索词"),
    n: int = Query(10, description="结果数"),
    entity_type: str | None = Query(None, description="实体类型过滤"),
):
    """语义搜索（向量相似度）"""
    from app.services.v2.vector.chroma_service import ChromaService
    svc = ChromaService()
    if not svc.available:
        raise HTTPException(503, detail={"code": "search_unavailable", "message": "Semantic search is unavailable"})
    results = svc.semantic_search(ontology_id, q, n_results=n, entity_type=entity_type)
    return {"results": results, "chroma_available": True, "query": q}


@router.post("/{ontology_id}/search")
def unified_search(ontology_id: str, body: SearchRequest, db: Session = Depends(get_db)):
    """统一搜索端点"""
    from app.services.v2.vector.chroma_service import ChromaService
    svc = ChromaService()
    if not svc.available:
        if body.mode == "keyword":
            results = _sql_keyword_search(db, ontology_id, body.query, body.n_results)
            return {"results": results, "chroma_available": False, "mode": body.mode}
        raise HTTPException(503, detail={"code": "search_unavailable", "message": "Semantic search is unavailable"})

    if body.mode == "semantic":
        results = svc.semantic_search(
            ontology_id, body.query,
            n_results=body.n_results,
            entity_type=body.entity_type,
        )
    else:
        results = svc.keyword_search(ontology_id, body.query, n_results=body.n_results)

    return {"results": results, "chroma_available": True, "mode": body.mode}


@router.post("/{ontology_id}/search/objects")
def object_instance_search(ontology_id: str, body: ObjectSearchRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Deterministic object-instance search backed by the shared Query Core.

    Semantic Chroma search remains a separate candidate-discovery path; this
    endpoint always returns typed ontology instances and never accepts query
    language from the client.
    """
    graph_service = FalkorDBService()
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
        core = QueryCore(metadata, FalkorReadAdapter(graph), QueryPolicy(principal=user.id))
        return core.load(request)
    except ObjectQueryError as exc:
        from app.routers.v2.object_sets import http_error
        raise http_error(exc) from exc
