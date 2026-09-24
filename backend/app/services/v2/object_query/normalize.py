"""Canonicalization, identity, and dependency extraction for Object Sets."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from dataclasses import dataclass
from typing import Any

from app.schemas.v2.object_query import ObjectSetExpr


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: _canonical(item) for key, item in sorted(value.items())}
        if result.get("kind") == "static":
            result["object_ids"] = sorted(set(result["object_ids"]))
            if not result["object_ids"]:
                return {"kind": "empty", "type_ref": result["type_ref"]}
        if result.get("kind") in {"and", "or"} and isinstance(result.get("items"), list):
            result["items"] = sorted(result["items"], key=canonical_json)
        if result.get("kind") in {"union", "intersect"} and isinstance(result.get("inputs"), list):
            result["inputs"] = sorted(result["inputs"], key=canonical_json)
        return result
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite decimal")
        return {"$decimal": format(value.normalize(), "f")}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Timestamp requires timezone")
        return {"$timestamp": value.astimezone(timezone.utc).isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    return value


def canonical_data(expression: ObjectSetExpr) -> dict[str, Any]:
    return _canonical(deepcopy(expression.model_dump(mode="python", exclude_none=False)))


def canonical_json(value: Any) -> str:
    canonical = _canonical(deepcopy(value))
    return json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def definition_hash(expression: ObjectSetExpr, parameter_schema: dict | None = None) -> str:
    return stable_hash({"contract_version": "contract_v2", "expression": canonical_data(expression),
                        "parameter_schema": parameter_schema or {}})


def execution_hash(definition: str, **context: Any) -> str:
    return stable_hash({"definition_hash": definition, **context})


def expression_hash(expression: ObjectSetExpr) -> str:
    return hashlib.sha256(canonical_json(canonical_data(expression)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DependencyFootprint:
    type_refs: tuple[str, ...]
    properties: tuple[str, ...]
    links: tuple[str, ...]
    object_set_references: tuple[str, ...]
    node_kinds: tuple[str, ...]

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "type_refs": list(self.type_refs),
            "properties": list(self.properties),
            "links": list(self.links),
            "object_set_references": list(self.object_set_references),
            "node_kinds": list(self.node_kinds),
        }


def dependency_footprint(expression: ObjectSetExpr) -> DependencyFootprint:
    types: set[str] = set()
    properties: set[str] = set()
    links: set[str] = set()
    references: set[str] = set()
    kinds: set[str] = set()

    def walk(value: Any, parent_key: str | None = None) -> None:
        if isinstance(value, dict):
            kind = value.get("kind")
            if isinstance(kind, str):
                kinds.add(kind)
            type_ref = value.get("type_ref")
            if isinstance(type_ref, dict) and isinstance(type_ref.get("api_name"), str):
                types.add(f"{type_ref.get('kind', 'object')}:{type_ref['api_name']}")
            prop = value.get("property")
            if isinstance(prop, dict) and isinstance(prop.get("api_name"), str):
                properties.add(prop["api_name"])
            link = value.get("link")
            if isinstance(link, dict) and isinstance(link.get("api_name"), str):
                links.add(link["api_name"])
            if isinstance(value.get("object_set_id"), str):
                references.add(value["object_set_id"])
            for key, item in value.items():
                walk(item, key)
        elif isinstance(value, list):
            for item in value:
                walk(item, parent_key)

    walk(expression.model_dump(mode="json", exclude_none=True))
    return DependencyFootprint(
        type_refs=tuple(sorted(types)),
        properties=tuple(sorted(properties)),
        links=tuple(sorted(links)),
        object_set_references=tuple(sorted(references)),
        node_kinds=tuple(sorted(kinds)),
    )
