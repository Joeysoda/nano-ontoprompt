"""Bounded Object Query materialization runner shared by HTTP and Celery."""
from app.schemas.v2.object_query import LoadObjectSetRequest
from app.services.v2.object_query.core import QueryCore, QueryPolicy, FalkorReadAdapter
from app.services.v2.object_query.authorization import build_query_policy, policies_from_projection, require_ontology_access
from app.services.v2.object_query.data_views import require_ready
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.resources import ResourceService

def run_materialization_job(db, job, request: LoadObjectSetRequest, user, graph_service):
    job.status = "running"
    try:
        metadata = load_sql_metadata(db, job.ontology_id)
        view = require_ready(db, request.context.data_view_id, job.ontology_id, principal_id=user.id, is_admin=user.role == "admin")
        graph = graph_service._graph(view.graph_key)
        access = require_ontology_access(db, job.ontology_id, str(user.id), user.role, metadata_revision_id=request.context.revision_id)
        policy = build_query_policy(
            db,
            job.ontology_id,
            str(user.id),
            user.role,
            metadata_revision_id=request.context.revision_id,
            requested_fields=tuple(item.api_name for item in request.read.select),
            preloaded_policies=policies_from_projection(access),
        )
        resolver = ResourceService(db, job.ontology_id, user, metadata, authorization_projection=access)
        core = QueryCore(
            metadata,
            FalkorReadAdapter(graph, graph_ontology_id=view.graph_key),
            policy,
        )
        page_token = None
        records = []
        for page_no in range(50):
            db.refresh(job)
            if job.cancel_requested:
                job.status = "cancelled"
                break
            page_request = request.model_copy(update={"read": request.read.model_copy(update={"page_size": 200, "page_token": page_token})})
            result = core.load(page_request, resolver)
            records.extend([obj.model_dump(mode="json") for obj in result.objects])
            job.checkpoint_json = {"pages": page_no + 1, "returned": len(records), "execution_hash": result.execution_hash}
            db.commit()
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
            job.status = "failed"
            job.error_json = {"code": "query_too_complex", "message": "Materialization page budget exceeded"}
    except ObjectQueryError as exc:
        job.status = "failed"
        job.error_json = {"code": exc.code, "path": exc.path, "message": exc.message}
    except Exception as exc:
        job.status = "failed"
        job.error_json = {"code": "execution_failed", "message": str(exc)}
    db.commit()
    return job
