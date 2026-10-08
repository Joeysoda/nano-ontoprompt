"""Validated Object Set IR to parameterized FalkorDB Cypher.

This compiler deliberately supports the linear Phase 2 subset only.  It never
accepts raw Cypher fragments: identifiers come from ontology metadata and all
runtime values are query parameters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.schemas.v2.object_query import ExecutionContext, FilterExpr, ObjectSetExpr, ReadOptions
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import OntologyMetadata, TypeMetadata
from app.services.v2.object_query.validate import ObjectSetReferenceResolver, ValidationResult, validate_object_set


def physical_relation_type(value: str) -> str:
    """Match the storage mapping used by FalkorDBService.upsert_relations."""
    relation = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "RELATED")).upper()
    return relation if relation and relation[0].isalpha() else f"R_{relation}"


def _identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


@dataclass(frozen=True)
class CompiledQuery:
    cypher: str
    params: dict[str, Any]
    result_alias: str
    validation: ValidationResult


class _Parameters:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def add(self, value: Any, prefix: str = "value") -> str:
        name = f"{prefix}_{len(self.values)}"
        self.values[name] = value
        return f"${name}"


class FalkorObjectQueryCompiler:
    def __init__(
        self,
        metadata: OntologyMetadata,
        reference_resolver: ObjectSetReferenceResolver | None = None,
    ) -> None:
        self.metadata = metadata
        self.reference_resolver = reference_resolver

    def compile_load(
        self,
        expression: ObjectSetExpr,
        read: ReadOptions,
        context: ExecutionContext,
    ) -> CompiledQuery:
        self._validate_context(context, read)
        validation = validate_object_set(
            expression, self.metadata, terminal="load", reference_resolver=self.reference_resolver,
        )
        result_type = self.metadata.resolve_type(
            validation.result_type.kind, validation.result_type.api_name, "expression",
        )
        self._validate_read(read, result_type)
        params = _Parameters()
        clauses: list[str] = []
        alias, _ = self._compile_expression(expression, context, params, clauses, 0, ())

        order_parts = []
        for item in read.order_by:
            prop = f"{alias}.{_identifier(item.property.api_name)}"
            null_rank = 0 if item.nulls == "first" else 1
            order_parts.append(f"CASE WHEN {prop} IS NULL THEN {null_rank} ELSE {1 - null_rank} END ASC")
            order_parts.append(f"{prop} {item.direction.upper()}")
        if not any(item.property.api_name.casefold() == "_instance_id" for item in read.order_by):
            order_parts.append(f"{alias}.`_instance_id` ASC")
        params.values["page_limit"] = read.page_size + 1
        cypher = " ".join([
            *clauses,
            f"WITH DISTINCT {alias}",
            f"RETURN {alias} AS object",
            f"ORDER BY {', '.join(order_parts)}",
            "LIMIT $page_limit",
        ])
        return CompiledQuery(cypher, params.values, "object", validation)

    @staticmethod
    def _validate_context(context: ExecutionContext, read: ReadOptions) -> None:
        if context.consistency != "live":
            raise ObjectQueryError(
                "consistency_unavailable", "context.consistency",
                "Only live consistency is executable until projection manifests and snapshots are implemented",
                requested=context.consistency,
            )
        if context.revision_policy != "latest" or context.revision_id is not None:
            raise ObjectQueryError(
                "revision_unavailable", "context.revision_policy",
                "Explicit revisions are not executable until revision snapshots are implemented",
            )
        if read.page_token is not None:
            raise ObjectQueryError(
                "pagination_unavailable", "read.page_token",
                "Continuation tokens are not executable until signed keyset cursors are implemented",
            )
        if read.include_total:
            raise ObjectQueryError(
                "total_unavailable", "read.include_total",
                "Exact totals are not executable in the initial bounded load path",
            )

    def _validate_read(self, read: ReadOptions, result_type: TypeMetadata) -> None:
        for index, selected in enumerate(read.select):
            self.metadata.resolve_property(result_type, selected.api_name, f"read.select.{index}")
        for index, item in enumerate(read.order_by):
            prop = self.metadata.resolve_property(result_type, item.property.api_name, f"read.order_by.{index}.property")
            if not prop.sortable:
                raise ObjectQueryError(
                    "property_not_sortable", f"read.order_by.{index}.property",
                    f"Property '{prop.api_name}' is not sortable", property_api_name=prop.api_name,
                )

    def _compile_expression(
        self,
        expression: ObjectSetExpr,
        context: ExecutionContext,
        params: _Parameters,
        clauses: list[str],
        next_alias: int,
        reference_stack: tuple[str, ...],
    ) -> tuple[str, int]:
        kind = expression.kind
        if kind in {"base", "static"}:
            alias = f"n{next_alias}"
            ontology = params.add(context.ontology_id, "ontology")
            object_type = params.add(expression.type_ref.api_name, "type")
            predicates = [f"{alias}.`_ontology_id` = {ontology}", f"{alias}.`_type` = {object_type}"]
            if kind == "static":
                ids = params.add(expression.object_ids, "object_ids")
                predicates.append(f"{alias}.`_instance_id` IN {ids}")
            clauses.append(f"MATCH ({alias}:Instance) WHERE {' AND '.join(predicates)}")
            return alias, next_alias + 1
        if kind == "reference":
            if self.reference_resolver is None:
                raise ObjectQueryError(
                    "invalid_object_set_reference", "expression",
                    f"Object Set reference '{expression.object_set_id}' cannot be resolved",
                    object_set_id=expression.object_set_id,
                )
            if expression.object_set_id in reference_stack:
                raise ObjectQueryError("reference_cycle", "expression", "Object Set reference cycle detected")
            resolved = self.reference_resolver.resolve(expression.object_set_id)
            return self._compile_expression(
                resolved, context, params, clauses, next_alias, (*reference_stack, expression.object_set_id),
            )
        if kind == "filter":
            alias, next_alias = self._compile_expression(
                expression.input, context, params, clauses, next_alias, reference_stack,
            )
            predicate = self._compile_filter(expression.where, alias, params)
            clauses.append(f"WITH {alias} WHERE {predicate}")
            return alias, next_alias
        if kind == "traverse":
            source, next_alias = self._compile_expression(
                expression.input, context, params, clauses, next_alias, reference_stack,
            )
            source_type = self._infer_compiled_type(expression.input, reference_stack)
            link, target_type = self.metadata.resolve_link(
                source_type.api_name, expression.link.api_name, expression.link.direction, "expression.link",
            )
            target = f"n{next_alias}"
            relationship = physical_relation_type(link.api_name)
            target_type_param = params.add(target_type, "type")
            ontology = params.add(context.ontology_id, "ontology")
            if expression.link.direction == "out":
                pattern = f"({source})-[:{relationship}]->({target}:Instance)"
            else:
                pattern = f"({source})<-[:{relationship}]-({target}:Instance)"
            clauses.append(
                f"MATCH {pattern} WHERE {target}.`_ontology_id` = {ontology} "
                f"AND {target}.`_type` = {target_type_param}"
            )
            return target, next_alias + 1
        raise ObjectQueryError(
            "unsupported_terminal_expression", "expression",
            f"Load compiler does not support '{kind}'", terminal="load", node_kind=kind,
        )

    def _infer_compiled_type(self, expression: ObjectSetExpr, stack: tuple[str, ...]) -> TypeMetadata:
        result = validate_object_set(
            expression, self.metadata, terminal="load", reference_resolver=self.reference_resolver,
        )
        return self.metadata.resolve_type(result.result_type.kind, result.result_type.api_name, "expression")

    def _compile_filter(self, expression: FilterExpr, alias: str, params: _Parameters) -> str:
        kind = expression.kind
        if kind in {"and", "or"}:
            joiner = " AND " if kind == "and" else " OR "
            return "(" + joiner.join(self._compile_filter(item, alias, params) for item in expression.items) + ")"
        if kind == "not":
            return f"NOT ({self._compile_filter(expression.item, alias, params)})"
        prop = f"{alias}.{_identifier(expression.property.api_name)}"
        if kind == "null_test":
            return f"{prop} IS {'NULL' if expression.is_null else 'NOT NULL'}"
        if kind == "comparison":
            value = params.add(expression.value)
            operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "in": "IN"}
            return f"{prop} {operators[expression.op]} {value}"
        if kind == "interval":
            bounds: list[str] = []
            if expression.lower is not None:
                op = ">=" if expression.lower_inclusive else ">"
                bounds.append(f"{prop} {op} {params.add(expression.lower, 'lower')}")
            if expression.upper is not None:
                op = "<=" if expression.upper_inclusive else "<"
                bounds.append(f"{prop} {op} {params.add(expression.upper, 'upper')}")
            return "(" + " AND ".join(bounds) + ")"
        if kind == "array_match":
            values = params.add(expression.values, "array")
            quantifier = "any" if expression.mode == "contains_any" else "all"
            return f"{quantifier}(item IN {values} WHERE item IN coalesce({prop}, []))"
        if kind == "text":
            if expression.fuzzy:
                raise ObjectQueryError(
                    "capability_unavailable", "expression.where.fuzzy",
                    "Fuzzy text search requires a configured full-text index",
                )
            if expression.mode in {"contains", "starts_with"}:
                value = params.add(expression.query.casefold(), "text")
                operator = "CONTAINS" if expression.mode == "contains" else "STARTS WITH"
                return f"toLower(coalesce(toString({prop}), '')) {operator} {value}"
            tokens = [token for token in expression.query.casefold().split() if token]
            value = params.add(tokens, "tokens")
            quantifier = "all" if expression.mode == "match_all_tokens" else "any"
            return f"{quantifier}(token IN {value} WHERE toLower(coalesce(toString({prop}), '')) CONTAINS token)"
        raise AssertionError(f"Unhandled filter kind: {kind}")
