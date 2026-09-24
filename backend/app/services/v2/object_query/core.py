"""Shared bounded Query Core. Membership is evaluated before page selection.

The adapter reads a bounded live projection, never a snapshot. All callers use
this service; no API accepts executable SQL/Cypher. Limits fail closed.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from functools import cmp_to_key
from time import monotonic
from datetime import date, datetime, timedelta
from pydantic import TypeAdapter
from app.schemas.v2.object_query import ObjectSetExpr, LoadObjectSetResponse, LoadPageInfo, ObjectRecord, AggregateResult
from .compiler import FalkorObjectQueryCompiler, physical_relation_type
from .errors import ObjectQueryError
from .normalize import canonical_data, definition_hash, execution_hash, stable_hash
from .validate import validate_object_set
from .values import bind_parameters, typed
from .cursor import decode as decode_cursor, encode as encode_cursor


def metadata_digest(metadata):
    return stable_hash({'types': [asdict(v) for _, v in sorted(metadata._types.items())],
                        'links': sorted([asdict(v) for v in metadata.links], key=lambda v: (v['api_name'], v['source_type'], v['target_type']))})


@dataclass(frozen=True)
class QueryPolicy:
    principal: str
    revision: str = 'owner-admin-v1'
    # None means the ontology-authorized principal has unrestricted data access.
    object_ids: frozenset[tuple[str, str]] | None = None
    fields: dict[str, frozenset[str]] | None = None

    def field_allowed(self, type_name, name):
        if self.fields is not None and name not in self.fields.get(type_name, frozenset()):
            raise ObjectQueryError('forbidden', 'property', 'Field is not authorized')

    def digest(self):
        return stable_hash({'principal': self.principal, 'revision': self.revision,
            'objects': sorted(self.object_ids) if self.object_ids is not None else None,
            'fields': {k: sorted(v) for k, v in self.fields.items()} if self.fields is not None else None})


@dataclass
class GraphData:
    objects: dict[tuple[str, str], dict]
    edges: list[tuple[tuple[str, str], str, tuple[str, str]]]


def _bucket_value(value, unit):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                value = date.fromisoformat(value)
            except ValueError as exc:
                raise ObjectQueryError("invalid_time_bucket", "time_bucket.property", "Stored date value is invalid") from exc
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    if not isinstance(value, datetime):
        raise ObjectQueryError("invalid_time_bucket", "time_bucket.property", "Stored value is not date or timestamp")
    if unit == "hour":
        return value.replace(minute=0, second=0, microsecond=0).isoformat()
    if unit == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if unit == "week":
        start = value - timedelta(days=value.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


class FalkorReadAdapter:
    def __init__(self, graph, max_objects=10000, max_edges=50000, graph_ontology_id=None):
        self.graph = graph
        self.max_objects = max_objects
        self.max_edges = max_edges
        self.graph_ontology_id = graph_ontology_id

    def read(self, ontology_id):
        graph_ontology_id = self.graph_ontology_id or ontology_id
        try:
            result = self.graph.ro_query(
                'MATCH (n:Instance) WHERE n._ontology_id = $ontology RETURN n LIMIT $limit',
                params={'ontology': graph_ontology_id, 'limit': self.max_objects + 1}, timeout=10000)
            if len(result.result_set) > self.max_objects:
                raise ObjectQueryError('query_too_complex', 'execution', 'Object scan budget exceeded')
            objects = {}
            for row in result.result_set:
                props = dict(row[0].properties)
                if not props.get('_instance_id') or not props.get('_type'):
                    raise ObjectQueryError('invalid_projection_record', 'execution', 'Object identity missing')
                identity = (props['_type'], props['_instance_id'])
                if identity in objects:
                    raise ObjectQueryError('invalid_projection_record', 'execution', 'Duplicate object identity')
                objects[identity] = props
            result = self.graph.ro_query(
                'MATCH (a:Instance)-[r]->(b:Instance) WHERE a._ontology_id = $ontology AND b._ontology_id = $ontology '
                'RETURN a._type, a._instance_id, type(r), b._type, b._instance_id LIMIT $limit',
                params={'ontology': graph_ontology_id, 'limit': self.max_edges + 1}, timeout=10000)
            if len(result.result_set) > self.max_edges:
                raise ObjectQueryError('query_too_complex', 'execution', 'Edge scan budget exceeded')
            return GraphData(objects, [((a, b), r, (c, d)) for a, b, r, c, d in result.result_set])
        except ObjectQueryError:
            raise
        except Exception as exc:
            raise ObjectQueryError('graph_unavailable', 'execution', 'Graph read failed; retry with a fresh request') from exc


class QueryCore:
    def __init__(self, metadata, adapter, policy):
        self.metadata, self.adapter, self.policy = metadata, adapter, policy

    def load(self, request, resolver=None):
        # Copy before validation/hash/execution, including mutable nested values.
        request = request.model_copy(deep=True)
        expression = request.expression
        original_hash = definition_hash(expression, {k: v.model_dump(mode='python', exclude_unset=True) for k, v in request.parameter_schema.items()})
        manifest = []
        if resolver:
            expression, manifest = resolver.expand(expression)
        schema = dict(request.parameter_schema)
        if resolver:
            from app.schemas.v2.object_query import ParameterSpec
            for name, spec in resolver.parameter_schema.items():
                parsed = ParameterSpec.model_validate(spec)
                if name in schema and schema[name] != parsed:
                    raise ObjectQueryError('invalid_parameter', 'parameter_schema', 'Conflicting parameter declarations')
                schema[name] = parsed
        bound, parameters = bind_parameters(expression, schema, request.parameters)
        validation = validate_object_set(bound, self.metadata)
        if request.context.consistency == "live":
            FalkorObjectQueryCompiler._validate_context(request.context, request.read)
        elif request.context.consistency == "snapshot":
            if not request.context.data_view_id:
                raise ObjectQueryError("consistency_unavailable", "context.data_view_id", "Snapshot reads require a data view")
            if request.read.include_total:
                raise ObjectQueryError("total_unavailable", "read.include_total", "Exact totals use aggregate terminal")
        else:
            raise ObjectQueryError("consistency_unavailable", "context.consistency", "Consistency mode is unavailable")
        digest = metadata_digest(self.metadata)
        if request.context.metadata_digest and request.context.metadata_digest != digest:
            raise ObjectQueryError('metadata_mismatch', 'context.metadata_digest', 'Metadata has changed')
        context = {'ontology_id': request.context.ontology_id, 'consistency': request.context.consistency,
                   'data_view_id': request.context.data_view_id, 'metadata_digest': digest,
                   'permission_digest': self.policy.digest(), 'references': manifest,
                   'snapshot': request.context.consistency == 'snapshot',
                   'read_digest': stable_hash(request.read.model_dump(mode='python', exclude={'page_token'}))}
        current_execution_hash = execution_hash(original_hash, parameters=parameters, **context)
        cursor_payload = None
        if request.read.page_token:
            if request.context.consistency != 'snapshot':
                raise ObjectQueryError('pagination_unavailable', 'read.page_token', 'Continuation requires a snapshot data view')
            cursor_payload = decode_cursor(request.read.page_token)
            if cursor_payload.get('execution_hash') != current_execution_hash:
                raise ObjectQueryError('cursor_mismatch', 'read.page_token', 'Cursor does not match query context')
        compiler = FalkorObjectQueryCompiler(self.metadata)
        result_type = self.metadata.resolve_type(validation.result_type.kind, validation.result_type.api_name, 'expression')
        compiler._validate_read(request.read, result_type)
        self._authorize_fields(bound)
        for item in request.read.select:
            self.policy.field_allowed(result_type.api_name, item.api_name)
        for item in request.read.order_by:
            self.policy.field_allowed(result_type.api_name, item.property.api_name)
        data = self.adapter.read(request.context.ontology_id)
        visible = {k: v for k, v in data.objects.items() if self.policy.object_ids is None or k in self.policy.object_ids}
        evaluator = _Evaluator(self.metadata, GraphData(visible, data.edges))
        members = evaluator.evaluate(bound)
        rows = list(members)
        def compare(a, b):
            for order in request.read.order_by:
                name = order.property.api_name
                dtype = result_type.properties[name].data_type
                av, bv = typed(visible[a].get(name), dtype), typed(visible[b].get(name), dtype)
                if av is None or bv is None:
                    delta = (av is None) - (bv is None)
                    if order.nulls == 'first':
                        delta = -delta
                else:
                    delta = (av > bv) - (av < bv)
                    if order.direction == 'desc':
                        delta = -delta
                if delta:
                    return delta
            return (a > b) - (a < b)
        try:
            rows.sort(key=cmp_to_key(compare))
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise ObjectQueryError('invalid_projection_record', 'read.order_by', 'Stored value violates metadata') from exc
        if cursor_payload:
            last_key = tuple(cursor_payload.get("last_key", []))
            try:
                index = next(index for index, key in enumerate(rows) if list(key) == list(last_key))
            except StopIteration as exc:
                raise ObjectQueryError("cursor_mismatch", "read.page_token", "Cursor object is not present in this view") from exc
            rows = rows[index + 1:]
        has_more = len(rows) > request.read.page_size
        selected = {v.api_name for v in request.read.select} or {k for k in result_type.properties if not k.startswith('_')}
        if self.policy.fields is not None:
            selected &= self.policy.fields.get(result_type.api_name, frozenset())
        objects = [ObjectRecord(object_type=k[0], object_id=k[1], properties={p: visible[k].get(p) for p in sorted(selected)}) for k in rows[:request.read.page_size]]
        completeness = 'incomplete' if evaluator.incomplete else 'partial' if has_more else 'complete'
        next_page_token = None
        stability = "snapshot_keyset" if request.context.consistency == "snapshot" else "single_page_live"
        if has_more and request.context.consistency == "snapshot" and objects:
            next_page_token = encode_cursor({"execution_hash": current_execution_hash, "last_key": list(rows[request.read.page_size - 1])})
        return LoadObjectSetResponse(objects=objects,
            page=LoadPageInfo(page_size=request.read.page_size, returned=len(objects), has_more=has_more,
                              next_page_token=next_page_token, stability=stability),
            definition_hash=original_hash, execution_hash=current_execution_hash,
            execution_context=context, completeness=completeness,
            status=completeness if completeness != 'complete' else 'ok' if objects else 'empty')

    def materialize(self, request, resolver=None, max_pages=50):
        """Bounded complete iterator used by compare and SetHandle terminals."""
        if request.context.consistency != 'snapshot' or not request.context.data_view_id:
            raise ObjectQueryError('consistency_unavailable', 'context', 'Complete materialization requires a pinned snapshot')
        token = None
        records = []
        last = None
        for _ in range(max_pages):
            page_request = request.model_copy(update={'read': request.read.model_copy(update={'page_size': 200, 'page_token': token})})
            last = self.load(page_request, resolver)
            records.extend(last.objects)
            if not last.page.has_more:
                if last.completeness != 'complete':
                    raise ObjectQueryError('incomplete', 'execution', 'Materialization did not produce a complete set')
                return records, last
            token = last.page.next_page_token
        raise ObjectQueryError('query_too_complex', 'execution', 'Materialization page budget exceeded')

    def aggregate(self, request, resolver=None):
        if request.context.consistency != "snapshot" or not request.context.data_view_id:
            raise ObjectQueryError("consistency_unavailable", "context", "Exact aggregate requires a pinned snapshot data view")
        request = request.model_copy(deep=True)
        expression = request.expression
        original_hash = definition_hash(expression, {k: v.model_dump(mode='python', exclude_unset=True) for k, v in request.parameter_schema.items()})
        manifest = []
        if resolver:
            expression, manifest = resolver.expand(expression)
        bound, parameters = bind_parameters(expression, request.parameter_schema, request.parameters)
        validation = validate_object_set(bound, self.metadata, terminal="aggregate")
        digest = metadata_digest(self.metadata)
        if request.context.metadata_digest and request.context.metadata_digest != digest:
            raise ObjectQueryError("metadata_mismatch", "context.metadata_digest", "Metadata has changed")
        result_type = self.metadata.resolve_type(validation.result_type.kind, validation.result_type.api_name, "expression")
        self._authorize_fields(bound)
        for group in request.group_by:
            prop = self.metadata.resolve_property(result_type, group.api_name, "group_by")
            if not prop.aggregatable:
                raise ObjectQueryError("property_not_aggregatable", "group_by", "Property is not aggregatable")
            self.policy.field_allowed(result_type.api_name, group.api_name)
        if request.time_bucket:
            bucket_prop = self.metadata.resolve_property(result_type, request.time_bucket.property.api_name, "time_bucket.property")
            if bucket_prop.data_type not in {"date", "datetime", "timestamp"}:
                raise ObjectQueryError("invalid_time_bucket", "time_bucket.property", "Time buckets require date or timestamp")
            self.policy.field_allowed(result_type.api_name, bucket_prop.api_name)
        for spec in request.aggregations:
            if spec.property:
                prop = self.metadata.resolve_property(result_type, spec.property.api_name, "aggregations.property")
                if not prop.aggregatable:
                    raise ObjectQueryError("property_not_aggregatable", "aggregations.property", "Property is not aggregatable")
                self.policy.field_allowed(result_type.api_name, spec.property.api_name)
        context = {'ontology_id': request.context.ontology_id, 'consistency': request.context.consistency,
                   'data_view_id': request.context.data_view_id, 'metadata_digest': digest,
                   'permission_digest': self.policy.digest(), 'references': manifest,
                   'snapshot': request.context.consistency == 'snapshot'}
        current_execution_hash = execution_hash(original_hash, parameters=parameters, **context)
        data = self.adapter.read(request.context.ontology_id)
        visible = {k: v for k, v in data.objects.items() if self.policy.object_ids is None or k in self.policy.object_ids}
        evaluator = _Evaluator(self.metadata, GraphData(visible, data.edges))
        members = evaluator.evaluate(bound)
        if evaluator.incomplete:
            raise ObjectQueryError("incomplete", "execution", "Aggregate input is incomplete")
        groups = {}
        for key in members:
            group_values = [visible[key].get(item.api_name) for item in request.group_by]
            if request.time_bucket:
                value = visible[key].get(request.time_bucket.property.api_name)
                group_values.append(_bucket_value(value, request.time_bucket.unit))
            group_key = tuple(group_values)
            groups.setdefault(group_key, []).append(key)
        output = []
        for group_key, keys in sorted(groups.items(), key=lambda item: repr(item[0])):
            row = {item.api_name: value for item, value in zip(request.group_by, group_key)}
            if request.time_bucket:
                row[request.time_bucket.property.api_name + "__" + request.time_bucket.unit] = group_key[len(request.group_by)]
            for spec in request.aggregations:
                alias = spec.alias
                if spec.op == "count":
                    row[alias] = len(keys)
                    continue
                prop = self.metadata.resolve_property(result_type, spec.property.api_name, "aggregations.property")
                values = [typed(visible[key].get(prop.api_name), prop.data_type) for key in keys]
                values = [value for value in values if value is not None]
                if spec.op == "exact_distinct":
                    row[alias] = len({repr(value) for value in values})
                elif not values:
                    row[alias] = None
                elif spec.op == "sum":
                    from decimal import Decimal
                    row[alias] = sum((Decimal(str(value)) for value in values), Decimal(0)) if prop.data_type == "decimal" else sum(values)
                elif spec.op == "avg":
                    from decimal import Decimal
                    row[alias] = (sum((Decimal(str(value)) for value in values), Decimal(0)) / Decimal(len(values))) if prop.data_type == "decimal" else sum(values) / len(values)
                elif spec.op == "min":
                    row[alias] = min(values)
                elif spec.op == "max":
                    row[alias] = max(values)
                else:
                    raise ObjectQueryError("capability_unavailable", "aggregations.op", "Approximate aggregate is not enabled")
            output.append(row)
        context = {"ontology_id": request.context.ontology_id, "data_view_id": request.context.data_view_id,
                   "consistency": "snapshot", "metadata_digest": digest, "permission_digest": self.policy.digest(),
                   "references": manifest, "snapshot": True}
        return AggregateResult(groups=output, exact=True, completeness="complete", definition_hash=original_hash,
            execution_hash=execution_hash(original_hash, parameters=parameters, **context), execution_context=context)

    def _authorize_fields(self, expr):
        kind = expr.kind
        if kind in ('filter', 'linked'):
            inferred = validate_object_set(expr.input, self.metadata).result_type.api_name
            if kind == 'linked':
                _, inferred = self.metadata.resolve_link(inferred, expr.link.api_name, expr.link.direction, 'link')
            def check(node):
                if node.kind in ('and', 'or'):
                    for item in node.items:
                        check(item)
                elif node.kind == 'not':
                    check(node.item)
                else:
                    self.policy.field_allowed(inferred, node.property.api_name)
            check(expr.where)
        from .capabilities import _child_expressions
        for _, child in _child_expressions(expr):
            self._authorize_fields(child)


class _Evaluator:
    def __init__(self, metadata, data):
        self.metadata, self.data = metadata, data
        self.incomplete = False
        self.deadline = monotonic() + 10
        self.adjacency = {}
        for source, link, target in data.edges:
            if source in data.objects and target in data.objects:
                self.adjacency.setdefault((source, link, 'out'), set()).add(target)
                self.adjacency.setdefault((target, link, 'in'), set()).add(source)

    def neighbors(self, source, link):
        _, target_type = self.metadata.resolve_link(source[0], link.api_name, link.direction, 'link')
        return {k for k in self.adjacency.get((source, physical_relation_type(link.api_name), link.direction), ()) if k[0] == target_type}

    def evaluate(self, expr):
        if monotonic() > self.deadline:
            raise ObjectQueryError('query_timeout', 'execution', 'Execution time budget exceeded')
        kind = expr.kind
        if kind in ('base', 'static', 'empty'):
            return {k for k in self.data.objects if k[0] == expr.type_ref.api_name and kind != 'empty' and (kind != 'static' or k[1] in set(expr.object_ids))}
        if kind in ('union', 'intersect'):
            children = [self.evaluate(v) for v in expr.inputs]
            return set.union(*children) if kind == 'union' else set.intersection(*children)
        if kind == 'subtract':
            return self.evaluate(expr.base) - set.union(*(self.evaluate(v) for v in expr.subtract))
        source = self.evaluate(expr.input)
        if kind == 'filter':
            return {k for k in source if self.predicate(expr.where, k) is True}
        if kind == 'traverse':
            return set.union(set(), *(self.neighbors(k, expr.link) for k in source))
        if kind == 'linked':
            result = set()
            for k in source:
                neighbors = self.neighbors(k, expr.link)
                count = sum(self.predicate(expr.where, n) is True for n in neighbors)
                keep = {'any': count > 0, 'none': count == 0, 'all': count == len(neighbors),
                        'count': self.comparison(count, expr.count, expr.count_op)}[expr.quantifier]
                if keep:
                    result.add(k)
            return result
        if kind == 'reachable':
            visited, frontier = set(source), set(source)
            for _ in range(expr.max_depth):
                if monotonic() > self.deadline:
                    raise ObjectQueryError('query_timeout', 'execution', 'Reachability time budget exceeded')
                following = set.union(set(), *(self.neighbors(k, expr.link) for k in frontier)) - visited
                if len(visited | following) > expr.max_nodes:
                    self.incomplete = True
                    break
                visited |= following
                frontier = following
                if not frontier:
                    break
            if set.union(set(), *(self.neighbors(k, expr.link) for k in frontier)) - visited:
                self.incomplete = True
            return visited if expr.include_seed else visited - source
        raise ObjectQueryError('unsupported_terminal_expression', 'expression', 'Expression is not executable')

    @staticmethod
    def comparison(left, right, op):
        if op == 'in' and not right:
            return False
        if left is None or right is None:
            return None
        if op == 'in':
            return True if left in right else None if None in right else False
        if op == 'eq': return left == right
        if op == 'ne': return left != right
        if op == 'gt': return left > right
        if op == 'gte': return left >= right
        if op == 'lt': return left < right
        if op == 'lte': return left <= right
        raise AssertionError(op)

    def predicate(self, expr, key):
        kind = expr.kind
        if kind in ('and', 'or'):
            values = [self.predicate(v, key) for v in expr.items]
            if kind == 'and': return False if False in values else None if None in values else True
            return True if True in values else None if None in values else False
        if kind == 'not':
            value = self.predicate(expr.item, key)
            return None if value is None else not value
        raw = self.data.objects[key].get(expr.property.api_name)
        if kind == 'null_test':
            return (raw is None) == expr.is_null
        if kind == 'array_match':
            if raw is not None and not isinstance(raw, list):
                raise ObjectQueryError('invalid_projection_record', 'execution', 'Array property is not an array')
            return (any if expr.mode == 'contains_any' else all)(v in (raw or []) for v in expr.values)
        dtype = self.metadata.resolve_type('object', key[0], 'type').properties[expr.property.api_name].data_type
        try:
            value = typed(raw, dtype)
            if kind == 'comparison':
                operand = [typed(v, dtype) for v in expr.value] if expr.op == 'in' else typed(expr.value, dtype)
                return self.comparison(value, operand, expr.op)
            if kind == 'interval':
                if value is None: return None
                return (expr.lower is None or self.comparison(value, typed(expr.lower, dtype), 'gte' if expr.lower_inclusive else 'gt')) and (expr.upper is None or self.comparison(value, typed(expr.upper, dtype), 'lte' if expr.upper_inclusive else 'lt'))
            if kind == 'text':
                if expr.fuzzy:
                    raise ObjectQueryError('capability_unavailable', 'expression.where', 'Fuzzy search is not supported')
                value = (value or '').casefold()
                query = expr.query.casefold()
                if expr.mode == 'contains': return query in value
                if expr.mode == 'starts_with': return value.startswith(query)
                return (all if expr.mode == 'match_all_tokens' else any)(v in value for v in query.split())
        except (ValueError, TypeError, ArithmeticError) as exc:
            if isinstance(exc, ObjectQueryError): raise
            raise ObjectQueryError('invalid_projection_record', 'execution', 'Stored value violates metadata') from exc
        raise AssertionError(kind)
