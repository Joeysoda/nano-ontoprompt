"""Periodic Tracking Scenario refresh; Pinned resources are never selected."""
from datetime import timedelta
from app.tasks.celery_app import celery_app


@celery_app.task(name='scenario_tracking.rebase_due')
def rebase_due():
    from app.database import SessionLocal
    from app.models.user import User
    from app.models.v2.scenario import ScenarioResource
    from app.services.v2.object_query.data_views import now, aware
    from app.services.v2.scenarios import ScenarioService
    from app.services.v2.scenario_tracking import rebase
    processed = 0
    with SessionLocal() as db:
        rows = db.query(ScenarioResource).filter_by(mode='tracking', status='active', protected_demo=False).all()
        candidates = [(row.id, row.ontology_id, row.owner_id) for row in rows
                      if (not row.last_rebased_at or aware(row.last_rebased_at) <= now() - timedelta(minutes=10))
                      and (not row.ttl_seconds or aware(row.created_at) + timedelta(seconds=row.ttl_seconds) > now())]
        for scenario_id, ontology_id, owner_id in candidates:
            owner = db.get(User, owner_id)
            if not owner:
                continue
            try:
                service = ScenarioService(db, ontology_id, owner)
                service.authorize_ontology()
                row = service.get(scenario_id, write=True)
                rebase(service, scenario_id, row.etag)
                processed += 1
            except Exception as exc:
                db.rollback()
                row = db.get(ScenarioResource, scenario_id)
                if row:
                    row.rebase_error = {'code': getattr(exc, 'code', 'rebase_failed'), 'message': 'Automatic rebase failed; retry or review the Scenario'}
                    db.commit()
    return {'processed': processed}
