from app.models.ontology import OntologyProject
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.temporal_replay import DataModelSnapshot, TemporalFact, TemporalReplay, TemporalStreamEvent
from app.services.v2 import temporal_stream_service as streams


def _rows():
    return [
        {
            "episode_id": "episode-a",
            "machine_type": "CNC_Mill_3_Axis",
            "time_s": float(index),
            "ctx_process_phase": "roughing" if index < 2 else "finishing",
            "ctx_tool_condition": "unworn" if index < 2 else "worn",
            "ctx_passed_visual_inspection": True,
            "feedback_force": index * 0.1,
            "_source_row_index": index,
        }
        for index in range(3)
    ] + [
        {
            "episode_id": "episode-b",
            "machine_type": "CNC_Mill_3_Axis",
            "time_s": 0.0,
            "ctx_process_phase": "roughing",
            "ctx_tool_condition": "unworn",
            "ctx_passed_visual_inspection": True,
            "feedback_force": 0.0,
            "_source_row_index": 3,
        }
    ]


def _source(db, admin_user):
    ontology = OntologyProject(
        id="stream-ontology",
        name="FactoryNet stream fixture",
        domain="制造",
        data_class="temporal",
        created_by=admin_user.id,
    )
    dataset = Dataset(
        id="stream-dataset",
        name="FactoryNet fixture",
        kind="structured",
        data_class="temporal",
        schema_json={"source_id": "factorynet_cnc", "filename": "fixture.parquet"},
    )
    version = DatasetVersion(
        id="stream-version",
        dataset_id=dataset.id,
        version_no=1,
        storage_uri="s3://fixture",
        checksum="fixture-checksum",
        rowcount=len(_rows()),
    )
    dataset.latest_version_id = version.id
    db.add_all([ontology, dataset, version])
    db.commit()
    return ontology, dataset, version


def test_stream_defaults_to_one_episode_and_processes_one_event(db, admin_user, monkeypatch):
    ontology, dataset, version = _source(db, admin_user)
    monkeypatch.setattr(streams, "_resolve_source", lambda _db, _dataset_id, _version_id: (dataset, version, _rows()))

    replay = streams.create_stream_run(db, ontology.id, dataset_id=dataset.id)
    events = db.query(TemporalStreamEvent).filter(TemporalStreamEvent.replay_id == replay.id).all()
    assert replay.series_ids == ["episode-a"]
    assert len(events) == 3
    assert replay.graph_namespace.startswith("stream_")

    result = streams.process_one_event(db, replay, events[0])
    assert result["event"]["status"] == "committed"
    assert replay.committed_events == 1
    assert db.query(TemporalFact).filter(TemporalFact.replay_id == replay.id).count() > 0


def test_stream_state_fact_expires_and_duplicate_is_idempotent(db, admin_user, monkeypatch):
    ontology, dataset, version = _source(db, admin_user)
    monkeypatch.setattr(streams, "_resolve_source", lambda _db, _dataset_id, _version_id: (dataset, version, _rows()))
    replay = streams.create_stream_run(db, ontology.id, dataset_id=dataset.id, episode_ids=["episode-a"])
    events = db.query(TemporalStreamEvent).filter(TemporalStreamEvent.replay_id == replay.id).order_by(TemporalStreamEvent.source_sequence).all()

    streams.process_one_event(db, replay, events[0])
    first_committed = replay.committed_events
    streams.process_one_event(db, replay, events[2])
    active = db.query(TemporalFact).filter(
        TemporalFact.replay_id == replay.id,
        TemporalFact.state_key == "tool_condition",
        TemporalFact.status == "active",
        TemporalFact.valid_to_ordinal.is_(None),
    ).all()
    expired = db.query(TemporalFact).filter(
        TemporalFact.replay_id == replay.id,
        TemporalFact.state_key == "tool_condition",
        TemporalFact.status == "expired",
    ).all()
    assert len(active) == 1
    assert expired and expired[0].valid_to_ordinal == events[2].ordinal

    # Replaying the already committed event cannot add another fact or move
    # the watermark.
    result = streams.process_one_event(db, replay, events[2])
    assert result["idempotent"] is True
    assert replay.committed_events == first_committed + 1


def test_stream_runs_keep_graph_namespaces_isolated(db, admin_user, monkeypatch):
    ontology, dataset, version = _source(db, admin_user)
    monkeypatch.setattr(streams, "_resolve_source", lambda _db, _dataset_id, _version_id: (dataset, version, _rows()))
    first = streams.create_stream_run(db, ontology.id, dataset_id=dataset.id, episode_ids=["episode-a"])
    second = streams.create_stream_run(db, ontology.id, dataset_id=dataset.id, episode_ids=["episode-a"])
    assert first.graph_namespace != second.graph_namespace
    assert db.query(TemporalStreamEvent).filter(TemporalStreamEvent.replay_id == first.id).count() == 3
    assert db.query(TemporalStreamEvent).filter(TemporalStreamEvent.replay_id == second.id).count() == 3


def test_completed_stream_publishes_immutable_snapshot(db, admin_user, monkeypatch):
    ontology, dataset, version = _source(db, admin_user)
    monkeypatch.setattr(streams, "_resolve_source", lambda _db, _dataset_id, _version_id: (dataset, version, _rows()))
    replay = streams.create_stream_run(db, ontology.id, dataset_id=dataset.id, episode_ids=["episode-a"])
    events = db.query(TemporalStreamEvent).filter(TemporalStreamEvent.replay_id == replay.id).order_by(TemporalStreamEvent.source_sequence).all()
    for event in events:
        streams.process_one_event(db, replay, event)
    replay.status = "completed"
    db.commit()

    result = streams.publish_stream_run(db, replay, created_by=admin_user.id)
    assert result["snapshot"]["event_count"] == len(events)
    snapshot = db.query(DataModelSnapshot).filter(DataModelSnapshot.id == result["snapshot"]["id"]).one()
    db.refresh(ontology)
    assert snapshot.graph_namespace == replay.graph_namespace
    assert ontology.current_data_snapshot_id == snapshot.id
    assert replay.status == "published"


def test_push_event_idempotency_and_late_event_gate(db, admin_user, monkeypatch):
    ontology, _dataset, _version = _source(db, admin_user)
    monkeypatch.setattr(streams, "dispatch_stream", lambda _run_id: "test")
    replay = streams.create_stream_run(db, ontology.id, source_mode="push")
    body = {
        "event_id": "push-1",
        "episode_id": "episode-a",
        "entity_key": "CNC_Mill_3_Axis",
        "ordinal": 1,
        "source_sequence": 0,
        "payload": {
            "time_s": 1,
            "ctx_process_phase": "roughing",
            "ctx_tool_condition": "unworn",
            "ctx_passed_visual_inspection": True,
        },
        "source_ref": {"source_row_id": "push-1"},
    }
    event, idempotent = streams.ingest_push_event(db, replay, body)
    assert idempotent is False
    same, idempotent = streams.ingest_push_event(db, replay, body)
    assert idempotent is True
    assert same.id == event.id
    replay.watermark_ordinal = 1
    replay.watermark_sequence = 0
    db.commit()
    try:
        streams.ingest_push_event(db, replay, {**body, "event_id": "push-0", "ordinal": 0, "source_sequence": 1})
    except streams.StreamError as exc:
        assert exc.code == "LATE_EVENT_NOT_SUPPORTED"
    else:
        raise AssertionError("expected a late event rejection")


def test_file_and_push_sequences_share_snapshot_hash(db, admin_user, monkeypatch):
    ontology, dataset, version = _source(db, admin_user)
    monkeypatch.setattr(streams, "_resolve_source", lambda _db, _dataset_id, _version_id: (dataset, version, _rows()))
    monkeypatch.setattr(streams, "dispatch_stream", lambda _run_id: "test")

    file_run = streams.create_stream_run(db, ontology.id, dataset_id=dataset.id, episode_ids=["episode-a"])
    file_events = db.query(TemporalStreamEvent).filter(
        TemporalStreamEvent.replay_id == file_run.id,
    ).order_by(TemporalStreamEvent.source_sequence.asc()).all()
    for event in file_events:
        streams.process_one_event(db, file_run, event)
    file_run.status = "completed"
    db.commit()
    file_snapshot = streams.publish_stream_run(db, file_run, created_by=admin_user.id)["snapshot"]

    push_run = streams.create_stream_run(db, ontology.id, source_mode="push")
    for source_event in file_events:
        streams.ingest_push_event(db, push_run, {
            "event_id": source_event.event_key,
            "episode_id": source_event.episode_id,
            "entity_key": source_event.entity_key,
            "ordinal": float(source_event.ordinal),
            "source_sequence": source_event.source_sequence,
            "payload": source_event.payload,
            "source_ref": {"source_row_id": source_event.source_row_id},
        })
    push_events = db.query(TemporalStreamEvent).filter(
        TemporalStreamEvent.replay_id == push_run.id,
    ).order_by(TemporalStreamEvent.source_sequence.asc()).all()
    for event in push_events:
        streams.process_one_event(db, push_run, event)
    push_run.status = "completed"
    db.commit()
    push_snapshot = streams.publish_stream_run(db, push_run, created_by=admin_user.id)["snapshot"]

    assert file_snapshot["snapshot_hash"] == push_snapshot["snapshot_hash"]
