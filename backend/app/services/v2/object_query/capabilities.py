"""Versioned terminal capability policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, get_args

from app.schemas.v2.object_query import ObjectSetExpr, TerminalKind
from app.services.v2.object_query.errors import ObjectQueryError


LINEAR_NODES = frozenset({"base", "static", "reference", "filter", "traverse"})
SET_NODES = frozenset({"union", "intersect", "subtract"})
CAPABILITY_VERSION = "2026-09-18"


@dataclass(frozen=True)
class TerminalCapability:
    enabled: bool
    allowed_nodes: frozenset[str]
    supports_interfaces: bool = False
    allows_partial: bool = False


CAPABILITIES: dict[str, TerminalCapability] = {
    "load": TerminalCapability(True, LINEAR_NODES | SET_NODES | {"empty", "linked", "reachable"}),
    "aggregate": TerminalCapability(True, LINEAR_NODES | SET_NODES | {"empty", "linked", "reachable"}),
    "derive": TerminalCapability(False, LINEAR_NODES | SET_NODES | {"with_properties"}),
    "create_temporary": TerminalCapability(False, LINEAR_NODES | SET_NODES),
    "load_links": TerminalCapability(False, LINEAR_NODES | SET_NODES, allows_partial=True),
    "subscribe": TerminalCapability(False, frozenset({"base", "static", "reference", "filter"})),
}


def capability_payload() -> dict:
    return {
        "version": CAPABILITY_VERSION,
        "terminals": {
            name: {
                "enabled": value.enabled,
                "allowed_nodes": sorted(value.allowed_nodes),
                "supports_interfaces": value.supports_interfaces,
                "allows_partial": value.allows_partial,
            }
            for name, value in sorted(CAPABILITIES.items())
        },
        "execution": {
            "consistency": ["live", "snapshot"],
            "revision_policy": ["latest"],
            "pagination": "single_page_live_or_snapshot_keyset",
            "exact_total": False,
            "bounded_python_fallback": True,
            "fallback_limits": {"objects": 10000, "edges": 50000},
        },
    }


def _child_expressions(expression: ObjectSetExpr) -> Iterable[tuple[str, ObjectSetExpr]]:
    kind = expression.kind
    if kind in {"filter", "traverse", "linked", "reachable", "interface_traverse", "nearest_neighbors", "with_properties", "as_type", "as_base_object_types"}:
        yield "input", expression.input
    elif kind in {"union", "intersect"}:
        for index, child in enumerate(expression.inputs):
            yield f"inputs.{index}", child
    elif kind == "subtract":
        yield "base", expression.base
        for index, child in enumerate(expression.subtract):
            yield f"subtract.{index}", child


def validate_terminal(expression: ObjectSetExpr, terminal: TerminalKind) -> None:
    if terminal not in get_args(TerminalKind):
        raise ObjectQueryError("unknown_terminal", "terminal", f"Unknown terminal: {terminal}")
    capability = CAPABILITIES[terminal]
    if not capability.enabled:
        raise ObjectQueryError(
            "capability_unavailable", "terminal", f"Terminal '{terminal}' is not enabled",
            terminal=terminal,
        )

    def walk(node: ObjectSetExpr, path: str) -> None:
        if node.kind not in capability.allowed_nodes:
            raise ObjectQueryError(
                "unsupported_terminal_expression", path,
                f"Terminal '{terminal}' does not support '{node.kind}'",
                terminal=terminal, node_kind=node.kind,
            )
        type_ref = getattr(node, "type_ref", None)
        if type_ref is not None and type_ref.kind == "interface" and not capability.supports_interfaces:
            raise ObjectQueryError(
                "unsupported_type_scope", f"{path}.type_ref",
                f"Terminal '{terminal}' does not support interface type scope",
                terminal=terminal, type_api_name=type_ref.api_name,
            )
        for label, child in _child_expressions(node):
            walk(child, f"{path}.{label}")

    walk(expression, "expression")
