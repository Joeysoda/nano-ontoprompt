"""Manifest and immutable view helpers for Object Query."""
from datetime import datetime, timedelta, timezone
from app.models.v2.query_view import QueryDataView
from .core import FalkorReadAdapter, metadata_digest
from .errors import ObjectQueryError
from .normalize import stable_hash


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def as_response(view):
    return {
        "view_id": view.id,
        "ontology_id": view.ontology_id,
        "status": view.status,
        "metadata_digest": view.metadata_digest,
        "source_manifest_digest": view.source_manifest_digest,
        "base_view_id": view.base_view_id,
        "changeset_digest": view.changeset_digest,
        "changeset_version": view.changeset_version,
        "builder_version": view.builder_version,
        "index_version": view.index_version,
        "object_count": view.object_count,
        "edge_count": view.edge_count,
        "content_digest": view.content_digest,
        "created_at": view.created_at.isoformat(),
        "retention_until": view.retention_until.isoformat() if view.retention_until else None,
    }


def require_ready(db, view_id, ontology_id, *, principal_id=None, is_admin=False):
    view = db.get(QueryDataView, view_id)
    if view is None or view.ontology_id != ontology_id:
        raise ObjectQueryError("not_found", "context.data_view_id", "Data view not found")
    if principal_id is not None and not is_admin and view.created_by != principal_id:
        raise ObjectQueryError("forbidden", "context.data_view_id", "Data view is not authorized for this principal")
    if view.retention_until and aware(view.retention_until) <= now():
        if view.status != "expired":
            view.status = "expired"
            db.commit()
        raise ObjectQueryError("view_expired", "context.data_view_id", "Data view has expired")
    if view.status != "ready":
        raise ObjectQueryError("view_unavailable", "context.data_view_id", "Data view is not ready", status=view.status)
    return view


def build_live_view(db, graph_service, metadata, ontology_id, principal_id, source_manifest_digest, retention_seconds):
    """Copy a bounded live graph into an isolated immutable graph and publish atomically."""
    source = graph_service._graph(ontology_id)
    source_data = FalkorReadAdapter(source, max_objects=100000, max_edges=500000).read(ontology_id)
    view = QueryDataView(
        ontology_id=ontology_id,
        source_manifest_digest=source_manifest_digest,
        metadata_digest=metadata_digest(metadata),
        graph_key=f"view_{stable_hash({'ontology': ontology_id, 'source': source_manifest_digest, 'digest': metadata_digest(metadata)})[:48]}",
        status="building",
        created_by=principal_id,
        retention_until=now() + timedelta(seconds=retention_seconds),
    )
    db.add(view)
    db.flush()
    instances = [{"id": object_id, "entity_type": type_name,
                  "properties": {k: v for k, v in props.items() if not k.startswith("_")}}
                 for (type_name, object_id), props in source_data.objects.items()]
    relations = [{"source": source_id[1], "target": target_id[1], "type": relation}
                 for source_id, relation, target_id in source_data.edges]
    try:
        view.status = "validating"
        graph_service.upsert_instances(view.graph_key, instances)
        graph_service.upsert_relations(view.graph_key, relations)
        # A live graph has no MVCC snapshot API.  Read it again after the copy
        # and refuse publication if the source changed during capture.  This
        # prevents a mixed object/relation cut from being advertised as a
        # strict snapshot; a future coordinated source adapter can replace
        # this bounded consistency check.
        source_after = FalkorReadAdapter(source, max_objects=100000, max_edges=500000).read(ontology_id)
        after_digest = stable_hash({"objects": sorted(source_after.objects), "edges": sorted(source_after.edges)})
        before_digest = stable_hash({"objects": sorted(source_data.objects), "edges": sorted(source_data.edges)})
        if before_digest != after_digest:
            view.status = "failed"
            db.commit()
            raise ObjectQueryError("snapshot_unavailable", "data_view", "Source graph changed during snapshot capture")
        view.object_count = len(instances)
        view.edge_count = len(relations)
        view.content_digest = stable_hash({"objects": sorted(source_data.objects), "edges": sorted(source_data.edges)})
        view.status = "ready"
        db.commit()
    except ObjectQueryError:
        if db.is_active:
            db.commit()
        raise
    except Exception:
        view.status = "failed"
        db.commit()
        raise ObjectQueryError("view_build_failed", "data_view", "Data view build failed")
    return view
