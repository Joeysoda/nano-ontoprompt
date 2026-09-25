from app.services.v2.temporal_replay_service import build_factorynet_replay_batches
from app.services.v2.temporal_service import FactoryNetIncrementalState, build_factorynet_instances_incremental
from app.models.v2.dataset import Dataset, DatasetVersion
from app.routers.v2.temporal_replays import TemporalReplayCreate, create_replay


def _rows(count=6):
    return [
        {
            "episode_id": "ep-a" if index % 2 == 0 else "ep-b",
            "machine_type": "CNC_Mill_3_Axis",
            "time_s": float(index),
            "ctx_process_phase": "roughing",
            "ctx_tool_condition": "new" if index < 3 else "worn",
            "ctx_passed_visual_inspection": True,
            "feedback_x": index * 0.1,
            "_source_row_index": index,
        }
        for index in range(count)
    ]


def test_replay_schedule_is_deterministic_and_preserves_all_series():
    batches, summary = build_factorynet_replay_batches(_rows(), series_ids=["ep-a", "ep-b"], window_seconds=2)
    assert summary["selected_rows"] == 6
    assert summary["series_ids"] == ["ep-a", "ep-b"]
    assert [row["_source_row_index"] for batch in batches for row in batch["rows"]] == list(range(6))
    assert all(batch["time_from"] <= batch["time_to"] for batch in batches)


def test_incremental_builder_keeps_next_observation_across_batches():
    rows = _rows()
    first, second = rows[:3], rows[3:]
    first = [dict(row, _replay_event_seq=index) for index, row in enumerate(first)]
    second = [dict(row, _replay_event_seq=index + 1) for index, row in enumerate(second)]
    nodes_a, edges_a, state = build_factorynet_instances_incremental(first, FactoryNetIncrementalState())
    nodes_b, edges_b, state = build_factorynet_instances_incremental(second, state)
    observation_ids = {node["id"] for node in nodes_a + nodes_b if node["entity_type"] == "Observation"}
    assert len(observation_ids) == 6
    assert any(edge["type"] == "NEXT_OBSERVATION" for edge in edges_b)
    assert state.sequence_by_episode["ep-a"] >= 3


def test_incremental_builder_only_emits_static_links_once_after_checkpoint():
    rows = _rows(4)
    first = [dict(rows[0], _replay_event_seq=0)]
    second = [dict(row, _replay_event_seq=index + 1) for index, row in enumerate(rows[1:], start=1)]
    nodes_a, edges_a, state = build_factorynet_instances_incremental(first, FactoryNetIncrementalState())
    nodes_b, edges_b, _ = build_factorynet_instances_incremental(second, state)
    assert any(node["entity_type"] == "Machine" for node in nodes_a)
    assert not any(node["entity_type"] == "Machine" for node in nodes_b)
    assert sum(edge["type"] == "HAS_EPISODE" for edge in edges_a) == 1
    # The second chunk introduces ep-b once, but must not repeat ep-a's
    # static link from the checkpoint.
    assert sum(edge["type"] == "HAS_EPISODE" for edge in edges_b) == 1
    assert not any(edge["target"] == "FactoryNet:Episode:ep-a" for edge in edges_b)


def test_batch_split_and_whole_build_have_same_observation_ids():
    rows = _rows()
    batches, _ = build_factorynet_replay_batches(rows, series_ids=["ep-a", "ep-b"], window_seconds=1)
    whole_nodes, _, _ = build_factorynet_instances_incremental(
        [dict(row, _replay_event_seq=index) for index, row in enumerate(rows)],
        FactoryNetIncrementalState(),
    )
    state = FactoryNetIncrementalState()
    split_nodes = []
    for batch in batches:
        nodes, _, state = build_factorynet_instances_incremental(batch["rows"], state)
        split_nodes.extend(nodes)
    whole_ids = {node["id"] for node in whole_nodes if node["entity_type"] == "Observation"}
    split_ids = {node["id"] for node in split_nodes if node["entity_type"] == "Observation"}
    assert split_ids == whole_ids


def test_invalid_time_is_kept_for_worker_validation_instead_of_crashing_scheduler():
    rows = _rows(2)
    rows[1]["time_s"] = "not-a-number"
    batches, summary = build_factorynet_replay_batches(rows, series_ids=["ep-a", "ep-b"])
    assert summary["selected_rows"] == 2
    assert sum(batch["source_rows"] for batch in batches) == 2
    assert any("not-a-number" == batch_row["time_s"] for batch in batches for batch_row in batch["rows"])


def test_replay_schedule_accepts_a_user_selected_numeric_time_column():
    rows = [dict(row, elapsed=row.pop("time_s")) for row in _rows(4)]
    batches, summary = build_factorynet_replay_batches(rows, time_column="elapsed", window_seconds=2)
    assert summary["time_column"] == "elapsed"
    assert summary["selected_rows"] == 4
    assert batches[0]["time_from"] == 0


def test_replay_max_records_is_time_uniform_and_keeps_each_episode():
    batches, summary = build_factorynet_replay_batches(_rows(10), max_records=4, window_seconds=1)
    selected = [row for batch in batches for row in batch["rows"]]
    assert summary["selected_rows"] == 4
    assert {row["episode_id"] for row in selected} == {"ep-a", "ep-b"}
    assert selected[0]["time_s"] == 0
    assert selected[-1]["time_s"] == 9


def test_replay_never_exceeds_explicit_limit_when_series_outnumber_rows():
    batches, summary = build_factorynet_replay_batches(_rows(10), max_records=1, window_seconds=1)
    selected = [row for batch in batches for row in batch["rows"]]
    assert len(selected) == 1
    assert summary["selected_rows"] == 1


def test_create_replay_defaults_to_one_episode(db, admin_user, monkeypatch):
    dataset = Dataset(id="factorynet-replay-dataset", name="FactoryNet fixture", kind="structured", data_class="temporal", schema_json={"source_id": "factorynet_cnc", "filename": "fixture.parquet", "sha256": "abc"})
    version = DatasetVersion(id="factorynet-replay-version", dataset_id=dataset.id, version_no=1, storage_uri="s3://fixture", checksum="abc")
    dataset.latest_version_id = version.id
    db.add_all([dataset, version]); db.commit()
    monkeypatch.setattr("app.routers.v2.temporal_replays._read_factorynet", lambda _db, _version: _rows())
    result = create_replay(TemporalReplayCreate(dataset_id=dataset.id, window_seconds=2), db, admin_user)
    assert result["status"] == "created"
    assert result["series_ids"] == ["ep-a"]
    assert result["selected_rows"] == 3
    assert result["total_batches"] > 0
