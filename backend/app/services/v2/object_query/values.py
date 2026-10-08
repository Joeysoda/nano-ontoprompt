"""contract_v2 values: explicit types, no coercion of strings to numbers."""
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from pydantic import TypeAdapter
from app.schemas.v2.object_query import ObjectSetExpr, ParameterRef, ParameterSpec
from .errors import ObjectQueryError


def typed(value, kind):
    if value is None:
        return None
    if kind == 'decimal':
        if isinstance(value, dict) and set(value) == {'$decimal'}:
            value = value['$decimal']
        elif not isinstance(value, (Decimal, int)) or isinstance(value, bool):
            raise ValueError('Decimal requires a tagged decimal string or integer; float is not exact')
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError('Decimal must be finite')
        return result
    if kind in ('date', 'timestamp', 'datetime'):
        if isinstance(value, dict):
            value = value.get('$date' if kind == 'date' else '$timestamp')
        if kind == 'date':
            if isinstance(value, datetime):
                raise ValueError('Date cannot contain a time')
            return value if isinstance(value, date) else date.fromisoformat(value)
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError('Timestamp requires timezone')
        return result.astimezone(timezone.utc)
    if kind in ('integer', 'long') and type(value) is int:
        return value
    if kind == 'boolean' and type(value) is bool:
        return value
    if kind == 'string' and isinstance(value, str):
        return value
    if kind == 'double' and type(value) in (int, float) and math.isfinite(value):
        return value
    if kind == 'object' and isinstance(value, dict) and set(value) == {'object_type', 'object_id'} and all(isinstance(v, str) and v for v in value.values()):
        return deepcopy(value)
    if kind == 'array' and isinstance(value, list):
        return deepcopy(value)
    raise ValueError('Value does not match declared type')


def validate_parameter(value, spec):
    if value is None and not spec.nullable:
        raise ValueError('Parameter is not nullable')
    result = typed(value, spec.data_type)
    if result is None:
        return None
    if isinstance(result, (str, list)) and len(result) > spec.max_length:
        raise ValueError('Parameter exceeds length limit')
    if spec.data_type == 'array':
        if spec.element_type is None:
            raise ValueError('Array parameter requires element_type')
        result = [typed(v, spec.element_type) for v in result]
    for bound, op in ((spec.minimum, 'min'), (spec.maximum, 'max')):
        if bound is not None:
            boundary = typed(bound, spec.data_type)
            if (op == 'min' and result < boundary) or (op == 'max' and result > boundary):
                raise ValueError('Parameter outside declared range')
    if spec.enum is not None and result not in [typed(v, spec.data_type) for v in spec.enum]:
        raise ValueError('Parameter outside enum')
    return result


def bind_parameters(expression, schema, supplied):
    values = {}
    if set(supplied) - set(schema):
        raise ObjectQueryError('invalid_parameter', 'parameters', 'Undeclared parameter')
    for name, spec in schema.items():
        try:
            if name not in supplied and 'default' not in spec.model_fields_set and spec.required:
                raise ValueError('Required parameter missing')
            values[name] = validate_parameter(supplied.get(name, spec.default), spec)
        except (ValueError, TypeError, InvalidOperation) as exc:
            raise ObjectQueryError('invalid_parameter', f'parameters.{name}', str(exc)) from exc

    def walk(node, field=None):
        if isinstance(node, list):
            return [walk(v, field) for v in node]
        if not isinstance(node, dict):
            return deepcopy(node)
        if node.get('kind') == 'parameter':
            ref = ParameterRef.model_validate(node)
            spec = schema.get(ref.name)
            if spec is None or ref.data_type != spec.data_type or field not in spec.allowed_fields:
                raise ObjectQueryError('invalid_parameter', 'expression', 'Parameter type or allowed field mismatch')
            return deepcopy(values[ref.name])
        prop = node.get('property', {}).get('api_name', field)
        return {k: walk(v, prop) for k, v in node.items()}
    return TypeAdapter(ObjectSetExpr).validate_python(walk(expression.model_dump(mode='python'))), values
