"""Manifest and immutable view helpers for Object Query."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app.models.v2.query_view import QueryDataView
from .core import FalkorReadAdapter, GraphData, metadata_digest
from .errors import ObjectQueryError
from .normalize import stable_hash

SNAPSHOT_WRITE_BATCH_SIZE = 1000


def _batches(items, size=SNAPSHOT_WRITE_BATCH_SIZE):
    for start in range(0, len(items), size):
        yield items[start:start + size]


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
        "source_snapshot_id": view.source_snapshot_id,
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


def build_live_view(db, graph_service, metadata, ontology_id, principal_id, source_manifest_digest, retention_seconds, *, source_graph_key=None, source_snapshot_id=None):
    """Copy a bounded live graph into an isolated immutable graph and publish atomically."""
    source_key = source_graph_key or ontology_id
    # FalkorDB creates graphs lazily on the first write. A newly-created
    # ontology therefore has no source graph yet; represent that as a valid
    # empty snapshot without issuing GRAPH.RO_QUERY against a missing key.
    source_exists = graph_service.graph_exists(ontology_id, source_graph_key)
    source = graph_service._graph(source_key) if source_exists else None
    source_data = (
        FalkorReadAdapter(source, max_objects=100000, max_edges=500000).read(ontology_id)
        if source is not None else GraphData(objects={}, edges=[])
    )
    view = QueryDataView(
        ontology_id=ontology_id,
        source_manifest_digest=source_manifest_digest,
        source_snapshot_id=source_snapshot_id,
        metadata_digest=metadata_digest(metadata),
        graph_key=f"view_{uuid4().hex}",
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
        written_instances = sum(
            graph_service.upsert_instances(view.graph_key, batch)
            for batch in _batches(instances)
        )
        written_relations = sum(
            graph_service.upsert_relations(view.graph_key, batch)
            for batch in _batches(relations)
        )
        if written_instances != len(instances) or written_relations != len(relations):
            raise ObjectQueryError(
                "snapshot_write_incomplete", "data_view",
                "Data view graph did not accept every source record",
                expected_objects=len(instances), written_objects=written_instances,
                expected_edges=len(relations), written_edges=written_relations,
            )
        copied_exists = graph_service.graph_exists(ontology_id, view.graph_key)
        copied = (
            FalkorReadAdapter(
                graph_service._graph(view.graph_key),
                max_objects=max(len(instances), 1),
                max_edges=max(len(relations), 1),
                graph_ontology_id=view.graph_key,
            ).read(ontology_id)
            if copied_exists else GraphData(objects={}, edges=[])
        )
        if len(copied.objects) != len(instances) or len(copied.edges) != len(relations):
            raise ObjectQueryError(
                "snapshot_write_incomplete", "data_view",
                "Data view graph failed post-write verification",
                expected_objects=len(instances), copied_objects=len(copied.objects),
                expected_edges=len(relations), copied_edges=len(copied.edges),
            )
        # A live graph has no MVCC snapshot API.  Read it again after the copy
        # and refuse publication if the source changed during capture.  This
        # prevents a mixed object/relation cut from being advertised as a
        # strict snapshot; a future coordinated source adapter can replace
        # this bounded consistency check.
        source_still_exists = graph_service.graph_exists(ontology_id, source_graph_key)
        source_after = (
            FalkorReadAdapter(graph_service._graph(source_key), max_objects=100000, max_edges=500000).read(ontology_id)
            if source_still_exists else GraphData(objects={}, edges=[])
        )
        after_digest = stable_hash({"objects": sorted(source_after.objects.items()), "edges": sorted(source_after.edges)})
        before_digest = stable_hash({"objects": sorted(source_data.objects.items()), "edges": sorted(source_data.edges)})
        if before_digest != after_digest:
            view.status = "failed"
            db.commit()
            raise ObjectQueryError("snapshot_unavailable", "data_view", "Source graph changed during snapshot capture")
        view.object_count = len(instances)
        view.edge_count = len(relations)
        view.content_digest = before_digest
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
