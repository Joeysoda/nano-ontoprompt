"""Resource endpoints delegate all semantics and execution to Query Core."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.deps import get_current_user, get_db
from app.schemas.v2.object_set import CreateObjectSet, NewObjectSetVersion, PatchObjectSet, GrantObjectSet, ObjectSetDefinition, SetHandleRequest
from app.services.v2.object_query.resources import ResourceService
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.core import QueryCore, QueryPolicy, FalkorReadAdapter
from app.services.v2.object_query.data_views import require_ready
from app.schemas.v2.object_query import LoadObjectSetRequest, ExecutionContext, ReadOptions

router = APIRouter()


def http_error(exc):
    status = {'not_found': 404, 'forbidden': 403, 'version_conflict': 409,
              'expired_reference': 410, 'stale_reference': 409, 'metadata_mismatch': 409,
              'graph_unavailable': 503, 'query_timeout': 504, 'snapshot_unavailable': 409,
              'view_build_failed': 422}.get(exc.code, 422)
    return HTTPException(status_code=status, detail=exc.as_dict())


def service(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    svc = ResourceService(db, ontology_id, user, load_sql_metadata(db, ontology_id))
    try:
        svc.authorize_ontology()
        yield svc
    except ObjectQueryError as exc:
        db.rollback()
        raise http_error(exc) from exc
    except Exception:
        db.rollback()
        raise


def summary(row):
    from app.services.v2.object_query.resources import aware, now
    return {k: getattr(row, k) for k in ('id', 'ontology_id', 'owner_id', 'name', 'description',
        'lifecycle', 'definition_kind', 'head_version', 'etag', 'status', 'expires_at')} | {
        'expired': bool(row.expires_at and aware(row.expires_at) <= now())}


@router.post('/{ontology_id}/object-sets/validate')
def validate(request: ObjectSetDefinition, svc=Depends(service)):
    canonical, validation, manifest = svc.prepare(request)
    from app.services.v2.object_query.normalize import definition_hash
    return {'valid': True, 'result_type': validation.result_type, 'dependencies': manifest,
            'definition_hash': definition_hash(request.expression, canonical['parameter_schema'])}


@router.post('/{ontology_id}/object-sets/resources', status_code=201)
def create(request: CreateObjectSet, svc=Depends(service)):
    row = svc.create(request)
    return summary(row) | {'definition_hash': svc.version(row.id).definition_hash}


@router.get('/{ontology_id}/object-sets/resources')
def listing(offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200), svc=Depends(service)):
    return {'resources': [summary(v) for v in svc.list(offset, limit)]}


@router.get('/{ontology_id}/object-sets/resources/{rid}')
def get_resource(rid: str, svc=Depends(service)):
    row = svc.get(rid)
    return summary(row) | {'definition_hash': svc.version(rid).definition_hash}


@router.get('/{ontology_id}/object-sets/resources/{rid}/versions/{version}')
def get_version(rid: str, version: int, svc=Depends(service)):
    row = svc.version(rid, version)
    return {'resource_id': rid, 'version': row.version, 'definition': row.definition,
            'definition_hash': row.definition_hash, 'metadata_digest': row.metadata_digest,
            'dependencies': row.dependency_manifest}


@router.post('/{ontology_id}/object-sets/resources/{rid}/versions')
def new_version(rid: str, request: NewObjectSetVersion, svc=Depends(service)):
    return summary(svc.new_version(rid, request))


@router.patch('/{ontology_id}/object-sets/resources/{rid}')
def patch(rid: str, request: PatchObjectSet, svc=Depends(service)):
    changes = request.model_dump(exclude_none=True, exclude={'expected_etag'})
    row = svc.mutate(rid, request.expected_etag, changes, 'update')
    svc.db.commit()
    return summary(row)


@router.delete('/{ontology_id}/object-sets/resources/{rid}')
def delete(rid: str, expected_etag: int = Query(ge=1), svc=Depends(service)):
    row = svc.mutate(rid, expected_etag, {'status': 'tombstone'}, 'tombstone')
    svc.db.commit()
    return summary(row)


@router.put('/{ontology_id}/object-sets/resources/{rid}/grants')
def grant(rid: str, request: GrantObjectSet, svc=Depends(service)):
    return summary(svc.grant(rid, request))


@router.post('/{ontology_id}/object-sets/resources/{rid}/set-handle')
def set_handle(ontology_id: str, rid: str, request: SetHandleRequest | None = None,
               svc=Depends(service), graph_service: FalkorDBService = Depends(lambda: FalkorDBService())):
    if request is None:
        svc.get(rid)
        raise HTTPException(status_code=422, detail={'code': 'complete_set_unavailable', 'path': 'binding',
            'message': 'A pinned snapshot data_view_id is required to materialize a SetHandle', 'details': {}})
    resource = svc.get(rid)
    version = svc.version(rid)
    view = require_ready(svc.db, request.data_view_id, ontology_id, principal_id=svc.user.id, is_admin=svc.user.role == 'admin')
    if not graph_service.available:
        raise HTTPException(status_code=503, detail={'code': 'graph_unavailable', 'path': 'data_view_id', 'message': 'FalkorDB is unavailable', 'details': {}})
    graph = graph_service._graph(view.graph_key)
    definition = ObjectSetDefinition.model_validate(version.definition)
    load = LoadObjectSetRequest(expression=definition.expression, parameter_schema=definition.parameter_schema,
        parameters=request.parameters, read=ReadOptions(page_size=200),
        context=ExecutionContext(ontology_id=ontology_id, consistency='snapshot', data_view_id=view.id,
                                 metadata_digest=view.metadata_digest))
    core = QueryCore(svc.metadata, FalkorReadAdapter(graph, graph_ontology_id=view.graph_key), QueryPolicy(principal=svc.user.id))
    objects = []
    token = None
    for _ in range(50):
        page = load.model_copy(update={'read': load.read.model_copy(update={'page_token': token})})
        result = core.load(page, svc)
        objects.extend([item.model_dump(mode='json') for item in result.objects])
        if not result.page.has_more:
            break
        token = result.page.next_page_token
    else:
        raise HTTPException(status_code=422, detail={'code': 'query_too_complex', 'path': 'binding', 'message': 'SetHandle materialization budget exceeded', 'details': {}})
    lease = svc.lease(rid, version.version, request.lease_seconds)
    return {'kind': 'set_handle', 'resource_id': rid, 'definition_version': version.version,
            'lease_id': lease.id, 'data_view_id': view.id, 'consistency': 'snapshot',
            'objects': objects, 'count': len(objects), 'completeness': 'complete',
            'definition_hash': result.definition_hash, 'execution_hash': result.execution_hash}
