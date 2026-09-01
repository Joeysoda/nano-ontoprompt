from app.services.v2.temporal_service import TemporalConfig, build_bts_instances, build_observation_instances, normalize_temporal_rows, summarize_temporal_rows
from app.services.v2.benchmark_service import evaluate
from app.services.v2.datasets.temporal_adapters import get_adapter
from app.services.v2.datasets.icews_adapter import build_icews_instances, icews_summary, normalize_icews_rows, parse_icews_tsv
from app.tasks.v2.temporal_construction import _filter_icews_rows


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


def test_bts_instances_keep_brick_stream_identity_and_time():
    nodes, edges = build_bts_instances([
        {"stream_id": "s1", "point_name": "温度", "brick_class": "Temperature_Sensor", "event_time": "2024-01-01T00:00:00+00:00", "value": "21.5", "site_id": "Site_B"},
        {"stream_id": "s1", "point_name": "温度", "brick_class": "Temperature_Sensor", "event_time": "2024-01-01T01:00:00+00:00", "value": "21.8", "site_id": "Site_B"},
    ])
    assert len(nodes) == 4  # building + point + two observations
    assert {n["entity_type"] for n in nodes} == {"Building", "Temperature_Sensor", "Observation"}
    assert {e["type"] for e in edges} == {"LOCATED_IN", "OBSERVED_ON"}
    assert sum(e["type"] == "OBSERVED_ON" for e in edges) == 2


def test_temporal_summary_is_json_safe_and_stable():
    summary = summarize_temporal_rows([
        {"stream_id": "b", "event_time": "2024-01-02T00:00:00+00:00", "value": 2},
        {"stream_id": "a", "event_time": "2024-01-01T00:00:00+00:00", "value": 1},
    ])
    assert summary["streams"] == 2
    assert summary["time_from"] < summary["time_to"]
    assert summary["time_kind"] == "instant"


def test_gold_metrics_separate_hallucination_and_schema_violation():
    result = evaluate(
        [{"id": "a", "type": "Equipment"}], [{"id": "a", "type": "Equipment"}],
        [("a", "HAS", "b"), ("x", "HALLUCINATED", "y")], [("a", "HAS", "b")],
        schema_nodes=[{"type": "Unknown"}], schema={"entity_types": ["Equipment"]},
    )
    assert result["entity"]["f1"] == 1.0
    assert result["hallucination_rate"] == 0.5
    assert result["schema"]["violation_count"] == 1


def test_icews_preserves_day_precision_and_fixed_relations():
    row = {
        "Event ID": "100", "Event Date": "2023-01-02", "Source Name": "A",
        "Source Country": "X", "Target Name": "B", "Target Country": "Y",
        "Event Text": "Consult", "CAMEO Code": "010", "Intensity": "-2",
        "Story ID": "s1", "Sentence Number": "1", "Publisher": "p",
        "City": "C", "District": "", "Province": "P", "Country": "Y",
        "Latitude": "1", "Longitude": "2",
    }
    rows, issues = normalize_icews_rows([row])
    assert not issues
    assert rows[0]["event_time"] == "2023-01-02"
    assert rows[0]["time_kind"] == "instant" and rows[0]["time_precision"] == "day"
    nodes, edges = build_icews_instances(rows)
    assert any(node["id"] == "ICEWS:Event:100" for node in nodes)
    assert {edge["type"] for edge in edges} == {"INITIATED", "TARGETED", "CLASSIFIED_AS", "ASSOCIATED_WITH", "OCCURRED_IN"}


def test_icews_invalid_rows_are_reported_not_guessed():
    rows, issues = normalize_icews_rows([{
        "Event ID": "1", "Event Date": "not-a-date", "Source Name": "A",
        "Target Name": "B", "CAMEO Code": "010",
    }])
    assert rows == []
    assert issues and "invalid Event Date" in issues[0]["error"]


def test_icews_parser_requires_official_schema():
    try:
        parse_icews_tsv(b"Event ID\tEvent Date\n1\t2023-01-01\n")
    except ValueError as exc:
        assert "missing columns" in str(exc)
    else:
        raise AssertionError("expected missing-column validation")


def test_icews_filters_are_applied_before_build_and_limit_rows():
    rows = [
        {"Event Date": "2023-01-01", "Source Country": "Ukraine", "Target Country": "Russia", "Event Text": "Make statement", "CAMEO Code": "010", "Intensity": "-2"},
        {"Event Date": "2023-01-02", "Source Country": "France", "Target Country": "Germany", "Event Text": "Consult", "CAMEO Code": "040", "Intensity": "1"},
        {"Event Date": "2023-01-03", "Source Country": "Ukraine", "Target Country": "Poland", "Event Text": "Protest", "CAMEO Code": "145", "Intensity": "-7"},
    ]
    selected = _filter_icews_rows(rows, {"scenario": "negative", "date_from": "2023-01-01", "date_to": "2023-01-03", "max_records": 1})
    assert len(selected) == 1 and selected[0]["Source Country"] == "Ukraine"
