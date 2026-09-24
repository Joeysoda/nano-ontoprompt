"""Bounded FalkorDB execution for compiled Object Set load queries."""
from __future__ import annotations

from typing import Any, Protocol

from app.schemas.v2.object_query import (
    LoadObjectSetResponse,
    LoadPageInfo,
    ObjectRecord,
    QueryExplanation,
    ReadOptions,
)
from app.services.v2.object_query.compiler import CompiledQuery
from app.services.v2.object_query.errors import ObjectQueryError


class FalkorGraph(Protocol):
    def query(self, query: str, params: dict[str, Any]) -> Any: ...


def _properties(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return dict(getattr(value, "properties", {}) or {})


def execute_load(graph: FalkorGraph, compiled: CompiledQuery, read: ReadOptions) -> LoadObjectSetResponse:
    result = graph.query(compiled.cypher, params=compiled.params)
    rows = list(getattr(result, "result_set", []) or [])
    has_more = len(rows) > read.page_size
    selected = {item.api_name for item in read.select}
    objects: list[ObjectRecord] = []
    for row in rows[:read.page_size]:
        node = row[0]
        props = _properties(node)
        object_id = str(props.get("_instance_id") or "")
        if not object_id:
            raise ObjectQueryError(
                "invalid_projection_record", "result._instance_id",
                "FalkorDB returned an object without the canonical _instance_id",
            )
        object_type = str(props.get("_type") or compiled.validation.result_type.api_name)
        if selected:
            visible = {key: props.get(key) for key in selected}
        else:
            visible = {key: value for key, value in props.items() if not key.startswith("_")}
        objects.append(ObjectRecord(object_id=object_id, object_type=object_type, properties=visible))
    explanation = None
    if read.explain:
        validation = compiled.validation
        explanation = QueryExplanation(
            expression_hash=validation.expression_hash,
            node_count=validation.node_count,
            traversal_depth=validation.traversal_depth,
            result_type=validation.result_type,
        )
    return LoadObjectSetResponse(
        objects=objects,
        page=LoadPageInfo(page_size=read.page_size, returned=len(objects), has_more=has_more),
        explanation=explanation,
    )
