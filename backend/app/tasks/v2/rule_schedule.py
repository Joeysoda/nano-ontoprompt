"""Periodic rule evaluation; due rules are selected by their own interval."""
from app.tasks.celery_app import celery_app


@celery_app.task(name='rule_runtime.evaluate_due')
def evaluate_due():
    from app.database import SessionLocal
    from app.services.v2.graph.falkordb_service import FalkorDBService
    from app.services.v2.object_query.core import FalkorReadAdapter
    from app.services.v2.object_query.data_views import build_live_view
    from app.services.v2.object_query.errors import ObjectQueryError
    from app.services.v2.object_query.metadata import load_sql_metadata
    from app.services.v2.rule_scheduler import run_due

    graph = FalkorDBService()

    def reader(view):
        if not graph.available:
            raise ObjectQueryError('graph_unavailable', 'execution', 'FalkorDB is unavailable')
        return FalkorReadAdapter(graph._graph(view.graph_key), graph_ontology_id=view.graph_key)

    def live_view_builder(db, ontology_id, owner):
        if not graph.available:
            raise ObjectQueryError('graph_unavailable', 'execution', 'FalkorDB is unavailable')
        return build_live_view(db, graph, load_sql_metadata(db, ontology_id), ontology_id, owner.id,
                               'scheduled-rule-evaluation', 86400)

    with SessionLocal() as db:
        return {'results': run_due(db, reader=reader, live_view_builder=live_view_builder)}
