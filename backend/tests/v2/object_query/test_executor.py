from __future__ import annotations

from dataclasses import dataclass

from app.schemas.v2.object_query import LoadObjectSetRequest
from app.services.v2.object_query.compiler import FalkorObjectQueryCompiler
from app.services.v2.object_query.executor import execute_load
from app.services.v2.object_query.metadata import OntologyMetadata, PropertyMetadata, TypeMetadata
from app.services.v2.object_query.errors import ObjectQueryError
import pytest


class Node:
    def __init__(self, **properties):
        self.properties = properties


@dataclass
class Result:
    result_set: list[list[Node]]


class Graph:
    def __init__(self, rows: list[list[Node]]):
        self.rows = rows
        self.calls = []

    def query(self, query, params):
        self.calls.append((query, params))
        return Result(self.rows)


def test_bounded_load_shapes_records_and_reports_truncation() -> None:
    metadata = OntologyMetadata([
        TypeMetadata("Demand", properties={
            "_instance_id": PropertyMetadata("_instance_id", "string", False),
            "status": PropertyMetadata("status", "string"),
            "quantity": PropertyMetadata("quantity", "double"),
        }),
    ], [])
    request = LoadObjectSetRequest.model_validate({
        "expression": {"kind": "base", "type_ref": {"kind": "object", "api_name": "Demand"}},
        "read": {"select": [{"api_name": "status"}], "page_size": 2, "explain": True},
        "context": {"ontology_id": "o", "consistency": "live"},
    })
    compiled = FalkorObjectQueryCompiler(metadata).compile_load(
        request.expression, request.read, request.context,
    )
    graph = Graph([
        [Node(_instance_id="d1", _type="Demand", status="open", quantity=1)],
        [Node(_instance_id="d2", _type="Demand", status="closed", quantity=2)],
        [Node(_instance_id="d3", _type="Demand", status="open", quantity=3)],
    ])

    response = execute_load(graph, compiled, request.read)

    assert [item.object_id for item in response.objects] == ["d1", "d2"]
    assert response.objects[0].properties == {"status": "open"}
    assert response.page.has_more is True
    assert response.page.next_page_token is None
    assert response.page.stability == "single_page_live"
    assert response.explanation is not None
    assert response.explanation.result_type.api_name == "Demand"
    assert graph.calls[0][1]["page_limit"] == 3


def test_default_projection_hides_internal_storage_properties() -> None:
    metadata = OntologyMetadata([
        TypeMetadata("Demand", properties={
            "_instance_id": PropertyMetadata("_instance_id", "string", False),
            "status": PropertyMetadata("status", "string"),
        }),
    ], [])
    request = LoadObjectSetRequest.model_validate({
        "expression": {"kind": "base", "type_ref": {"kind": "object", "api_name": "Demand"}},
        "context": {"ontology_id": "o", "consistency": "live"},
    })
    compiled = FalkorObjectQueryCompiler(metadata).compile_load(request.expression, request.read, request.context)
    response = execute_load(
        Graph([[Node(_instance_id="d1", _type="Demand", _ontology_id="o", status="open")]]),
        compiled,
        request.read,
    )
    assert response.objects[0].properties == {"status": "open"}


def test_projection_record_without_canonical_id_is_rejected() -> None:
    metadata = OntologyMetadata([TypeMetadata("Demand")], [])
    request = LoadObjectSetRequest.model_validate({
        "expression": {"kind": "base", "type_ref": {"kind": "object", "api_name": "Demand"}},
        "context": {"ontology_id": "o", "consistency": "live"},
    })
    compiled = FalkorObjectQueryCompiler(metadata).compile_load(request.expression, request.read, request.context)
    with pytest.raises(ObjectQueryError) as error:
        execute_load(Graph([[Node(_type="Demand")]]), compiled, request.read)
    assert error.value.code == "invalid_projection_record"
