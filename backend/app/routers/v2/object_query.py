"""Object Set load API backed by the validated v2 query IR."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_editor
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
from app.services.v2.object_query.authorization import build_query_policy, policies_from_projection, require_ontology_access
from app.services.v2.authorization_service import redact_link_record, redact_record, resolve_projection
from app.services.v2.object_query.resources import ResourceService
from app.routers.v2.object_sets import http_error
from app.services.v2.object_query.capabilities import capability_payload
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.data_views import as_response, build_live_view, require_ready
from app.schemas.v2.object_query import AggregateResult, QueryDataViewCreate, QueryDataViewResponse, QueryJobResponse
from app.schemas.v2.object_query import CompareObjectSetRequest, CompareResult, MultiSetGraphRequest, ReadOptions
from app.models.v2.query_job import QueryJob
from app.services.v2.scenarios import resolve_scenario_context, ScenarioError
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import os


router = APIRouter()


def _validate_request(ontology_id: str, request: LoadObjectSetRequest, db: Session, user, *, authorization_projection=None):
    if request.context.ontology_id != ontology_id:
        raise ObjectQueryError("ontology_context_mismatch", "context.ontology_id", "Path ontology and execution context ontology must match")
    try:
        request = request.model_copy(update={"context": resolve_scenario_context(db, request.context, ontology_id, user)})
    except ScenarioError as exc:
        raise ObjectQueryError(exc.code, exc.path, exc.message) from exc
    metadata = load_sql_metadata(db, ontology_id)
    resource_service = ResourceService(
        db,
        ontology_id,
        user,
        metadata,
        authorization_projection=authorization_projection,
    )
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


def _authorize(ontology_id: str, db: Session, user, *, metadata_revision_id: str | None = None):
    try:
        return require_ontology_access(
            db,
            ontology_id,
            str(user.id),
            user.role,
            metadata_revision_id=metadata_revision_id,
        )
    except ObjectQueryError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": "Ontology not found or inaccessible",
                "next_action": "检查 ontology_id 或访问策略",
            },
        ) from exc


def _query_policy(
    db: Session,
    ontology_id: str,
    user,
    *,
    metadata_revision_id: str | None = None,
    requested_fields: tuple[str, ...] = (),
    access_projection=None,
) -> QueryPolicy:
    return build_query_policy(
        db,
        ontology_id,
        str(user.id),
        user.role,
        metadata_revision_id=metadata_revision_id,
        requested_fields=requested_fields,
        preloaded_policies=policies_from_projection(access_projection) if access_projection is not None else None,
    )


def get_falkordb() -> FalkorDBService:
    return FalkorDBService()


def _pin_context_to_view(context, view, revision_id: str | None = None):
    """Copy the immutable view manifest into the request-scoped context."""
    return context.model_copy(update={
        "metadata_digest": context.metadata_digest or view.metadata_digest,
        "projection_token": context.projection_token or view.source_manifest_digest,
        "revision_id": context.revision_id or revision_id,
    })


@router.get("/{ontology_id}/object-query/catalog")
def query_catalog(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access = _authorize(ontology_id, db, user)
    from app.services.v2.object_query.core import metadata_digest
    metadata = load_sql_metadata(db, ontology_id)
    catalog = metadata.catalog()
    # Metadata is itself a data-plane read.  Build one synthetic record per
    # object type so the same object/field projection used by Query Core can
    # hide unauthorized types and properties before the catalog reaches the
    # client.  No policy table is read again: the ontology decision already
    # carries the request's applicable policy rows.
    type_records = [
        {
            "id": str(item["api_name"]),
            "entity_type": str(item["api_name"]),
            "properties": {str(prop["api_name"]): None for prop in item.get("properties", [])},
        }
        for item in catalog.get("types", [])
    ]
    catalog_projection = resolve_projection(
        db,
        access.context,
        preloaded_policies=policies_from_projection(access),
        records=type_records,
    )
    visible_types: list[dict] = []
    visible_type_names: set[str] = set()
    for item, record in zip(catalog.get("types", []), type_records):
        safe = redact_record(catalog_projection, record)
        if safe is None:
            continue
        object_id = str(record["id"])
        allowed = catalog_projection.allowed_fields_by_object.get(object_id, catalog_projection.allowed_fields)
        denied = catalog_projection.denied_fields | catalog_projection.denied_fields_by_object.get(object_id, set())
        properties = [
            prop for prop in item.get("properties", [])
            if allowed is None or (str(prop["api_name"]) in allowed and str(prop["api_name"]) not in denied)
        ]
        visible_types.append({**item, "properties": properties})
        visible_type_names.add(str(item["api_name"]))
    visible_links: list[dict] = []
    for link in catalog.get("links", []):
        if link.get("source_type") not in visible_type_names or link.get("target_type") not in visible_type_names:
            continue
        safe_link = redact_link_record(
            catalog_projection,
            {
                "id": f"link:{link.get('api_name')}",
                "link_type_id": link.get("api_name"),
                "relation_type": link.get("api_name"),
                "properties": {},
            },
            link_type_id=str(link.get("api_name") or ""),
        )
        if safe_link is not None:
            visible_links.append(link)
    return {"types": visible_types, "links": visible_links, "metadata_digest": metadata_digest(metadata)}


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
    access = _authorize(ontology_id, db, user)
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
            request = request.model_copy(update={"context": _pin_context_to_view(request.context, view, access.context.metadata_revision_id)})
        core = QueryCore(
            metadata,
            FalkorReadAdapter(graph, graph_ontology_id=graph_ontology_id),
            _query_policy(db, ontology_id, user, metadata_revision_id=request.context.revision_id, access_projection=access),
        )
        return core.load(request, ResourceService(db, ontology_id, user, metadata, authorization_projection=access))
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/object-query/validate", response_model=ValidateObjectQueryResponse)
def validate_object_query(
    ontology_id: str,
    request: LoadObjectSetRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
) -> ValidateObjectQueryResponse:
    access = _authorize(ontology_id, db, user)
    try:
        return _validate_request(ontology_id, request, db, user, authorization_projection=access)
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
    access = _authorize(ontology_id, db, user)
    try:
        request = request.model_copy(update={"context": resolve_scenario_context(db, request.context, ontology_id, user)})
        if not request.context.data_view_id:
            raise ObjectQueryError("consistency_unavailable", "context.data_view_id", "Exact aggregate requires a pinned snapshot data view")
        if not graph_service.available:
            raise ObjectQueryError("graph_unavailable", "execution", "FalkorDB is unavailable")
        metadata = load_sql_metadata(db, ontology_id)
        view = require_ready(db, request.context.data_view_id, ontology_id, principal_id=user.id, is_admin=user.role == "admin")
        request = request.model_copy(update={"context": _pin_context_to_view(request.context, view, access.context.metadata_revision_id)})
        graph = graph_service._graph(view.graph_key)
        core = QueryCore(
            metadata,
            FalkorReadAdapter(graph, graph_ontology_id=view.graph_key),
            _query_policy(db, ontology_id, user, metadata_revision_id=request.context.revision_id, access_projection=access),
        )
        return core.aggregate(request, ResourceService(db, ontology_id, user, metadata, authorization_projection=access))
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/object-query/compare", response_model=CompareResult)
def compare_object_query(ontology_id: str, request: CompareObjectSetRequest, db: Session = Depends(get_db), user=Depends(get_current_user), graph_service: FalkorDBService = Depends(get_falkordb)):
    access = _authorize(ontology_id, db, user)
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
        resolver = ResourceService(
            db,
            ontology_id,
            user,
            metadata,
            authorization_projection=access,
        )
        def load_for(view, context):
            context = _pin_context_to_view(context, view, access.context.metadata_revision_id)
            graph = graph_service._graph(view.graph_key)
            core = QueryCore(
                metadata,
                FalkorReadAdapter(graph, graph_ontology_id=view.graph_key),
                _query_policy(db, ontology_id, user, metadata_revision_id=context.revision_id, access_projection=access),
            )
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
            baseline_execution_hash=base_last.execution_hash, candidate_execution_hash=cand_last.execution_hash,
            result_manifests=[item for item in (base_last.result_manifest, cand_last.result_manifest) if item is not None])
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


def _induced_edges(access, snapshot, keys):
    """Edges between visible members, each passed through the link projection."""
    edges = []
    for source, relation, target in snapshot.edges:
        if source not in keys or target not in keys:
            continue
        safe_link = redact_link_record(
            access,
            {
                "id": f"{relation}:{source[0]}:{source[1]}:{target[0]}:{target[1]}",
                "source": {"object_type": source[0], "object_id": source[1]},
                "target": {"object_type": target[0], "object_id": target[1]},
                "relation_type": relation,
                "link_type_id": relation,
                "properties": {},
            },
            link_type_id=str(relation),
        )
        if safe_link is not None:
            edges.append({"source": safe_link["source"], "target": safe_link["target"], "relation_type": safe_link["relation_type"]})
    return edges


@router.post("/{ontology_id}/object-query/graph:multi")
def graph_multi_set(ontology_id: str, request: MultiSetGraphRequest, db: Session = Depends(get_db), user=Depends(get_current_user), graph_service: FalkorDBService = Depends(get_falkordb)):
    """Induced graph over the union of several typed Object Sets (Vertex Search Around)."""
    access = _authorize(ontology_id, db, user)
    try:
        if request.context.ontology_id != ontology_id:
            raise ObjectQueryError("ontology_context_mismatch", "context.ontology_id", "Path ontology and execution context ontology must match")
        context = resolve_scenario_context(db, request.context, ontology_id, user)
        if context.consistency != 'snapshot' or not context.data_view_id:
            raise ObjectQueryError('consistency_unavailable', 'context', 'Graph requires a pinned view')
        view = require_ready(db, context.data_view_id, ontology_id, principal_id=user.id, is_admin=user.role == 'admin')
        context = _pin_context_to_view(context, view, access.context.metadata_revision_id)
        if not graph_service.available:
            raise ObjectQueryError('graph_unavailable', 'execution', 'FalkorDB is unavailable')
        metadata = load_sql_metadata(db, ontology_id)
        adapter = FalkorReadAdapter(graph_service._graph(view.graph_key), graph_ontology_id=view.graph_key)
        core = QueryCore(metadata, adapter, _query_policy(db, ontology_id, user, metadata_revision_id=context.revision_id, access_projection=access))
        resources = ResourceService(db, ontology_id, user, metadata, authorization_projection=access)
        objects, hashes = {}, []
        for index, expression in enumerate(request.sets):
            load = LoadObjectSetRequest(expression=expression, context=context)
            try:
                members, result = core.materialize(load, resources)
            except ObjectQueryError as exc:
                raise ObjectQueryError(exc.code, f'sets[{index}].{exc.path}', exc.issue.message) from exc
            hashes.append(result.execution_hash)
            for item in members:
                objects[(item.object_type, item.object_id)] = item
            if len(objects) > 1000:
                raise ObjectQueryError('query_too_complex', f'sets[{index}]', 'Narrow the graph to at most 1,000 objects')
        edges = _induced_edges(access, adapter.read(ontology_id), set(objects))
        return {'objects': [objects[k].model_dump(mode='json') for k in sorted(objects)], 'edges': edges,
                'execution_context': context.model_dump(mode='json'), 'set_execution_hashes': hashes}
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/object-query/graph")
def graph_object_query(ontology_id: str, request: LoadObjectSetRequest, db: Session = Depends(get_db), user=Depends(get_current_user), graph_service: FalkorDBService = Depends(get_falkordb)):
    """An induced instance graph, using exactly the queried object cohort."""
    access = _authorize(ontology_id, db, user)
    try:
        _validate_request(ontology_id, request, db, user, authorization_projection=access)
        context = resolve_scenario_context(db, request.context, ontology_id, user)
        if context.consistency != 'snapshot' or not context.data_view_id:
            raise ObjectQueryError('consistency_unavailable', 'context', 'Graph requires a pinned view')
        view = require_ready(db, context.data_view_id, ontology_id, principal_id=user.id, is_admin=user.role == 'admin')
        context = _pin_context_to_view(context, view, access.context.metadata_revision_id)
        if not graph_service.available:
            raise ObjectQueryError('graph_unavailable', 'execution', 'FalkorDB is unavailable')
        metadata = load_sql_metadata(db, ontology_id)
        adapter = FalkorReadAdapter(graph_service._graph(view.graph_key), graph_ontology_id=view.graph_key)
        core = QueryCore(
            metadata,
            adapter,
            _query_policy(db, ontology_id, user, metadata_revision_id=context.revision_id, access_projection=access),
        )
        objects, result = core.materialize(
            request.model_copy(update={'context': context}),
            ResourceService(db, ontology_id, user, metadata, authorization_projection=access),
        )
        if len(objects) > 1000:
            raise ObjectQueryError('query_too_complex', 'expression', 'Narrow the graph to at most 1,000 objects')
        keys = {(item.object_type, item.object_id) for item in objects}
        edges = _induced_edges(access, adapter.read(ontology_id), keys)
        return {'objects': [item.model_dump(mode='json') for item in objects], 'edges': edges,
                'execution_context': context.model_dump(mode='json'), 'execution_hash': result.execution_hash,
                'result_manifest': result.result_manifest}
    except ObjectQueryError as exc:
        raise http_error(exc) from exc
    except ScenarioError as exc:
        raise HTTPException(exc.status, detail={'code': exc.code, 'message': exc.message}) from exc


@router.post("/{ontology_id}/object-query/data-views", response_model=QueryDataViewResponse, status_code=201)
def create_data_view(
    ontology_id: str,
    request: QueryDataViewCreate,
    db: Session = Depends(get_db),
    user=Depends(require_editor),
    graph_service: FalkorDBService = Depends(get_falkordb),
):
    access = _authorize(ontology_id, db, user)
    if request.source_ontology_id != ontology_id:
        raise HTTPException(status_code=422, detail={"code": "ontology_context_mismatch", "path": "source_ontology_id", "message": "Source ontology must match route ontology", "details": {}})
    if not graph_service.available:
        raise HTTPException(status_code=503, detail={"code": "graph_unavailable", "path": "data_view", "message": "FalkorDB is unavailable", "details": {"retryable": True}})
    try:
        metadata = load_sql_metadata(db, ontology_id)
        source_graph_key = None
        materialized = None
        manifest = request.source_manifest_digest
        if request.source_snapshot_id:
            from app.models.v2.temporal_replay import DataModelSnapshot
            snapshot = db.get(DataModelSnapshot, request.source_snapshot_id)
            if not snapshot or snapshot.ontology_id != ontology_id or (user.role != 'admin' and snapshot.created_by != user.id):
                raise ObjectQueryError('not_found', 'source_snapshot_id', 'Published snapshot not found or inaccessible')
            if snapshot.status != 'published' or not snapshot.snapshot_hash:
                raise ObjectQueryError('snapshot_unavailable', 'source_snapshot_id', 'Snapshot is not published')
            project = db.get(OntologyProject, ontology_id)
            if snapshot.schema_revision_id != project.current_revision_id:
                raise ObjectQueryError('metadata_mismatch', 'source_snapshot_id', 'Snapshot schema revision differs from the current query catalog')
            source_graph_key, manifest = snapshot.graph_namespace, snapshot.snapshot_hash
            if request.temporal_surface:
                from app.models.v2.temporal_replay import TemporalStreamEvent
                from app.services.v2.temporal_surface import prepare_events
                source_data = FalkorReadAdapter(graph_service._graph(source_graph_key), max_objects=100000, max_edges=500000).read(ontology_id)
                events = db.query(TemporalStreamEvent).filter_by(replay_id=snapshot.replay_id).order_by(TemporalStreamEvent.source_sequence, TemporalStreamEvent.id).all()
                materialized = prepare_events(ontology_id, metadata, source_data, events, request.temporal_surface)
        elif request.temporal_surface:
            raise ObjectQueryError('snapshot_unavailable', 'source_snapshot_id', 'Event materialization requires a published temporal snapshot')
        view = build_live_view(db, graph_service, metadata, ontology_id, user.id, manifest, request.retention_seconds,
                               source_graph_key=source_graph_key, source_snapshot_id=request.source_snapshot_id)
        if materialized:
            from app.services.v2.temporal_surface import install_surface
            try:
                install_surface(db, graph_service, view, *materialized)
            except Exception as exc:
                db.rollback()
                view = db.get(type(view), view.id)
                view.status = 'failed'; db.commit()
                raise ObjectQueryError('view_build_failed', 'temporal_surface', 'Temporal surface could not be published') from exc
        return as_response(view)
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


@router.get('/{ontology_id}/object-query/temporal-snapshots/{snapshot_id}')
def get_temporal_snapshot(ontology_id: str, snapshot_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    from app.models.v2.temporal_replay import DataModelSnapshot
    snapshot = db.get(DataModelSnapshot, snapshot_id)
    if not snapshot or snapshot.ontology_id != ontology_id or (user.role != 'admin' and snapshot.created_by != user.id):
        raise HTTPException(404, detail={'code': 'not_found', 'message': 'Published snapshot not found'})
    return {'id': snapshot.id, 'status': snapshot.status, 'snapshot_hash': snapshot.snapshot_hash,
            'schema_revision_id': snapshot.schema_revision_id, 'event_count': snapshot.event_count,
            'published_at': snapshot.published_at.isoformat() if snapshot.published_at else None}


@router.get('/{ontology_id}/object-query/data-views/{view_id}/time-series/{series_id}')
def read_time_series(ontology_id: str, view_id: str, series_id: str, offset: int = 0, limit: int = 200,
                     db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    if offset < 0 or limit < 1 or limit > 1000:
        raise HTTPException(422, detail={'code': 'invalid_pagination'})
    try:
        view = require_ready(db, view_id, ontology_id, principal_id=user.id, is_admin=user.role == 'admin')
        from app.models.v2.time_series import TimeSeriesSync
        series = db.query(TimeSeriesSync).filter_by(ontology_id=ontology_id, data_view_id=view.id, series_id=series_id).first()
        if not series:
            raise ObjectQueryError('not_found', 'series_id', 'Time series is not available in this view')
        return {'series_id': series.series_id, 'data_view_id': view.id, 'source_snapshot_id': view.source_snapshot_id,
                'root': {'object_type': series.root_type, 'object_id': series.root_id},
                'sensor': {'object_type': series.sensor_type, 'object_id': series.sensor_id},
                'property': series.property_api_name, 'unit': series.unit, 'interpolation': series.interpolation,
                'points': series.points[offset:offset + limit], 'page': {'offset': offset, 'returned': len(series.points[offset:offset + limit]),
                'has_more': offset + limit < series.point_count, 'next_offset': offset + limit if offset + limit < series.point_count else None}}
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


def _job_response(job: QueryJob) -> QueryJobResponse:
    return QueryJobResponse(job_id=job.id, ontology_id=job.ontology_id, kind=job.kind,
        status=job.status, checkpoint=job.checkpoint_json or {}, result=job.result_json,
        error=job.error_json, cancel_requested=bool(job.cancel_requested))


@router.post("/{ontology_id}/object-query/jobs", response_model=QueryJobResponse, status_code=202)
def create_object_query_job(ontology_id: str, request: LoadObjectSetRequest, db: Session = Depends(get_db), user=Depends(require_editor), graph_service: FalkorDBService = Depends(get_falkordb)):
    """Run a bounded materialization with durable status/checkpoint semantics.

    The API deliberately caps one run at the adapter budget; larger requests must
    be split by a worker in a later deployment rather than looping indefinitely
    inside an HTTP worker.
    """
    access = _authorize(ontology_id, db, user)
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
        view = require_ready(db, request.context.data_view_id, ontology_id, principal_id=user.id, is_admin=user.role == "admin")
        request = request.model_copy(update={"context": _pin_context_to_view(request.context, view, access.context.metadata_revision_id)})
        graph = graph_service._graph(view.graph_key)
        core = QueryCore(
            metadata,
            FalkorReadAdapter(graph, graph_ontology_id=view.graph_key),
            _query_policy(db, ontology_id, user, metadata_revision_id=request.context.revision_id, access_projection=access),
        )
        page_token = None
        records = []
        for page_no in range(50):
            if job.cancel_requested:
                job.status = "cancelled"; break
            page_request = request.model_copy(update={"read": request.read.model_copy(update={"page_size": 200, "page_token": page_token})})
            result = core.load(
                page_request,
                ResourceService(db, ontology_id, user, metadata, authorization_projection=access),
            )
            records.extend([obj.model_dump(mode="json") for obj in result.objects])
            job.checkpoint_json = {"pages": page_no + 1, "returned": len(records), "execution_hash": result.execution_hash}
            db.flush()
            if not result.page.has_more or not result.page.next_page_token:
                job.result_json = {
                    "objects": records,
                    "count": len(records),
                    "definition_hash": result.definition_hash,
                    "execution_hash": result.execution_hash,
                    "completeness": result.completeness,
                    "result_manifest": result.result_manifest.model_dump(mode="json") if result.result_manifest else None,
                }
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
def cancel_object_query_job(ontology_id: str, job_id: str, db: Session = Depends(get_db), user=Depends(require_editor)):
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
