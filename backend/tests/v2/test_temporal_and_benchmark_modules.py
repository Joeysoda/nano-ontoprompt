from app.services.v2.temporal_service import TemporalConfig, build_observation_instances, normalize_temporal_rows
from app.services.v2.benchmark_service import evaluate
from app.services.v2.datasets.temporal_adapters import get_adapter


def test_ordinal_temporal_semantics_do_not_invent_dates():
    rows, issues = normalize_temporal_rows(
        [{"unit_id": "u1", "cycle": 2, "sensor": 0.4}, {"unit_id": "u1", "cycle": "bad"}],
        TemporalConfig(time_kind="ordinal", sequence_column="cycle"),
    )
    assert rows[0]["event_seq"] == 2
    assert rows[0]["event_time"] is None
    assert issues and "missing sequence" not in issues[0]["error"]


def test_interval_rejects_reversed_bounds():
    rows, issues = normalize_temporal_rows(
        [{"unit_id": "u1", "start": "2024-02-02", "end": "2024-02-01"}],
        TemporalConfig(time_kind="interval", valid_from_column="start", valid_to_column="end"),
    )
    assert rows == []
    assert "precedes" in issues[0]["error"]


def test_cmapss_adapter_maps_cycle_to_ordinal():
    row = get_adapter("cmapss").normalize([{"unit": 7, "cycle": 3}])[0]
    assert row["unit_id"] == 7 and row["event_seq"] == 3


def test_observation_instances_have_stable_relation_semantics():
    nodes, edges = build_observation_instances([{"unit_id": "u1", "event_seq": 1, "sensor": 0.1}], entity_id_column="unit_id")
    assert {n["entity_type"] for n in nodes} == {"Equipment", "SensorReading"}
    assert edges[0]["type"] == "OBSERVED_ON"
    assert edges[0]["properties"]["event_seq"] == 1


def test_gold_metrics_separate_hallucination_and_schema_violation():
    result = evaluate(
        [{"id": "a", "type": "Equipment"}], [{"id": "a", "type": "Equipment"}],
        [("a", "HAS", "b"), ("x", "HALLUCINATED", "y")], [("a", "HAS", "b")],
        schema_nodes=[{"type": "Unknown"}], schema={"entity_types": ["Equipment"]},
    )
    assert result["entity"]["f1"] == 1.0
    assert result["hallucination_rate"] == 0.5
    assert result["schema"]["violation_count"] == 1
