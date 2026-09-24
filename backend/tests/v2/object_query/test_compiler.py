from __future__ import annotations

import pytest

from app.schemas.v2.object_query import LoadObjectSetRequest
from app.services.v2.object_query.compiler import FalkorObjectQueryCompiler, physical_relation_type
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import LinkMetadata, OntologyMetadata, PropertyMetadata, TypeMetadata
from app.services.v2.graph.falkordb_service import FalkorDBService


@pytest.fixture
def metadata() -> OntologyMetadata:
    return OntologyMetadata(
        [
            TypeMetadata("Demand", properties={
                "_instance_id": PropertyMetadata("_instance_id", "string", False),
                "status": PropertyMetadata("status", "string"),
                "quantity": PropertyMetadata("quantity", "double"),
                "embedding": PropertyMetadata("embedding", "vector", sortable=False),
            }),
            TypeMetadata("Item", properties={
                "_instance_id": PropertyMetadata("_instance_id", "string", False),
                "cost": PropertyMetadata("cost", "double"),
            }),
        ],
        [LinkMetadata("demands-item", "Demand", "Item", "many-to-one")],
    )


def request(expression: dict, **read: object) -> LoadObjectSetRequest:
    return LoadObjectSetRequest.model_validate({
        "expression": expression,
        "read": read,
        "context": {"ontology_id": "ontology-1", "consistency": "live"},
    })


def base() -> dict:
    return {"kind": "base", "type_ref": {"kind": "object", "api_name": "Demand"}}


def test_values_are_parameters_and_tie_breaker_is_always_present(metadata: OntologyMetadata) -> None:
    marker = "open') MATCH (secret) //"
    parsed = request({
        "kind": "filter",
        "input": base(),
        "where": {"kind": "comparison", "property": {"api_name": "status"}, "op": "eq", "value": marker},
    }, order_by=[{"property": {"api_name": "quantity"}, "direction": "desc"}], page_size=25)
    compiled = FalkorObjectQueryCompiler(metadata).compile_load(parsed.expression, parsed.read, parsed.context)

    assert marker not in compiled.cypher
    assert marker in compiled.params.values()
    assert "n0.`status` = $value_" in compiled.cypher
    assert "WITH DISTINCT n0 RETURN n0 AS object ORDER BY CASE WHEN n0.`quantity` IS NULL THEN 1 ELSE 0 END ASC, n0.`quantity` DESC, n0.`_instance_id` ASC" in compiled.cypher
    assert compiled.params["page_limit"] == 26


def test_traversal_uses_storage_relation_mapping_and_target_guards(metadata: OntologyMetadata) -> None:
    parsed = request({
        "kind": "filter",
        "input": {"kind": "traverse", "input": base(), "link": {"api_name": "demands-item"}},
        "where": {"kind": "comparison", "property": {"api_name": "cost"}, "op": "gt", "value": 10},
    })
    compiled = FalkorObjectQueryCompiler(metadata).compile_load(parsed.expression, parsed.read, parsed.context)

    assert "MATCH (n0)-[:DEMANDS_ITEM]->(n1:Instance)" in compiled.cypher
    assert "n1.`_ontology_id`" in compiled.cypher
    assert "n1.`_type`" in compiled.cypher
    assert "WITH n1 WHERE n1.`cost` > $value_" in compiled.cypher


def test_static_ids_are_bounded_parameters(metadata: OntologyMetadata) -> None:
    parsed = request({
        "kind": "static", "type_ref": {"kind": "object", "api_name": "Demand"},
        "object_ids": ["d-1", "d-2"],
    })
    compiled = FalkorObjectQueryCompiler(metadata).compile_load(parsed.expression, parsed.read, parsed.context)
    assert "d-1" not in compiled.cypher
    assert ["d-1", "d-2"] in compiled.params.values()


@pytest.mark.parametrize(
    ("read", "context", "code"),
    [
        ({"page_token": "opaque"}, {"consistency": "live"}, "pagination_unavailable"),
        ({"include_total": True}, {"consistency": "live"}, "total_unavailable"),
        ({}, {"consistency": "snapshot"}, "consistency_unavailable"),
        ({}, {"consistency": "live", "revision_policy": "pinned", "revision_id": "r1"}, "revision_unavailable"),
        ({}, {"consistency": "live", "revision_id": "r1"}, "revision_unavailable"),
    ],
)
def test_unimplemented_read_contracts_fail_explicitly(
    metadata: OntologyMetadata, read: dict, context: dict, code: str,
) -> None:
    parsed = LoadObjectSetRequest.model_validate({
        "expression": base(), "read": read, "context": {"ontology_id": "o", **context},
    })
    with pytest.raises(ObjectQueryError) as error:
        FalkorObjectQueryCompiler(metadata).compile_load(parsed.expression, parsed.read, parsed.context)
    assert error.value.code == code


def test_set_operation_remains_structurally_valid_but_is_not_advertised(metadata: OntologyMetadata) -> None:
    parsed = request({"kind": "union", "inputs": [base(), base()]})
    with pytest.raises(ObjectQueryError) as error:
        FalkorObjectQueryCompiler(metadata).compile_load(parsed.expression, parsed.read, parsed.context)
    assert error.value.code == "unsupported_terminal_expression"


def test_fuzzy_search_requires_an_indexed_implementation(metadata: OntologyMetadata) -> None:
    parsed = request({
        "kind": "filter", "input": base(),
        "where": {
            "kind": "text", "property": {"api_name": "status"},
            "mode": "contains", "query": "open", "fuzzy": True,
        },
    })
    with pytest.raises(ObjectQueryError) as error:
        FalkorObjectQueryCompiler(metadata).compile_load(parsed.expression, parsed.read, parsed.context)
    assert error.value.code == "capability_unavailable"


def test_relation_storage_mapping_matches_existing_writer() -> None:
    assert physical_relation_type("demands-item") == "DEMANDS_ITEM"
    assert physical_relation_type("123") == "R_123"
    assert physical_relation_type("demands-item") == FalkorDBService._safe_relation_type("demands-item")
