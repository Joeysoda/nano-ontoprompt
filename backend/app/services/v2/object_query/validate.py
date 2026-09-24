"""Ontology-aware semantic validation for Object Set expressions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from app.schemas.v2.object_query import (
    DerivedObjectSetExpr,
    DerivedPropertyExpr,
    FilterExpr,
    ObjectSetExpr,
    TerminalKind,
    TypeRef,
)
from app.services.v2.object_query.capabilities import validate_terminal
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import (
    NUMERIC_TYPES,
    ORDERED_TYPES,
    OntologyMetadata,
    PropertyMetadata,
    TypeMetadata,
)
from app.services.v2.object_query.normalize import DependencyFootprint, dependency_footprint, expression_hash


class ObjectSetReferenceResolver(Protocol):
    def resolve(self, object_set_id: str) -> ObjectSetExpr: ...


@dataclass(frozen=True)
class ValidationResult:
    result_type: TypeRef
    expression_hash: str
    dependencies: DependencyFootprint
    node_count: int
    traversal_depth: int


@dataclass
class _NodeResult:
    type_meta: TypeMetadata
    derived_properties: dict[str, PropertyMetadata]
    traversal_depth: int


def _property(
    metadata: OntologyMetadata,
    node: _NodeResult,
    api_name: str,
    path: str,
) -> PropertyMetadata:
    derived = node.derived_properties.get(api_name)
    return derived or metadata.resolve_property(node.type_meta, api_name, path)


def _validate_filter(expression: FilterExpr, node: _NodeResult, metadata: OntologyMetadata, path: str) -> None:
    kind = expression.kind
    if kind in {"and", "or"}:
        for index, item in enumerate(expression.items):
            _validate_filter(item, node, metadata, f"{path}.items.{index}")
        return
    if kind == "not":
        _validate_filter(expression.item, node, metadata, f"{path}.item")
        return
    prop = _property(metadata, node, expression.property.api_name, f"{path}.property")
    if not prop.searchable:
        raise ObjectQueryError(
            "property_not_searchable", f"{path}.property",
            f"Property '{prop.api_name}' is not searchable", property_api_name=prop.api_name,
        )
    if prop.is_array and kind not in {"array_match", "null_test"}:
        raise ObjectQueryError("operator_type_mismatch", path, "Multivalued properties require array_match")
    if kind == "text" and prop.data_type != "string":
        raise ObjectQueryError("operator_type_mismatch", path, f"Text filter requires a string property", property_type=prop.data_type)
    if kind == "array_match" and not prop.is_array:
        raise ObjectQueryError("operator_type_mismatch", path, f"Array filter requires an array property", property_type=prop.data_type)
    if kind == "interval" and prop.data_type not in ORDERED_TYPES:
        raise ObjectQueryError("operator_type_mismatch", path, f"Interval filter requires an ordered property", property_type=prop.data_type)
    if kind == "comparison" and expression.op in {"gt", "gte", "lt", "lte"} and prop.data_type not in ORDERED_TYPES:
        raise ObjectQueryError("operator_type_mismatch", path, f"'{expression.op}' requires an ordered property", property_type=prop.data_type)
    values = None
    if kind == "comparison":
        values = expression.value if expression.op == "in" else [expression.value]
    elif kind == "interval":
        values = [value for value in (expression.lower, expression.upper) if value is not None]
    elif kind == "array_match":
        # Ontology metadata currently records only `array`, not its element
        # type, so element validation must wait for typed array metadata.
        return
    if kind == "comparison" and isinstance(expression.value, dict) and expression.value.get("kind") == "parameter":
        from app.schemas.v2.object_query import ParameterRef
        ref = ParameterRef.model_validate(expression.value)
        if ref.data_type != prop.data_type and not (ref.data_type == "integer" and prop.data_type in {"long", "double", "decimal"}) and not (expression.op == "in" and ref.data_type == "array"):
            raise ObjectQueryError("value_type_mismatch", path, "Parameter type does not match property")
        return
    if values is not None and not all(_value_matches_type(value, prop.data_type) for value in values):
        raise ObjectQueryError(
            "value_type_mismatch", path,
            f"Filter value does not match property type '{prop.data_type}'",
            property_type=prop.data_type,
        )


def _value_matches_type(value: object, data_type: str) -> bool:
    from .values import typed
    try:
        typed(value, data_type)
        return True
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        return False



def _derived_input(
    expression: DerivedObjectSetExpr,
    root: _NodeResult,
    metadata: OntologyMetadata,
    path: str,
) -> tuple[_NodeResult, bool]:
    if expression.kind == "method_input":
        return root, False
    child, constrained = _derived_input(expression.input, root, metadata, f"{path}.input")
    if expression.kind == "filter":
        _validate_filter(expression.where, child, metadata, f"{path}.where")
        return child, constrained
    if expression.kind == "traverse":
        link, target = metadata.resolve_link(
            child.type_meta.api_name, expression.link.api_name, expression.link.direction, f"{path}.link",
        )
        target_type = metadata.resolve_type("object", target, f"{path}.link")
        return _NodeResult(target_type, {}, child.traversal_depth + 1), constrained or link.is_many
    raise AssertionError(f"Unhandled derived input kind: {expression.kind}")


def _validate_derived(
    expression: DerivedPropertyExpr,
    root: _NodeResult,
    metadata: OntologyMetadata,
    path: str,
) -> PropertyMetadata:
    node, constrained = _derived_input(expression.input, root, metadata, f"{path}.input")
    if node.traversal_depth > 3:
        raise ObjectQueryError("query_too_complex", path, "Derived property exceeds three link traversals", limit=3)
    if expression.kind == "selection":
        if constrained:
            raise ObjectQueryError(
                "cardinality_violation", path,
                "A property reached through a to-many link must be aggregated or collected",
            )
        prop = _property(metadata, node, expression.property.api_name, f"{path}.property")
        return PropertyMetadata(expression.property.api_name, prop.data_type, True, True, prop.sortable, prop.aggregatable, prop.is_array)

    if expression.op == "count":
        return PropertyMetadata("count", "long", False, True, True, True)
    prop = _property(metadata, node, expression.property.api_name, f"{path}.property")  # type: ignore[union-attr]
    if expression.op in {"sum", "avg"} and prop.data_type not in NUMERIC_TYPES:
        raise ObjectQueryError("operator_type_mismatch", path, f"'{expression.op}' requires a numeric property", property_type=prop.data_type)
    if expression.op in {"min", "max"} and prop.data_type not in ORDERED_TYPES:
        raise ObjectQueryError("operator_type_mismatch", path, f"'{expression.op}' requires an ordered property", property_type=prop.data_type)
    if expression.op in {"collect_list", "collect_set"}:
        return PropertyMetadata(expression.property.api_name, "array", True, True, False, False, True)  # type: ignore[union-attr]
    result_type = "long" if expression.op in {"exact_distinct", "approximate_distinct"} else ("double" if expression.op == "avg" else prop.data_type)
    nullable = expression.op not in {"exact_distinct", "approximate_distinct"}
    return PropertyMetadata(expression.property.api_name, result_type, nullable, True, result_type in ORDERED_TYPES, True)  # type: ignore[union-attr]


def validate_object_set(
    expression: ObjectSetExpr,
    metadata: OntologyMetadata,
    terminal: TerminalKind = "load",
    reference_resolver: ObjectSetReferenceResolver | None = None,
    max_nodes: int = 500,
) -> ValidationResult:
    """Validate structure-independent ontology semantics before execution."""
    node_count = 0
    references: list[str] = []

    def visit(node: ObjectSetExpr, path: str, stack: tuple[str, ...] = ()) -> _NodeResult:
        nonlocal node_count
        node_count += 1
        if node_count > max_nodes:
            raise ObjectQueryError("query_too_complex", path, "Object Set exceeds node budget", limit=max_nodes)
        kind = node.kind
        if kind in {"base", "empty", "interface_base", "static"}:
            type_meta = metadata.resolve_type(node.type_ref.kind, node.type_ref.api_name, f"{path}.type_ref")
            return _NodeResult(type_meta, {}, 0)
        if kind == "reference":
            if reference_resolver is None:
                raise ObjectQueryError(
                    "invalid_object_set_reference", path,
                    f"Object Set reference '{node.object_set_id}' cannot be resolved",
                    object_set_id=node.object_set_id,
                )
            if node.object_set_id in stack:
                raise ObjectQueryError(
                    "reference_cycle", path, "Object Set reference cycle detected",
                    reference_chain=[*stack, node.object_set_id],
                )
            if len(stack) >= 16:
                raise ObjectQueryError("query_too_complex", path, "Object Set reference depth exceeds limit", limit=16)
            references.append(node.object_set_id)
            resolved = reference_resolver.resolve(node.object_set_id)
            validate_terminal(resolved, terminal)
            return visit(resolved, f"{path}.resolved", (*stack, node.object_set_id))
        if kind == "filter":
            child = visit(node.input, f"{path}.input", stack)
            _validate_filter(node.where, child, metadata, f"{path}.where")
            return child
        if kind in {"linked", "reachable"}:
            child = visit(node.input, f"{path}.input", stack)
            _, target = metadata.resolve_link(child.type_meta.api_name, node.link.api_name, node.link.direction, f"{path}.link")
            target_meta = metadata.resolve_type("object", target, f"{path}.link")
            if kind == "linked":
                _validate_filter(node.where, _NodeResult(target_meta, {}, child.traversal_depth + 1), metadata, f"{path}.where")
                return child
            if target != child.type_meta.api_name:
                raise ObjectQueryError("unsupported_type_scope", path, "Repeated reachability requires a homogeneous link")
            return _NodeResult(target_meta, {}, node.max_depth)
        if kind in {"traverse", "interface_traverse"}:
            child = visit(node.input, f"{path}.input", stack)
            link, target = metadata.resolve_link(child.type_meta.api_name, node.link.api_name, node.link.direction, f"{path}.link")
            depth = child.traversal_depth + 1
            if depth > 8:
                raise ObjectQueryError("query_too_complex", path, "Object Set exceeds eight link traversals", limit=8)
            return _NodeResult(metadata.resolve_type("object", target, f"{path}.link"), {}, depth)
        if kind in {"union", "intersect"}:
            children = [visit(item, f"{path}.inputs.{index}", stack) for index, item in enumerate(node.inputs)]
            first = children[0]
            if any((item.type_meta.kind, item.type_meta.api_name) != (first.type_meta.kind, first.type_meta.api_name) for item in children[1:]):
                raise ObjectQueryError("set_type_mismatch", path, "Set operations require the same result type")
            return _NodeResult(first.type_meta, {}, max(item.traversal_depth for item in children))
        if kind == "subtract":
            children = [visit(node.base, f"{path}.base", stack)] + [
                visit(item, f"{path}.subtract.{index}", stack) for index, item in enumerate(node.subtract)
            ]
            first = children[0]
            if any((item.type_meta.kind, item.type_meta.api_name) != (first.type_meta.kind, first.type_meta.api_name) for item in children[1:]):
                raise ObjectQueryError("set_type_mismatch", path, "Subtract requires the same result type")
            return _NodeResult(first.type_meta, {}, max(item.traversal_depth for item in children))
        if kind == "nearest_neighbors":
            child = visit(node.input, f"{path}.input", stack)
            prop = _property(metadata, child, node.property.api_name, f"{path}.property")
            if prop.data_type != "vector":
                raise ObjectQueryError("operator_type_mismatch", path, "Nearest-neighbor search requires a vector property", property_type=prop.data_type)
            return child
        if kind == "with_properties":
            child = visit(node.input, f"{path}.input", stack)
            derived = dict(child.derived_properties)
            for name, definition in node.definitions.items():
                if name in child.type_meta.properties or name in derived:
                    raise ObjectQueryError("duplicate_property", f"{path}.definitions.{name}", f"Derived property '{name}' collides with an existing property")
                derived[name] = _validate_derived(definition, child, metadata, f"{path}.definitions.{name}")
            return _NodeResult(child.type_meta, derived, child.traversal_depth)
        if kind == "as_type":
            visit(node.input, f"{path}.input", stack)
            target = metadata.resolve_type(node.type_ref.kind, node.type_ref.api_name, f"{path}.type_ref")
            return _NodeResult(target, {}, 0)
        if kind == "as_base_object_types":
            child = visit(node.input, f"{path}.input", stack)
            if child.type_meta.kind != "interface":
                raise ObjectQueryError("unsupported_type_scope", path, "as_base_object_types requires interface input")
            return child
        raise AssertionError(f"Unhandled Object Set kind: {kind}")

    result = visit(expression, "expression")
    # Capability gating follows semantic analysis so the same IR validator can
    # report ontology/type mistakes even for structurally supported features
    # whose execution path has not been enabled yet.
    validate_terminal(expression, terminal)
    footprint = dependency_footprint(expression)
    if references:
        footprint = DependencyFootprint(
            type_refs=footprint.type_refs,
            properties=footprint.properties,
            links=footprint.links,
            object_set_references=tuple(sorted(set((*footprint.object_set_references, *references)))),
            node_kinds=footprint.node_kinds,
        )
    return ValidationResult(
        result_type=TypeRef(kind=result.type_meta.kind, api_name=result.type_meta.api_name),
        expression_hash=expression_hash(expression),
        dependencies=footprint,
        node_count=node_count,
        traversal_depth=result.traversal_depth,
    )
