"""Resolve Workshop data sources that read the Ontology directly.

An ``object_set`` source reads every object of one type (optionally filtered)
from a snapshot data view, so the widgets that count, sum or rank rows see a
complete set rather than one live page.  The snapshot is shared by every
source of the app and reused until it expires or the viewer asks for a new
one, which is what the app's refresh control does.  An ``alerts`` source reads
rule alerts through the same visibility filter as the Alert Center.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.v2.query_view import QueryDataView
from app.schemas.v2.object_query import (
    AndFilter, BaseObjectSet, ComparisonFilter, ExecutionContext, FilterObjectSet, LoadObjectSetRequest, PropertyRef,
    ReadOptions, TypeRef,
)
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.authorization import build_query_policy, policies_from_projection
from app.services.v2.object_query.core import FalkorReadAdapter, QueryCore, metadata_digest
from app.services.v2.object_query.data_views import build_live_view
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata

SNAPSHOT_SECONDS = 3600
MAX_OBJECTS = 10_000


def snapshot_key(app_id: str) -> str:
    return f"workshop:{app_id}"


def latest_snapshot(db, ontology_id: str, app_id: str, user, digest: str) -> QueryDataView | None:
    return (db.query(QueryDataView)
            .filter_by(ontology_id=ontology_id, source_manifest_digest=snapshot_key(app_id), status="ready",
                       created_by=user.id, metadata_digest=digest)
            .filter(QueryDataView.retention_until > datetime.now(timezone.utc))
            .order_by(QueryDataView.created_at.desc()).first())


def take_snapshot(db, ontology_id: str, app_id: str, user, graph: FalkorDBService | None = None) -> QueryDataView:
    graph = graph or FalkorDBService()
    if not graph.available:
        raise ObjectQueryError("graph_unavailable", "execution", "FalkorDB is unavailable")
    metadata = load_sql_metadata(db, ontology_id)
    return build_live_view(db, graph, metadata, ontology_id, user.id, snapshot_key(app_id), SNAPSHOT_SECONDS)


def object_type_properties(metadata, object_type: str) -> list[str]:
    item = metadata.resolve_type("object", object_type, "object_type")
    return [name for name in item.properties if not name.startswith("_")]


def resolve_object_set(db, ontology_id: str, app_id: str, source: dict, user, access) -> dict:
    graph = FalkorDBService()
    if not graph.available:
        raise ObjectQueryError("graph_unavailable", "execution", "FalkorDB is unavailable")
    metadata = load_sql_metadata(db, ontology_id)
    digest = metadata_digest(metadata)
    view = latest_snapshot(db, ontology_id, app_id, user, digest) or take_snapshot(db, ontology_id, app_id, user, graph)
    select = source.get("select") or object_type_properties(metadata, source["object_type"])
    expression = BaseObjectSet(kind="base", type_ref=TypeRef(api_name=source["object_type"]))
    if source.get("filters"):
        expression = FilterObjectSet(kind="filter", input=expression, where=AndFilter(kind="and", items=[
            ComparisonFilter(kind="comparison", property=PropertyRef(api_name=row["field"]), op=row["op"], value=row["value"])
            for row in source["filters"]]))
    revision_id = access.context.metadata_revision_id
    request = LoadObjectSetRequest(
        expression=expression,
        read=ReadOptions(select=[PropertyRef(api_name=name) for name in select], page_size=200, materialization_limit=MAX_OBJECTS),
        context=ExecutionContext(ontology_id=ontology_id, revision_policy="pinned" if revision_id else "latest", revision_id=revision_id,
                                 consistency="snapshot", metadata_digest=view.metadata_digest, data_view_id=view.id,
                                 projection_token=view.source_manifest_digest),
    )
    policy = build_query_policy(db, ontology_id, str(user.id), user.role, metadata_revision_id=revision_id,
                                requested_fields=tuple(select), preloaded_policies=policies_from_projection(access))
    core = QueryCore(metadata, FalkorReadAdapter(graph._graph(view.graph_key), graph_ontology_id=view.graph_key, max_objects=MAX_OBJECTS), policy)
    limit = int(source.get("limit") or MAX_OBJECTS)
    records, _ = core.materialize(request, max_pages=(limit + 199) // 200)
    objects = [{**record.properties, "id": record.object_id} for record in records[:limit]]
    return {"kind": "object_set", "status": "completed", "data_view_id": view.id, "completed_at": view.created_at.isoformat(),
            "outputs": {"objects": objects, "count": len(objects), "object_type": source["object_type"], "truncated": len(records) > limit}}


def resolve_alerts(db, ontology_id: str, source: dict, user) -> dict:
    from app.routers.v2.rule_runtime import list_alerts
    found = list_alerts(ontology_id, status=source.get("status"), subject_type=source.get("subject_type"), limit=source.get("limit") or 200,
                        db=db, user=user)
    alerts = [{**row, "subject_type": row["subject"]["object_type"], "subject_id": row["subject"]["object_id"]} for row in found["alerts"]]
    return {"kind": "alerts", "status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(),
            "outputs": {"alerts": alerts, "count": len(alerts), "total": found["total"]}}
