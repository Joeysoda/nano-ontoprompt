"""Explicit push source mapping and timezone-aware event time contract."""
import pytest

from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.temporal_replay import TemporalStreamEvent
from app.models.v2.temporal_replay import TemporalFact
from app.services.v2 import temporal_stream_service as streams
from app.services.v2.graph.falkordb_service import FalkorDBService


@pytest.fixture
def isolated_stream_graph():
    graphs = []
    yield graphs
    service = FalkorDBService()
    if service.available:
        for ontology_id, namespace in graphs:
            assert service.delete_graph(ontology_id, namespace=namespace)


def test_generic_push_requires_mapping_and_revision(db, admin_user):
    ontology = OntologyProject(id="generic-stream", name="Sensor", domain="test",
                               data_class="temporal", created_by=admin_user.id)
    db.add(ontology)
    db.commit()
    with pytest.raises(streams.StreamError, match="mapping"):
        streams.create_stream_run(db, ontology.id, source_mode="push", source_id="sensor-a")
    config = {"mapping": {"entity_type": "Machine", "properties": {"temperature": "temperature"}}}
    with pytest.raises(streams.StreamError) as error:
        streams.create_stream_run(db, ontology.id, source_mode="push", source_id="sensor-a", config=config)
    assert error.value.code == "SCHEMA_REVISION_REQUIRED"


def test_generic_push_event_time_projects_state_and_rejects_late(db, admin_user, monkeypatch, isolated_stream_graph):
    ontology = OntologyProject(id="generic-stream", name="Sensor", domain="test",
                               data_class="temporal", created_by=admin_user.id)
    db.add(ontology)
    db.flush()
    revision = OntologyRevision(id="generic-revision", ontology_id=ontology.id,
                                snapshot_json={"entities": [{"id": "machine-type", "name_en": "Machine"}]}, is_current=True)
    db.add(revision)
    ontology.current_revision_id = revision.id
    db.commit()
    monkeypatch.setattr(streams, "dispatch_stream", lambda _run_id: "test")
    config = {"mapping": {"entity_type": "Machine", "properties": {"temp": "temperature"}},
              "time_kind": "event_time", "late_event_policy": "reject"}
    replay = streams.create_stream_run(db, ontology.id, source_mode="push",
                                       source_id="sensor-a", config=config)
    isolated_stream_graph.append((ontology.id, replay.graph_namespace))
    assert replay.source_id == "sensor-a" and replay.schema_revision_id == revision.id
    body = {"event_id": "one", "episode_id": "line-a", "entity_key": "machine-1",
            "event_time": "2026-09-30T10:00:00-05:00", "source_sequence": 0,
            "payload": {"temp": 21}}
    event, duplicate = streams.ingest_push_event(db, replay, body)
    assert duplicate is False
    assert event.source_ref["event_time"] == "2026-09-30T10:00:00-05:00"
    serialized = streams._serialize_event(event)
    assert serialized["event_time"] == "2026-09-30T10:00:00-05:00"
    assert serialized["ingestion_time"]
    result = streams.process_one_event(db, replay, event)
    assert result["event"]["status"] == "committed"
    assert db.query(TemporalFact).filter(TemporalFact.replay_id == replay.id,
                                         TemporalFact.subject_id == "machine-1",
                                         TemporalFact.predicate == "temperature").count() == 1
    same, duplicate = streams.ingest_push_event(db, replay, body)
    assert duplicate and same.id == event.id
    with pytest.raises(streams.StreamError) as error:
        streams.ingest_push_event(db, replay, {**body, "event_id": "late",
                                              "event_time": "2026-09-30T14:59:59Z", "source_sequence": 1})
    assert error.value.code == "LATE_EVENT_NOT_SUPPORTED"
    with pytest.raises(streams.StreamError) as error:
        streams.ingest_push_event(db, replay, {**body, "event_id": "naive",
                                              "event_time": "2026-09-30T15:01:00", "source_sequence": 1})
    assert error.value.code == "INVALID_EVENT_TIME"
    next_event, _ = streams.ingest_push_event(db, replay, {**body, "event_id": "two",
                                                            "event_time": "2026-09-30T15:01:00Z",
                                                            "source_sequence": 1,
                                                            "payload": {"temp": 22}})
    streams.process_one_event(db, replay, next_event)
    facts = db.query(TemporalFact).filter(TemporalFact.replay_id == replay.id,
                                          TemporalFact.predicate == "temperature").all()
    assert len(facts) == 2
    assert sorted(fact.status for fact in facts) == ["active", "expired"]
    history = streams._sql_stream_graph(db, replay, limit=20, offset=0, at=None,
                                        mode="cumulative", relation_state="current")
    assert history["nodes"] == [{"id": "machine-1", "entity_type": "Machine",
                                  "labels": ["Instance"], "properties": {"temperature": 22},
                                  "node_kind": "instance"}]
    first_time = float(event.ordinal)
    earlier = streams.stream_graph(db, replay, limit=20, at=first_time, relation_state="current")
    assert earlier["nodes"][0]["properties"]["temperature"] == 21
    current = streams.stream_graph(db, replay, limit=20, relation_state="current")
    assert current["nodes"][0]["properties"]["temperature"] == 22
    replay.status = "completed"
    db.commit()
    published = streams.publish_stream_run(db, replay, created_by=admin_user.id)
    assert published["snapshot"]["event_count"] == 2


def test_generic_file_and_push_have_same_snapshot_hash(db, admin_user, monkeypatch, isolated_stream_graph):
    ontology = OntologyProject(id="generic-file-stream", name="Sensor", domain="test",
                               data_class="temporal", created_by=admin_user.id)
    db.add(ontology)
    db.flush()
    revision = OntologyRevision(id="generic-file-revision", ontology_id=ontology.id,
                                snapshot_json={"entities": [{"id": "machine-type", "name_en": "Machine"}]},
                                is_current=True)
    db.add(revision)
    ontology.current_revision_id = revision.id
    dataset = Dataset(id="generic-dataset", name="sensor file", kind="structured",
                      data_class="temporal", schema_json={"source_id": "sensor-a", "filename": "sensor.json"})
    version = DatasetVersion(id="generic-version", dataset_id=dataset.id, version_no=1,
                             storage_uri="s3://fixture", checksum="checksum", rowcount=1)
    dataset.latest_version_id = version.id
    db.add_all([dataset, version])
    db.commit()
    row = {"episode_id": "line-a", "machine_id": "machine-1",
           "time": "2026-09-30T15:00:00Z", "temp": 21}
    monkeypatch.setattr(streams, "_load_rows", lambda _db, _version: [dict(row)])
    monkeypatch.setattr(streams, "dispatch_stream", lambda _run_id: "test")
    config = {"mapping": {"entity_type": "Machine", "entity_key_field": "machine_id",
                          "properties": {"temp": "temperature"}},
              "time_kind": "event_time", "late_event_policy": "reject"}
    file_run = streams.create_stream_run(db, ontology.id, source_mode="file_replay",
                                         source_id="sensor-a", dataset_id=dataset.id,
                                         time_column="time", config=config)
    isolated_stream_graph.append((ontology.id, file_run.graph_namespace))
    file_event = db.query(TemporalStreamEvent).filter_by(replay_id=file_run.id).one()
    assert streams._serialize_event(file_event)["event_time"] == "2026-09-30T15:00:00+00:00"
    streams.process_one_event(db, file_run, file_event)
    file_run.status = "completed"
    db.commit()
    file_snapshot = streams.publish_stream_run(db, file_run, created_by=admin_user.id)["snapshot"]

    push_run = streams.create_stream_run(db, ontology.id, source_mode="push",
                                         source_id="sensor-a", config=config)
    isolated_stream_graph.append((ontology.id, push_run.graph_namespace))
    push_event, _ = streams.ingest_push_event(db, push_run, {
        "event_id": "push-1", "episode_id": "line-a", "entity_key": "machine-1",
        "event_time": row["time"], "source_sequence": 0, "payload": row,
    })
    streams.process_one_event(db, push_run, push_event)
    push_run.status = "completed"
    db.commit()
    push_snapshot = streams.publish_stream_run(db, push_run, created_by=admin_user.id)["snapshot"]
    assert file_snapshot["snapshot_hash"] == push_snapshot["snapshot_hash"]
