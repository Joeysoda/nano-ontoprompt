"""Periodic execution for due derived projections."""
from app.tasks.celery_app import celery_app


@celery_app.task(name="derived_projection.run_due")
def run_due():
    from app.database import SessionLocal
    from app.models.user import User
    from app.services.v2.derived_projections import due, run_projection
    from app.services.v2.object_query.authorization import require_ontology_access

    results = []
    with SessionLocal() as db:
        for row in due(db):
            owner = db.get(User, row.owner_id)
            if owner is None or not owner.is_active:
                row.schedule_error = {"code": "owner_unavailable", "message": "Projection owner is unavailable"}
                db.commit(); results.append({"projection_id": row.id, "status": "failed"}); continue
            try:
                access = require_ontology_access(db, row.ontology_id, str(owner.id), owner.role)
                run = run_projection(db, row, owner, access)
                results.append({"projection_id": row.id, "run_id": run.id, "status": run.status})
            except Exception:
                db.rollback(); row = db.get(type(row), row.id)
                row.schedule_error = {"code": "schedule_failed", "message": "Scheduled projection failed before creating a run"}
                db.commit(); results.append({"projection_id": row.id, "status": "failed"})
    return {"results": results}
