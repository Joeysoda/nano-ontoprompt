"""Runtime contracts for stored heterogeneous logic.

Schemas are persisted as JSON Schema 2020-12.  The small legacy adapter keeps
existing seeded assets readable while new assets use full schemas directly.
"""
from __future__ import annotations

from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - dependency is declared in requirements
    Draft202012Validator = None

try:
    import pint
except ImportError:  # pragma: no cover - optional for local development
    pint = None

SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"


class ContractValidationError(ValueError):
    def __init__(self, stage: str, errors: list[dict[str, Any]]):
        self.stage = stage
        self.errors = errors
        super().__init__(f"{stage} contract validation failed")


def legacy_input_schema(schema: dict, *, asset_key: str = "") -> dict:
    if schema.get("$schema") or schema.get("type"):
        return schema
    required = list(schema.get("required", []))
    return {
        "$schema": SCHEMA_URI,
        "$id": f"urn:logic-input:{asset_key or 'legacy'}:1.0.0",
        "type": "object",
        "required": required,
        "properties": {name: {} for name in required},
        "additionalProperties": True,
    }


def legacy_output_schema(schema: dict, *, asset_key: str = "") -> dict:
    if schema.get("$schema") or schema.get("type"):
        return schema
    fields = list(schema.get("fields", []))
    return {
        "$schema": SCHEMA_URI,
        "$id": f"urn:logic-output:{asset_key or 'legacy'}:1.0.0",
        "type": "object",
        "required": fields,
        "properties": {name: {} for name in fields},
        "additionalProperties": True,
    }


def normalize_contract(asset: dict) -> dict:
    result = dict(asset)
    key = result.get("asset_key", "legacy")
    result["input_schema"] = legacy_input_schema(result.get("input_schema") or {}, asset_key=key)
    result["output_schema"] = legacy_output_schema(result.get("output_schema") or {}, asset_key=key)
    result["interface_key"] = result.get("interface_key") or f"logic.{key}"
    result["interface_version"] = result.get("interface_version") or "1.0.0"
    result["executor_type"] = result.get("executor_type") or "local"
    result["binding_spec"] = result.get("binding_spec") or {
        "kind": "object_fields",
        "fields": {field.split(".")[-1]: field for field in (result.get("bindings") or {}).get("ontology_fields", [])},
    }
    return result


def _errors(validator, payload: Any) -> list[dict[str, Any]]:
    return [
        {"path": list(error.absolute_path), "schema_path": list(error.absolute_schema_path), "message": error.message}
        for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    ]


def validate_payload(schema: dict, payload: Any, stage: str) -> None:
    if Draft202012Validator is None:
        raise RuntimeError("jsonschema is required for runtime contract validation")
    validator = Draft202012Validator(schema)
    errors = _errors(validator, payload)
    if errors:
        raise ContractValidationError(stage, errors)


def _get_path(data: dict, path: str) -> tuple[bool, Any]:
    if path in data:
        return True, data[path]
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def bind_object_fields(asset: dict, properties: dict) -> tuple[dict, list[dict]]:
    spec = (asset.get("binding_spec") or {}).get("fields") or {}
    if not spec:
        spec = {field.split(".")[-1]: field for field in (asset.get("bindings") or {}).get("ontology_fields", [])}
    inputs: dict[str, Any] = {}
    trace: list[dict] = []
    for target, source in spec.items():
        found, value = _get_path(properties, source)
        if found:
            inputs[target] = value
    trace.append({"stage": "bind_object", "fields": list(inputs), "sources": spec})
    return inputs, trace


_UNIT_REGISTRY = pint.UnitRegistry(autoconvert_offset_to_baseunit=True) if pint else None

def _ureg():
    return _UNIT_REGISTRY

_BASIC_UNIT_FACTORS = {
    ("s", "s"): 1.0, ("min", "s"): 60.0, ("h", "s"): 3600.0,
    ("s", "min"): 1.0 / 60.0, ("min", "min"): 1.0, ("h", "min"): 60.0,
    ("Cel", "Cel"): 1.0, ("degC", "Cel"): 1.0,
    ("bar", "bar"): 1.0, ("kPa", "bar"): 0.01, ("Pa", "bar"): 0.00001,
}


def normalize_units(schema: dict, payload: Any) -> tuple[Any, list[dict]]:
    """Normalize `{value, unit}` values to the schema's declared UCUM-like unit.

    Raw numeric values are treated as already canonical; the runtime never
    guesses a unit from a field name.
    """
    if not isinstance(payload, dict):
        return payload, []
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    result = dict(payload)
    trace: list[dict] = []
    registry = _ureg()
    for name, definition in properties.items():
        if name not in result or not isinstance(definition, dict) or not definition.get("x-unit-code"):
            continue
        value = result[name]
        if not isinstance(value, dict) or "value" not in value:
            continue
        source_unit = value.get("unit")
        target_unit = definition["x-unit-code"]
        if not source_unit or source_unit == target_unit:
            result[name] = value["value"]
            continue
        if registry is None:
            factor = _BASIC_UNIT_FACTORS.get((source_unit, target_unit))
            if factor is None:
                raise ValueError(f"{name} 无法转换单位 {source_unit} → {target_unit}（运行环境缺少 Pint）")
            result[name] = float(value["value"]) * factor
        else:
            try:
                converted = registry.Quantity(value["value"], source_unit).to(target_unit)
            except Exception as exc:
                raise ValueError(f"{name} 无法从 {source_unit} 转换为 {target_unit}: {exc}") from exc
            result[name] = converted.magnitude
        trace.append({"stage": "normalize_unit", "field": name, "from": source_unit, "to": target_unit})
    return result, trace
