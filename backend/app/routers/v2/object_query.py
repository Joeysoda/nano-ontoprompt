"""Object Set load API backed by the validated v2 query IR."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.ontology import OntologyProject
from app.schemas.v2.object_query import (
    AggregateObjectSetRequest,
    LoadObjectSetRequest,
    LoadObjectSetResponse,
    ValidateObjectQueryResponse,
)
from app.schemas.v2.object_set import ObjectSetDefinition
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.core import QueryCore, QueryPolicy, FalkorReadAdapter
from app.services.v2.object_query.resources import ResourceService
from app.routers.v2.object_sets import http_error
from app.services.v2.object_query.capabilities import capability_payload
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.data_views import as_response, build_live_view, require_ready
from app.schemas.v2.object_query import AggregateResult, QueryDataViewCreate, QueryDataViewResponse, QueryJobResponse
from app.schemas.v2.object_query import CompareObjectSetRequest, CompareResult, ReadOptions
from app.models.v2.query_job import QueryJob
from app.services.v2.scenarios import resolve_scenario_context, ScenarioError
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import os


router = APIRouter()


def _validate_request(ontology_id: str, request: LoadObjectSetRequest, db: Session, user):
    if request.context.ontology_id != ontology_id:
        raise ObjectQueryError("ontology_context_mismatch", "context.ontology_id", "Path ontology and execution context ontology must match")
    try:
        request = request.model_copy(update={"context": resolve_scenario_context(db, request.context, ontology_id, user)})
    except ScenarioError as exc:
        raise ObjectQueryError(exc.code, exc.path, exc.message) from exc
    metadata = load_sql_metadata(db, ontology_id)
    resource_service = ResourceService(db, ontology_id, user, metadata)
    definition = ObjectSetDefinition(
        expression=request.expression,
        parameter_schema=request.parameter_schema,
    )
    canonical, validation, dependencies = resource_service.prepare(definition)
    from app.services.v2.object_query.core import metadata_digest
    from app.services.v2.object_query.normalize import definition_hash
    return ValidateObjectQueryResponse(
        result_type=validation.result_type,
        definition_hash=definition_hash(request.expression, canonical["parameter_schema"]),
        metadata_digest=metadata_digest(metadata),
        dependencies=dependencies,
        node_count=validation.node_count,
        traversal_depth=validation.traversal_depth,
        capabilities=capability_payload(),
    )


def _authorize(ontology_id: str, db: Session, user) -> None:
    item = db.get(OntologyProject, ontology_id)
    if item is None or (user.role != "admin" and item.created_by != user.id):
        raise HTTPException(status_code=404, detail="Ontology not found or inaccessible")


def get_falkordb() -> FalkorDBService:
    return FalkorDBService()


@router.get("/{ontology_id}/object-sets/capabilities")
def get_object_set_capabilities(
    ontology_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
) -> dict:
    _authorize(ontology_id, db, user)
    return capability_payload()


@router.post("/{ontology_id}/object-sets/load", response_model=LoadObjectSetResponse)
def load_object_set(
    ontology_id: str,
    request: LoadObjectSetRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    graph_service: FalkorDBService = Depends(get_falkordb),
) -> LoadObjectSetResponse:
    _authorize(ontology_id, db, user)
    request = request.model_copy(update={"context": resolve_scenario_context(db, request.context, ontology_id, user)})
    if request.context.ontology_id != ontology_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ontology_context_mismatch",
                "path": "context.ontology_id",
                "message": "Path ontology and execution context ontology must match",
                "details": {},
            },
        )
    try:
        metadata = load_sql_metadata(db, ontology_id)
        if not graph_service.available:
            raise ObjectQueryError("graph_unavailable", "execution", "FalkorDB is unavailable")
        graph = graph_service._graph(ontology_id)
        graph_ontology_id = None
        if request.context.data_view_id:
            view = require_ready(db, request.context.data_view_id, ontology_id, principal_id=user.id, is_admin=user.role == "admin")
            graph = graph_service._graph(view.graph_key)
            graph_ontology_id = view.graph_key
            if request.context.metadata_digest and request.context.metadata_digest != view.metadata_digest:
                raise ObjectQueryError("metadata_mismatch", "context.metadata_digest", "Data view metadata has changed")
        core = QueryCore(metadata, FalkorReadAdapter(graph, graph_ontology_id=graph_ontology_id), QueryPolicy(principal=user.id))
        return core.load(request, ResourceService(db, ontology_id, user, metadata))
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/object-query/validate", response_model=ValidateObjectQueryResponse)
def validate_object_query(
    ontology_id: str,
    request: LoadObjectSetRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
) -> ValidateObjectQueryResponse:
    _authorize(ontology_id, db, user)
    try:
        return _validate_request(ontology_id, request, db, user)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/object-query/load", response_model=LoadObjectSetResponse)
def load_object_query(
    ontology_id: str,
    request: LoadObjectSetRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    graph_service: FalkorDBService = Depends(get_falkordb),
) -> LoadObjectSetResponse:
    return load_object_set(ontology_id, request, db, user, graph_service)


@router.post("/{ontology_id}/object-query/aggregate", response_model=AggregateResult)
def aggregate_object_query(ontology_id: str, request: AggregateObjectSetRequest, db: Session = Depends(get_db), user=Depends(get_current_user), graph_service: FalkorDBService = Depends(get_falkordb)):
    _authorize(ontology_id, db, user)
    try:
        request = request.model_copy(update={"context": resolve_scenario_context(db, request.context, ontology_id, user)})
        if not request.context.data_view_id:
            raise ObjectQueryError("consistency_unavailable", "context.data_view_id", "Exact aggregate requires a pinned snapshot data view")
        if not graph_service.available:
            raise ObjectQueryError("graph_unavailable", "execution", "FalkorDB is unavailable")
        metadata = load_sql_metadata(db, ontology_id)
        view = require_ready(db, request.context.data_view_id, ontology_id, principal_id=user.id, is_admin=user.role == "admin")
        graph = graph_service._graph(view.graph_key)
        core = QueryCore(metadata, FalkorReadAdapter(graph, graph_ontology_id=view.graph_key), QueryPolicy(principal=user.id))
        return core.aggregate(request, ResourceService(db, ontology_id, user, metadata))
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/object-query/compare", response_model=CompareResult)
def compare_object_query(ontology_id: str, request: CompareObjectSetRequest, db: Session = Depends(get_db), user=Depends(get_current_user), graph_service: FalkorDBService = Depends(get_falkordb)):
    _authorize(ontology_id, db, user)
    try:
        request = request.model_copy(update={"baseline_context": resolve_scenario_context(db, request.baseline_context, ontology_id, user), "candidate_context": resolve_scenario_context(db, request.candidate_context, ontology_id, user)})
    except ScenarioError as exc:
        raise http_error(ObjectQueryError(exc.code, exc.path, exc.message)) from exc
    if request.baseline_context.ontology_id != ontology_id or request.candidate_context.ontology_id != ontology_id:
        raise HTTPException(status_code=422, detail={"code": "ontology_context_mismatch", "path": "context.ontology_id", "message": "Both comparison contexts must match the route ontology", "details": {}})
    if request.baseline_context.consistency != "snapshot" or request.candidate_context.consistency != "snapshot":
        raise HTTPException(status_code=422, detail={"code": "consistency_unavailable", "path": "context.consistency", "message": "Comparison requires two pinned snapshot views", "details": {}})
    if not graph_service.available:
        raise HTTPException(status_code=503, detail={"code": "graph_unavailable", "path": "execution", "message": "FalkorDB is unavailable", "details": {}})
    try:
        metadata = load_sql_metadata(db, ontology_id)
        views = [require_ready(db, ctx.data_view_id, ontology_id, principal_id=user.id, is_admin=user.role == "admin") for ctx in (request.baseline_context, request.candidate_context)]
        resolver = ResourceService(db, ontology_id, user, metadata)
        def load_for(view, context):
            graph = graph_service._graph(view.graph_key)
            core = QueryCore(metadata, FalkorReadAdapter(graph, graph_ontology_id=view.graph_key), QueryPolicy(principal=user.id))
            read = ReadOptions(select=request.select, page_size=200)
            load = LoadObjectSetRequest(expression=request.expression, parameter_schema=request.parameter_schema,
                parameters=request.parameters, read=read, context=context)
            return core.materialize(load, resolver)
        baseline, base_last = load_for(views[0], request.baseline_context)
        candidate, cand_last = load_for(views[1], request.candidate_context)
        base_map = {(item.object_type, item.object_id): item for item in baseline}
        cand_map = {(item.object_type, item.object_id): item for item in candidate}
        added = [item.model_dump(mode='json') for key, item in sorted(cand_map.items()) if key not in base_map]
        removed = [item.model_dump(mode='json') for key, item in sorted(base_map.items()) if key not in cand_map]
        retained = [{"object_type": key[0], "object_id": key[1], "baseline": base_map[key].properties, "candidate": cand_map[key].properties}
                    for key in sorted(base_map.keys() & cand_map.keys())]
        return CompareResult(mode=request.mode, added=added, removed=removed, retained=retained,
            baseline_execution_hash=base_last.execution_hash, candidate_execution_hash=cand_last.execution_hash)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/object-query/data-views", response_model=QueryDataViewResponse, status_code=201)
def create_data_view(
    ontology_id: str,
    request: QueryDataViewCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
    graph_service: FalkorDBService = Depends(get_falkordb),
):
    _authorize(ontology_id, db, user)
    if request.source_ontology_id != ontology_id:
        raise HTTPException(status_code=422, detail={"code": "ontology_context_mismatch", "path": "source_ontology_id", "message": "Source ontology must match route ontology", "details": {}})
    if not graph_service.available:
        raise HTTPException(status_code=503, detail={"code": "graph_unavailable", "path": "data_view", "message": "FalkorDB is unavailable", "details": {"retryable": True}})
    try:
        metadata = load_sql_metadata(db, ontology_id)
        return as_response(build_live_view(db, graph_service, metadata, ontology_id, user.id, request.source_manifest_digest, request.retention_seconds))
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.get("/{ontology_id}/object-query/data-views/{view_id}", response_model=QueryDataViewResponse)
def get_data_view(ontology_id: str, view_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    try:
        from app.models.v2.query_view import QueryDataView
        view = db.get(QueryDataView, view_id)
        if view is None or view.ontology_id != ontology_id or (view.created_by != user.id and user.role != "admin"):
            raise ObjectQueryError("not_found", "view_id", "Data view not found")
        return as_response(view)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


def _job_response(job: QueryJob) -> QueryJobResponse:
    return QueryJobResponse(job_id=job.id, ontology_id=job.ontology_id, kind=job.kind,
        status=job.status, checkpoint=job.checkpoint_json or {}, result=job.result_json,
        error=job.error_json, cancel_requested=bool(job.cancel_requested))


@router.post("/{ontology_id}/object-query/jobs", response_model=QueryJobResponse, status_code=202)
def create_object_query_job(ontology_id: str, request: LoadObjectSetRequest, db: Session = Depends(get_db), user=Depends(get_current_user), graph_service: FalkorDBService = Depends(get_falkordb)):
    """Run a bounded materialization with durable status/checkpoint semantics.

    The API deliberately caps one run at the adapter budget; larger requests must
    be split by a worker in a later deployment rather than looping indefinitely
    inside an HTTP worker.
    """
    _authorize(ontology_id, db, user)
    if request.context.consistency != "snapshot" or not request.context.data_view_id:
        raise HTTPException(status_code=422, detail={"code": "consistency_unavailable", "path": "context.data_view_id", "message": "Jobs require a pinned snapshot data view", "details": {}})
    job = QueryJob(ontology_id=ontology_id, created_by=user.id, kind="materialize", status="queued",
                   request_json=request.model_dump(mode="json"), checkpoint_json={"pages": 0, "returned": 0})
    db.add(job); db.flush()
    if os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}:
        try:
            from app.tasks.v2.object_query import run_object_query_materialize
            db.commit()
            run_object_query_materialize.delay(job.id)
            return _job_response(job)
        except Exception as exc:
            db.rollback()
            job.status = "failed"
            job.error_json = {"code": "job_queue_unavailable", "message": str(exc)}
            db.commit()
            raise HTTPException(status_code=503, detail={"code": "job_queue_unavailable", "path": "job", "message": "Job worker is unavailable", "details": {}}) from exc
    try:
        job.status = "running"
        metadata = load_sql_metadata(db, ontology_id)
        view = require_ready(db, request.context.data_view_id, ontology_id)
        graph = graph_service._graph(view.graph_key)
        core = QueryCore(metadata, FalkorReadAdapter(graph, graph_ontology_id=view.graph_key), QueryPolicy(principal=user.id))
        page_token = None
        records = []
        for page_no in range(50):
            if job.cancel_requested:
                job.status = "cancelled"; break
            page_request = request.model_copy(update={"read": request.read.model_copy(update={"page_size": 200, "page_token": page_token})})
            result = core.load(page_request, ResourceService(db, ontology_id, user, metadata))
            records.extend([obj.model_dump(mode="json") for obj in result.objects])
            job.checkpoint_json = {"pages": page_no + 1, "returned": len(records), "execution_hash": result.execution_hash}
            db.flush()
            if not result.page.has_more or not result.page.next_page_token:
                job.result_json = {"objects": records, "count": len(records), "definition_hash": result.definition_hash, "execution_hash": result.execution_hash, "completeness": result.completeness}
                job.status = "completed"
                break
            page_token = result.page.next_page_token
        else:
            job.status = "failed"; job.error_json = {"code": "query_too_complex", "message": "Materialization page budget exceeded"}
    except ObjectQueryError as exc:
        job.status = "failed"; job.error_json = {"code": exc.code, "path": exc.path, "message": exc.message}
    except Exception as exc:
        job.status = "failed"; job.error_json = {"code": "execution_failed", "message": str(exc)}
    db.commit()
    return _job_response(job)


@router.get("/{ontology_id}/object-query/jobs/{job_id}", response_model=QueryJobResponse)
def get_object_query_job(ontology_id: str, job_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    job = db.get(QueryJob, job_id)
    if job is None or job.ontology_id != ontology_id or (job.created_by != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Query job not found")
    return _job_response(job)


@router.post("/{ontology_id}/object-query/jobs/{job_id}:cancel", response_model=QueryJobResponse)
def cancel_object_query_job(ontology_id: str, job_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    job = db.get(QueryJob, job_id)
    if job is None or job.ontology_id != ontology_id or (job.created_by != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Query job not found")
    if job.status not in {"completed", "failed", "cancelled"}:
        job.cancel_requested = True
        job.status = "cancelled"
        job.checkpoint_json = {**(job.checkpoint_json or {}), "cancelled_at": datetime.now(timezone.utc).isoformat()}
        db.commit()
    return _job_response(job)
