"""Behavioral contracts for dataset-independent, read-only Study adaptation."""
from copy import deepcopy

import pytest

from app.schemas.v2.scenario_study import StudySpec
from app.services.v2.row_mapping import MappingError, map_rows, parse_mapping
from app.services.v2.scenario_studies import StudyError, validate_spec


def binding(source, **kwargs):
    return {"fields": [{"target": "duration", "source": source, "data_type": "number", **kwargs}]}


def test_different_source_schemas_and_units_feed_the_same_function():
    from app.services.v2.logic_assets import _capacity_replan
    sources = [
        (binding(["properties", "minutes"], source_unit="min", target_unit="s"),
         [{"object_id": "plant-a:7", "object_type": "Operation", "properties": {"minutes": 2}}]),
        (binding(["properties", "runtime_seconds"]),
         [{"object_id": "plant-b:x", "object_type": "Job", "properties": {"runtime_seconds": 120}}]),
    ]
    results = []
    for config, rows in sources:
        before = deepcopy(rows)
        mapped = map_rows(config, rows, max_rows=10)
        assert rows == before
        assert mapped["rows"] == [{"duration": 120}]
        assert mapped["lineage"][0]["source"]["object_id"] == rows[0]["object_id"]
        results.append(_capacity_replan({"tasks": mapped["rows"], "capacity": 200}))
    assert results[0] == results[1]
    assert results[0]["makespan"] == 120


@pytest.mark.parametrize("value", [True, "12", None, float("nan"), float("inf")])
def test_invalid_values_are_rejected_without_coercion_or_leakage(value):
    with pytest.raises(MappingError) as error:
        map_rows(binding(["secret"]), [{"secret": value}], max_rows=1)
    assert error.value.code == "mapping_value_invalid"
    assert str(error.value) == "mapping_value_invalid at rows.0.duration"


def test_missing_and_null_have_distinct_explicit_semantics():
    with pytest.raises(MappingError, match="mapping_field_missing"):
        map_rows(binding(["missing"]), [{}], max_rows=1)
    assert map_rows(binding(["missing"], required=False), [{}], max_rows=1)["rows"] == [{}]
    assert map_rows(binding(["value"], nullable=True), [{"value": None}], max_rows=1)["rows"] == [{"duration": None}]


@pytest.mark.parametrize("config", [
    {"fields": []},
    {"fields": binding(["value"])["fields"] * 2},
    binding(["_hidden"]),
    binding(["value"], source_unit="s"),
    binding(["value"], data_type="string", source_unit="s", target_unit="min"),
    {**binding(["value"]), "expression": "arbitrary code"},
])
def test_invalid_configuration_is_rejected(config):
    with pytest.raises(MappingError, match="invalid_row_mapping"):
        parse_mapping(config)


def test_budget_empty_rows_and_incompatible_units():
    assert map_rows(binding(["value"]), [], max_rows=1) == {"rows": [], "lineage": []}
    with pytest.raises(MappingError, match="mapping_budget_exceeded"):
        map_rows(binding(["value"]), [{"value": 1}] * 2, max_rows=1)
    with pytest.raises(MappingError, match="invalid_mapping_units"):
        map_rows(binding(["value"], source_unit="s", target_unit="kg"), [{"value": 1}], max_rows=1)


def test_mapping_is_validated_in_the_study_and_executed_by_the_real_dispatcher():
    from app.tasks.v2.scenario import _execute_study_node
    node = {"key": "adapt", "kind": "row_mapping", "inputs": {"rows": []},
            "outputs": {"rows": "rows", "lineage": "lineage"}, "config": binding(["value"])}
    assert validate_spec(StudySpec(nodes=[node])) == ["adapt"]
    result, _ = _execute_study_node(None, None, {"budgets": {"max_objects": 10}}, node, {"rows": [{"value": 3}]}, None)
    assert result["rows"] == [{"duration": 3}]
    node["outputs"] = {"value": "number"}
    with pytest.raises(StudyError, match="rows/lineage"):
        validate_spec(StudySpec(nodes=[node]))


def test_mapping_cannot_reference_unselected_query_fields():
    nodes = [
        {"key": "read", "kind": "object_query", "outputs": {"objects": "object_set"},
         "config": {"type": "Job", "select": ["runtime"]}},
        {"key": "adapt", "kind": "row_mapping", "inputs": {"rows": {"from": "read.objects"}},
         "outputs": {"rows": "rows", "lineage": "lineage"}, "config": binding(["properties", "secret"])},
    ]
    spec = {"nodes": nodes, "edges": [{"source": "read.objects", "target": "adapt.rows"}]}
    with pytest.raises(StudyError) as error:
        validate_spec(StudySpec.model_validate(spec))
    assert error.value.code == "mapping_source_not_selected"
    nodes[1]["config"] = binding(["properties", "runtime"])
    assert validate_spec(StudySpec.model_validate(spec)) == ["read", "adapt"]
