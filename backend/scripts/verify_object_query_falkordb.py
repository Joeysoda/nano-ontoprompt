"""Run a disposable real-FalkorDB smoke test for Object Query Core."""
from __future__ import annotations

import uuid

from app.schemas.v2.object_query import LoadObjectSetRequest
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.compiler import FalkorObjectQueryCompiler
from app.services.v2.object_query.executor import execute_load
from app.services.v2.object_query.metadata import LinkMetadata, OntologyMetadata, PropertyMetadata, TypeMetadata


def _request(ontology_id: str, expression: dict, **read) -> LoadObjectSetRequest:
    return LoadObjectSetRequest.model_validate({
        "expression": expression,
        "read": read,
        "context": {"ontology_id": ontology_id, "consistency": "live"},
    })


def main() -> None:
    ontology_id = f"object-query-smoke-{uuid.uuid4().hex}"
    service = FalkorDBService()
    if not service.available:
        raise RuntimeError(f"FalkorDB is unavailable at {service.host}:{service.port}")
    metadata = OntologyMetadata(
        [
            TypeMetadata("Demand", properties={
                "_instance_id": PropertyMetadata("_instance_id", "string", False),
                "status": PropertyMetadata("status", "string"),
                "quantity": PropertyMetadata("quantity", "double"),
                "tags": PropertyMetadata("tags", "array", is_array=True),
            }),
            TypeMetadata("Item", properties={
                "_instance_id": PropertyMetadata("_instance_id", "string", False),
                "cost": PropertyMetadata("cost", "double"),
            }),
        ],
        [LinkMetadata("demands_item", "Demand", "Item", "many-to-one")],
    )
    compiler = FalkorObjectQueryCompiler(metadata)
    try:
        service.upsert_instances(ontology_id, [
            {"id": "d1", "entity_type": "Demand", "properties": {"status": "open", "quantity": 4, "tags": ["urgent", "north"]}},
            {"id": "d2", "entity_type": "Demand", "properties": {"status": "open", "quantity": 9, "tags": ["south"]}},
            {"id": "d3", "entity_type": "Demand", "properties": {"status": "closed", "quantity": 12, "tags": ["urgent"]}},
            {"id": "i1", "entity_type": "Item", "properties": {"cost": 10}},
            {"id": "i2", "entity_type": "Item", "properties": {"cost": 30}},
        ])
        service.upsert_relations(ontology_id, [
            {"source": "d1", "target": "i1", "type": "demands_item"},
            {"source": "d2", "target": "i2", "type": "demands_item"},
        ])
        base = {"kind": "base", "type_ref": {"kind": "object", "api_name": "Demand"}}
        filtered = _request(ontology_id, {
            "kind": "filter", "input": base,
            "where": {"kind": "comparison", "property": {"api_name": "status"}, "op": "eq", "value": "open"},
        }, page_size=1, order_by=[{"property": {"api_name": "quantity"}, "direction": "desc"}])
        first = execute_load(
            service._graph(ontology_id),
            compiler.compile_load(filtered.expression, filtered.read, filtered.context),
            filtered.read,
        )
        assert [item.object_id for item in first.objects] == ["d2"]
        assert first.page.has_more is True

        traversed = _request(ontology_id, {
            "kind": "filter",
            "input": {"kind": "traverse", "input": base, "link": {"api_name": "demands_item"}},
            "where": {"kind": "comparison", "property": {"api_name": "cost"}, "op": "gt", "value": 20},
        })
        linked = execute_load(
            service._graph(ontology_id),
            compiler.compile_load(traversed.expression, traversed.read, traversed.context),
            traversed.read,
        )
        assert [item.object_id for item in linked.objects] == ["i2"]

        array_filter = _request(ontology_id, {
            "kind": "filter", "input": base,
            "where": {"kind": "array_match", "property": {"api_name": "tags"}, "mode": "contains_any", "values": ["urgent"]},
        })
        tagged = execute_load(
            service._graph(ontology_id),
            compiler.compile_load(array_filter.expression, array_filter.read, array_filter.context),
            array_filter.read,
        )
        assert [item.object_id for item in tagged.objects] == ["d1", "d3"]
        print("object-query FalkorDB smoke test passed")
    finally:
        service.delete_graph(ontology_id)


if __name__ == "__main__":
    main()
