"""Push stream -> allowed lateness -> published snapshot -> measurement-only TSP view (real FalkorDB)."""
import pytest

from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.v2.temporal_replay import TemporalFact
from app.services.v2 import temporal_stream_service as streams
from app.services.v2.graph.falkordb_service import FalkorDBService


@pytest.fixture
def stream_ontology(db, admin_user, monkeypatch):
    if not FalkorDBService().available:
        pytest.fail("Real FalkorDB is required for the published temporal surface")
    ontology = OntologyProject(id="late-stream", name="Sensors", domain="test", data_class="temporal", created_by=admin_user.id)
    db.add(ontology); db.flush()
    db.add(Entity(id="machine-type", ontology_id=ontology.id, name_cn="设备", name_en="Machine", canonical_id="ontology:Machine", type="EntityType",
                  properties={"property_definitions": [{"id": "temperature", "type": "double"}, {"id": "temperature_series", "type": "string"}]}))
    revision = OntologyRevision(id="late-revision", ontology_id=ontology.id, is_current=True,
                                snapshot_json={"entities": [{"id": "machine-type", "name_en": "Machine"}]})
    db.add(revision)
    ontology.current_revision_id = revision.id
    db.commit()
    monkeypatch.setattr(streams, "dispatch_stream", lambda _run_id: "test")
    namespaces = []
    yield ontology, namespaces
    service = FalkorDBService()
    for namespace in namespaces:
        service.delete_graph(ontology.id, namespace=namespace)


def push(db, replay, key, at, seq, temp):
    return streams.ingest_push_event(db, replay, {"event_id": key, "episode_id": "line-a", "entity_key": "machine-1",
                                                  "event_time": at, "source_sequence": seq,
                                                  "payload": {"temp": temp, "machine": "machine-1", "at": at}})


def test_late_reading_inside_window_reaches_tsp_but_not_state_facts(client, auth_headers, db, admin_user, stream_ontology):
    ontology, namespaces = stream_ontology
    with pytest.raises(streams.StreamError) as caught:
        streams.create_stream_run(db, ontology.id, source_mode="push", source_id="sensor-a", config={
            "mapping": {"entity_type": "Machine", "properties": {"temp": "temperature"}},
            "time_kind": "ordinal", "late_event_policy": "allowed_lateness", "allowed_lateness_seconds": 60})
    assert caught.value.code == "UNSUPPORTED_LATE_EVENT_POLICY"
    replay = streams.create_stream_run(db, ontology.id, source_mode="push", source_id="sensor-a", config={
        "mapping": {"entity_type": "Machine", "properties": {"temp": "temperature"}},
        "time_kind": "event_time", "late_event_policy": "allowed_lateness", "allowed_lateness_seconds": 3600})
    namespaces.append(replay.graph_namespace)
    for key, at, seq, temp in [("e1", "2026-10-18T10:00:00Z", 0, 21.0), ("e2", "2026-10-18T11:00:00Z", 1, 23.0)]:
        event, _ = push(db, replay, key, at, seq, temp)
        streams.process_one_event(db, replay, event)
    late, duplicate = push(db, replay, "late", "2026-10-18T10:30:00Z", 2, 22.0)
    assert late.status == "late" and duplicate is False
    assert push(db, replay, "late", "2026-10-18T10:30:00Z", 2, 22.0)[1] is True  # idempotent redelivery
    with pytest.raises(streams.StreamError) as caught:
        push(db, replay, "ancient", "2026-10-18T09:59:00Z", 3, 19.0)
    assert caught.value.code == "LATE_EVENT_BEYOND_ALLOWED_LATENESS"
    db.refresh(replay)
    assert replay.metrics["late_events"] == 1 and replay.total_events == 2
    assert db.query(TemporalFact).filter_by(replay_id=replay.id, predicate="temperature").count() == 2  # late value never rewrites state

    replay.status = "completed"; db.commit()
    snapshot = streams.publish_stream_run(db, replay, created_by=admin_user.id)["snapshot"]
    view = client.post(f"/api/v2/ontologies/{ontology.id}/object-query/data-views", headers=auth_headers, json={
        "source_ontology_id": ontology.id, "source_manifest_digest": "late-stream", "source_snapshot_id": snapshot["id"],
        "temporal_surface": {"materialize_events": False, "start_field": "at", "subject_type": "Machine", "subject_field": "machine",
                             "channels": [{"value_field": "temp", "tsp_property": "temperature_series", "unit": "C", "interpolation": "linear"}]}})
    assert view.status_code == 201, view.text
    body = view.json()
    machine = client.post(f"/api/v2/ontologies/{ontology.id}/object-query/load", headers=auth_headers, json={
        "expression": {"kind": "base", "type_ref": {"api_name": "Machine"}},
        "context": {"ontology_id": ontology.id, "consistency": "snapshot", "data_view_id": body["view_id"]}}).json()["objects"]
    assert len(machine) == 1 and body["object_count"] == 1  # measurements created no Event Objects
    series_id = machine[0]["properties"]["temperature_series"]
    series = client.get(f"/api/v2/ontologies/{ontology.id}/object-query/data-views/{body['view_id']}/time-series/{series_id}",
                        headers=auth_headers).json()
    assert [(p["timestamp"], p["value"]) for p in series["points"]] == [
        ("2026-10-18T10:00:00+00:00", 21.0), ("2026-10-18T10:30:00+00:00", 22.0), ("2026-10-18T11:00:00+00:00", 23.0)]
    assert series["unit"] == "C"
