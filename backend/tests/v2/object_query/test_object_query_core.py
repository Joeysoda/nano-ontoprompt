from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.v2.object_query import LoadObjectSetRequest, TypeRef, object_query_json_schema
from app.services.v2.object_query.capabilities import capability_payload, validate_terminal
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import (
    LinkMetadata,
    OntologyMetadata,
    PropertyMetadata,
    TypeMetadata,
)
from app.services.v2.object_query.normalize import canonical_data, dependency_footprint, expression_hash
from app.services.v2.object_query.validate import validate_object_set


def base(api_name: str = "Demand") -> dict:
    return {"kind": "base", "type_ref": {"kind": "object", "api_name": api_name}}


def prop(api_name: str) -> dict:
    return {"api_name": api_name}


def load(expression: dict) -> LoadObjectSetRequest:
    return LoadObjectSetRequest.model_validate({
        "expression": expression,
        "context": {"ontology_id": "ontology-1"},
    })


@pytest.fixture
def metadata() -> OntologyMetadata:
    return OntologyMetadata(
        [
            TypeMetadata("Demand", properties={
                "status": PropertyMetadata("status", "string"),
                "quantity": PropertyMetadata("quantity", "double"),
                "tags": PropertyMetadata("tags", "array", is_array=True),
                "embedding": PropertyMetadata("embedding", "vector", sortable=False, aggregatable=False),
            }),
            TypeMetadata("Item", properties={
                "name": PropertyMetadata("name", "string"),
                "cost": PropertyMetadata("cost", "double"),
            }),
            TypeMetadata("Supplier", properties={
                "name": PropertyMetadata("name", "string"),
            }),
            TypeMetadata("Region", properties={
                "name": PropertyMetadata("name", "string"),
            }),
            TypeMetadata("Country", properties={
                "name": PropertyMetadata("name", "string"),
            }),
        ],
        [
            LinkMetadata("demands_item", "Demand", "Item", "many-to-one"),
            LinkMetadata("supplied_by", "Item", "Supplier", "many-to-many"),
            LinkMetadata("located_in", "Supplier", "Region", "many-to-one"),
            LinkMetadata("part_of", "Region", "Country", "many-to-one"),
        ],
    )


def test_public_schema_contains_recursive_union_and_no_method_input_object_set() -> None:
    schema = object_query_json_schema()
    text = str(schema)
    for kind in (
        "base", "interface_base", "static", "reference", "filter", "traverse",
        "interface_traverse", "union", "intersect", "subtract",
        "nearest_neighbors", "with_properties", "as_type", "as_base_object_types",
    ):
        assert kind in text
    with pytest.raises(ValidationError):
        load({"kind": "method_input"})


def test_checked_in_json_schema_matches_model() -> None:
    artifact = Path(__file__).resolve().parents[3] / "app" / "schemas" / "v2" / "object_query.schema.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == object_query_json_schema()


def test_models_are_immutable_and_reject_extra_fields() -> None:
    request = load(base())
    with pytest.raises(ValidationError):
        request.read.page_size = 10
    with pytest.raises(ValidationError):
        load({**base(), "cypher": "MATCH (n) RETURN n"})


def test_structural_operator_and_revision_invariants() -> None:
    with pytest.raises(ValidationError, match="requires an array"):
        load({
            "kind": "filter", "input": base(),
            "where": {"kind": "comparison", "property": prop("status"), "op": "in", "value": "open"},
        })
    with pytest.raises(ValidationError, match="requires revision_id"):
        LoadObjectSetRequest.model_validate({
            "expression": base(),
            "context": {"ontology_id": "o", "revision_policy": "pinned"},
        })


def test_canonical_hash_is_stable_for_commutative_filters() -> None:
    left = {"kind": "comparison", "property": prop("status"), "op": "eq", "value": "open"}
    right = {"kind": "comparison", "property": prop("quantity"), "op": "gt", "value": 0}
    first = load({"kind": "filter", "input": base(), "where": {"kind": "and", "items": [left, right]}}).expression
    second = load({"kind": "filter", "input": base(), "where": {"kind": "and", "items": [right, left]}}).expression
    assert canonical_data(first) == canonical_data(second)
    assert expression_hash(first) == expression_hash(second)


def test_operation_order_changes_identity() -> None:
    where = {"kind": "comparison", "property": prop("name"), "op": "eq", "value": "A"}
    filter_after_traverse = load({
        "kind": "filter",
        "input": {"kind": "traverse", "input": base(), "link": {"api_name": "demands_item"}},
        "where": where,
    }).expression
    traverse_after_filter = load({
        "kind": "traverse",
        "input": {
            "kind": "filter", "input": base(),
            "where": {"kind": "comparison", "property": prop("status"), "op": "eq", "value": "open"},
        },
        "link": {"api_name": "demands_item"},
    }).expression
    assert expression_hash(filter_after_traverse) != expression_hash(traverse_after_filter)


def test_dependency_footprint_collects_domain_references() -> None:
    expression = load({
        "kind": "filter",
        "input": {"kind": "reference", "object_set_id": "set-1"},
        "where": {"kind": "text", "property": prop("status"), "mode": "contains", "query": "open"},
    }).expression
    footprint = dependency_footprint(expression)
    assert footprint.object_set_references == ("set-1",)
    assert footprint.properties == ("status",)
    assert set(footprint.node_kinds) >= {"filter", "reference", "text"}


def test_semantic_validation_infers_traversal_type(metadata: OntologyMetadata) -> None:
    expression = load({
        "kind": "filter",
        "input": {"kind": "traverse", "input": base(), "link": {"api_name": "demands_item"}},
        "where": {"kind": "comparison", "property": prop("cost"), "op": "gt", "value": 1},
    }).expression
    result = validate_object_set(expression, metadata)
    assert result.result_type == TypeRef(kind="object", api_name="Item")
    assert result.traversal_depth == 1
    assert result.node_count == 3


def test_unknown_property_and_operator_type_fail_before_execution(metadata: OntologyMetadata) -> None:
    unknown = load({
        "kind": "filter", "input": base(),
        "where": {"kind": "comparison", "property": prop("missing"), "op": "eq", "value": 1},
    }).expression
    with pytest.raises(ObjectQueryError) as error:
        validate_object_set(unknown, metadata)
    assert error.value.code == "unknown_property"
    assert error.value.path.endswith("where.property")

    wrong_type = load({
        "kind": "filter", "input": base(),
        "where": {"kind": "text", "property": prop("quantity"), "mode": "contains", "query": "1"},
    }).expression
    with pytest.raises(ObjectQueryError) as error:
        validate_object_set(wrong_type, metadata)
    assert error.value.code == "operator_type_mismatch"

    wrong_value = load({
        "kind": "filter", "input": base(),
        "where": {"kind": "comparison", "property": prop("quantity"), "op": "gt", "value": "ten"},
    }).expression
    with pytest.raises(ObjectQueryError) as error:
        validate_object_set(wrong_value, metadata)
    assert error.value.code == "value_type_mismatch"

    wrong_case = load({
        "kind": "filter", "input": base("demand"),
        "where": {"kind": "comparison", "property": prop("status"), "op": "eq", "value": "open"},
    }).expression
    with pytest.raises(ObjectQueryError) as error:
        validate_object_set(wrong_case, metadata)
    assert error.value.code == "unknown_type"


def test_set_operations_require_same_result_type(metadata: OntologyMetadata) -> None:
    expression = load({"kind": "union", "inputs": [base("Demand"), base("Item")]}).expression
    with pytest.raises(ObjectQueryError) as error:
        validate_object_set(expression, metadata)
    assert error.value.code == "set_type_mismatch"


def test_ambiguous_link_fails_instead_of_using_metadata_order() -> None:
    ambiguous = OntologyMetadata(
        [TypeMetadata("Demand"), TypeMetadata("Item"), TypeMetadata("Supplier")],
        [
            LinkMetadata("target", "Demand", "Item"),
            LinkMetadata("target", "Demand", "Supplier"),
        ],
    )
    expression = load({"kind": "traverse", "input": base(), "link": {"api_name": "target"}}).expression
    with pytest.raises(ObjectQueryError) as error:
        validate_object_set(expression, ambiguous)
    assert error.value.code == "ambiguous_link"


def test_fourth_traversal_is_within_eight_hop_budget(metadata: OntologyMetadata) -> None:
    expression = base()
    for link in ("demands_item", "supplied_by", "located_in", "part_of"):
        expression = {"kind": "traverse", "input": expression, "link": {"api_name": link}}
    parsed = load(expression).expression
    assert validate_object_set(parsed, metadata).traversal_depth == 4


def test_terminal_capability_is_distinct_from_structural_validity() -> None:
    nearest = load({
        "kind": "nearest_neighbors", "input": base(), "property": prop("embedding"),
        "query": {"kind": "text", "value": "urgent"}, "k": 10,
    }).expression
    with pytest.raises(ObjectQueryError) as error:
        validate_terminal(nearest, "load")
    assert error.value.code == "unsupported_terminal_expression"

    with pytest.raises(ObjectQueryError) as error:
        validate_terminal(load(base()).expression, "subscribe")
    assert error.value.code == "capability_unavailable"


def test_capability_payload_matches_current_executable_slice() -> None:
    payload = capability_payload()
    assert payload["terminals"]["load"]["enabled"] is True
    assert payload["terminals"]["aggregate"]["enabled"] is True
    assert "union" in payload["terminals"]["load"]["allowed_nodes"]
    assert payload["execution"] == {
        "consistency": ["live", "snapshot"],
        "revision_policy": ["latest"],
        "pagination": "single_page_live_or_snapshot_keyset",
        "exact_total": False,
        "bounded_python_fallback": True,
        "fallback_limits": {"objects": 10000, "edges": 50000},
    }


class DictResolver:
    def __init__(self, values: dict[str, object]):
        self.values = values

    def resolve(self, object_set_id: str):
        return self.values[object_set_id]


def test_reference_resolution_and_cycle_detection(metadata: OntologyMetadata) -> None:
    base_expr = load(base()).expression
    reference = load({"kind": "reference", "object_set_id": "saved"}).expression
    result = validate_object_set(reference, metadata, reference_resolver=DictResolver({"saved": base_expr}))
    assert result.result_type.api_name == "Demand"
    assert result.dependencies.object_set_references == ("saved",)

    first = load({"kind": "reference", "object_set_id": "second"}).expression
    second = load({"kind": "reference", "object_set_id": "first"}).expression
    with pytest.raises(ObjectQueryError) as error:
        validate_object_set(
            load({"kind": "reference", "object_set_id": "first"}).expression,
            metadata,
            reference_resolver=DictResolver({"first": first, "second": second}),
        )
    assert error.value.code == "reference_cycle"


def test_to_many_derived_selection_requires_aggregation(metadata: OntologyMetadata) -> None:
    with_properties = LoadObjectSetRequest.model_validate({
        "expression": {
            "kind": "with_properties",
            "input": {"kind": "traverse", "input": base(), "link": {"api_name": "demands_item"}},
            "definitions": {
                "supplier_name": {
                    "kind": "selection",
                    "input": {
                        "kind": "traverse", "input": {"kind": "method_input"},
                        "link": {"api_name": "supplied_by"},
                    },
                    "property": prop("name"),
                }
            },
        },
        "context": {"ontology_id": "ontology-1"},
    }).expression
    # Phase 1 semantic validation can inspect future nodes independently from
    # the terminal gate; enablement remains controlled by capabilities.py.
    from app.services.v2.object_query import capabilities
    original = capabilities.CAPABILITIES["load"]
    capabilities.CAPABILITIES["load"] = capabilities.TerminalCapability(True, original.allowed_nodes | {"with_properties"})
    try:
        with pytest.raises(ObjectQueryError) as error:
            validate_object_set(with_properties, metadata)
        assert error.value.code == "cardinality_violation"
    finally:
        capabilities.CAPABILITIES["load"] = original


def test_collect_requires_limit() -> None:
    with pytest.raises(ValidationError, match="explicit limit"):
        LoadObjectSetRequest.model_validate({
            "expression": {
                "kind": "with_properties", "input": base(),
                "definitions": {
                    "names": {
                        "kind": "aggregation", "input": {"kind": "method_input"},
                        "op": "collect_list", "property": prop("status"),
                    }
                },
            },
            "context": {"ontology_id": "ontology-1"},
        })
