"""Generate/check the TypeScript mirror from Plan A Pydantic contracts."""
from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys
from typing import Any, Literal, get_args, get_origin


MODEL_NAMES = [
    "SemanticResourcePayload", "SemanticResourceContract", "LegacyCompatibilityContract",
    "OntologySourceMappingContract", "SemanticSchemaContract", "SemanticChangeRequest", "PolicyConditionContract",
    "SecurityPolicyRequest", "SecurityPolicyPatch", "SecurityPolicyContract",
    "PolicyEvaluateRequest", "ConsistencyRequestContract", "AuthorizationContextContract",
    "AuthorizationProjectionContract", "AdapterCapabilitiesContract", "ExecutionContextContract",
    "ResultManifestContract", "DataPlaneProjectionContract", "DataPlaneStatusContract", "DataPlaneCapabilitiesContract",
    "MigrationInstructionContract", "MigrationRequest", "SchemaMigrationInstructionStatusContract",
    "SchemaMigrationRunContract", "SchemaMigrationPlanContract",
]
TYPE_ALIASES = ["SemanticResourceKindContract", "PolicyEffectContract", "PolicyScopeContract"]
FRONTEND_ALIASES = {
    "SemanticResource": "SemanticResourceContract",
    "SemanticSchema": "SemanticSchemaContract",
    "PolicyCondition": "PolicyConditionContract",
    "PolicyEffect": "PolicyEffectContract",
    "PolicyScope": "PolicyScopeContract",
    "SecurityPolicy": "SecurityPolicyContract",
    "ConsistencyRequest": "ConsistencyRequestContract",
    "AuthorizationContext": "AuthorizationContextContract",
    "AuthorizationProjection": "AuthorizationProjectionContract",
    "AdapterCapabilities": "AdapterCapabilitiesContract",
    "ExecutionContext": "ExecutionContextContract",
    "ResultManifest": "ResultManifestContract",
    "DataPlaneStatus": "DataPlaneStatusContract",
    "MigrationInstruction": "MigrationInstructionContract",
    "SchemaMigrationPlan": "SchemaMigrationPlanContract",
}


def _literal_type(values: tuple[Any, ...]) -> str:
    return " | ".join(json.dumps(value, ensure_ascii=False) for value in values) or "never"


def _json_schema_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "anyOf" in schema or "oneOf" in schema:
        union = schema.get("anyOf") or schema.get("oneOf") or []
        return " | ".join(dict.fromkeys(_json_schema_type(item) for item in union))
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    if "enum" in schema:
        return _literal_type(tuple(schema["enum"]))
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(dict.fromkeys(_json_schema_type({**schema, "type": item}) for item in kind))
    if kind == "string":
        return "string"
    if kind == "integer" or kind == "number":
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "array":
        item_type = _json_schema_type(schema.get("items") or {})
        return f"Array<{item_type}>"
    if kind == "object" or "additionalProperties" in schema:
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"Record<string, {_json_schema_type(additional)}>"
        return "Record<string, unknown>"
    return "unknown"


def _render_type_alias(name: str, annotation: Any) -> str:
    if get_origin(annotation) is Literal:
        return f"export type {name} = {_literal_type(get_args(annotation))};"
    raise TypeError(f"unsupported Pydantic contract type alias: {name}")


def generate(module: Any) -> str:
    sections = [
        "/** Generated from backend/app/schemas/v2/semantic_core.py. Do not edit by hand. */",
        "",
    ]
    for alias_name in TYPE_ALIASES:
        if not hasattr(module, alias_name):
            raise SystemExit(f"Pydantic contract type alias missing: {alias_name}")
        sections.extend([_render_type_alias(alias_name, getattr(module, alias_name)), ""])

    for model_name in MODEL_NAMES:
        model = getattr(module, model_name, None)
        if model is None or not hasattr(model, "model_json_schema"):
            raise SystemExit(f"Pydantic contract model missing: {model_name}")
        schema = model.model_json_schema(mode="validation")
        required = set(schema.get("required") or [])
        sections.append(f"export interface {model_name} {{")
        for name, prop_schema in schema.get("properties", {}).items():
            optional = "" if name in required else "?"
            sections.append(f"  {name}{optional}: {_json_schema_type(prop_schema)}")
        if model.model_config.get("extra") == "allow":
            sections.append("  [key: string]: unknown")
        sections.extend(["}", ""])

    for alias, target in FRONTEND_ALIASES.items():
        sections.append(f"export type {alias} = {target}")
    sections.append("")
    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="仅检查生成文件是否最新")
    args = parser.parse_args()
    backend_root = pathlib.Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    module = importlib.import_module("app.schemas.v2.semantic_core")
    target = pathlib.Path(__file__).resolve().parents[2] / "frontend/src/types/semanticCore.ts"
    generated = generate(module)
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != generated:
            raise SystemExit(f"TypeScript contract is stale; run {pathlib.Path(__file__).name} to regenerate it")
        print(f"validated {len(MODEL_NAMES) + len(TYPE_ALIASES)} Pydantic contracts -> {target}")
        return
    target.write_text(generated, encoding="utf-8")
    print(f"generated {len(MODEL_NAMES) + len(TYPE_ALIASES)} Pydantic contracts -> {target}")


if __name__ == "__main__":
    main()
