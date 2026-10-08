"""Celery worker entry point for complete Object Query materialization."""
from app.tasks.celery_app import celery_app

@celery_app.task(name="v2.object_query_materialize")
def run_object_query_materialize(job_id: str) -> None:
    from app.database import SessionLocal
    from app.models.user import User
    from app.models.v2.query_job import QueryJob
    from app.routers.v2.object_query import get_falkordb
    from app.schemas.v2.object_query import LoadObjectSetRequest
    from app.services.v2.object_query.job_runner import run_materialization_job
    db = SessionLocal()
    try:
        job = db.get(QueryJob, job_id)
        if job is None:
            return
        user = db.get(User, job.created_by)
        if user is None:
            job.status = "failed"; job.error_json = {"code": "not_found", "message": "Job principal not found"}; db.commit(); return
        run_materialization_job(db, job, LoadObjectSetRequest.model_validate(job.request_json), user, get_falkordb())
    finally:
        db.close()
