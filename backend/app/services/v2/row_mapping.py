"""Explicit, read-only adaptation of authorized rows to domain Function inputs.

No expressions, inferred names, defaults, or database access. Object identity is
returned separately as lineage and must never be used as an automatic edit map.
"""
from copy import deepcopy
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.v2.logic_contracts import normalize_units, validate_payload, ContractValidationError


class MappingError(ValueError):
    def __init__(self, code: str, path: str):
        self.code, self.path = code, path
        super().__init__(f"{code} at {path}")


class FieldBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=120)
    source: list[str] = Field(min_length=1, max_length=8)
    data_type: Literal["string", "number", "integer", "boolean", "array", "object"]
    required: bool = True
    nullable: bool = False
    source_unit: str | None = Field(default=None, min_length=1, max_length=80)
    target_unit: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def check(self):
        if any(not part or len(part) > 200 or part.startswith("_") for part in self.source):
            raise ValueError("Source paths must contain public field names")
        if bool(self.source_unit) != bool(self.target_unit):
            raise ValueError("Both source and target units are required")
        if self.source_unit and self.data_type not in {"number", "integer"}:
            raise ValueError("Unit conversion requires a numeric field")
        return self


class RowMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fields: list[FieldBinding] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_targets(self):
        if len({field.target for field in self.fields}) != len(self.fields):
            raise ValueError("Mapping targets must be unique")
        return self


def parse_mapping(config: dict) -> RowMapping:
    try:
        mapping = RowMapping.model_validate(config)
    except ValidationError as exc:
        # Never include input values in errors (they may contain sensitive data).
        path = ".".join(str(part) for part in exc.errors()[0]["loc"])
        raise MappingError("invalid_row_mapping", path or "config") from None
    for index, field in enumerate(mapping.fields):
        if field.source_unit:
            try:
                normalize_units({"properties": {field.target: {"x-unit-code": field.target_unit}}},
                                {field.target: {"value": 1, "unit": field.source_unit}})
            except (ValueError, TypeError):
                raise MappingError("invalid_mapping_units", f"fields.{index}") from None
    return mapping


def map_rows(config: dict, rows: list, *, max_rows: int) -> dict:
    mapping = parse_mapping(config)
    if not isinstance(rows, list):
        raise MappingError("mapping_rows_required", "rows")
    if len(rows) > max_rows:
        raise MappingError("mapping_budget_exceeded", "rows")
    result, lineage = [], []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise MappingError("mapping_row_invalid", f"rows.{index}")
        output = {}
        for field in mapping.fields:
            path = f"rows.{index}.{field.target}"
            value, found = row, True
            for segment in field.source:
                if not isinstance(value, dict) or segment not in value:
                    found = False
                    break
                value = value[segment]
            if not found:
                if field.required:
                    raise MappingError("mapping_field_missing", path)
                continue
            schema = {"type": [field.data_type, "null"] if field.nullable else field.data_type}
            try:
                validate_payload(schema, value, "mapping_source")
                if value is not None and field.source_unit:
                    converted, _ = normalize_units(
                        {"properties": {field.target: {"x-unit-code": field.target_unit}}},
                        {field.target: {"value": value, "unit": field.source_unit}},
                    )
                    value = converted[field.target]
                validate_payload(schema, value, "mapping_target")
                # Reject NaN/Infinity even when accepted as JSON Schema numbers.
                json.dumps(value, allow_nan=False)
            except (ContractValidationError, ValueError, TypeError):
                raise MappingError("mapping_value_invalid", path) from None
            output[field.target] = deepcopy(value)
        result.append(output)
        identity = {key: row[key] for key in ("ontology_id", "object_type", "object_id") if key in row}
        lineage.append({"row_index": index, "source": identity})
    return {"rows": result, "lineage": lineage}
