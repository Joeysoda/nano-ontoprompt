"""Portable, immutable Study packages with explicit ontology rebinding.

Packages contain a published Study specification and dependency contracts. They
never contain data, credentials, run history, Function source, or implicit name
matching. Installation creates a draft so a target ontology owner must review
and publish it against the target metadata revision.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models.v2.logic_asset import LogicAsset
from app.models.v2.scenario_study import StudyDefinition, StudyVersion
from app.schemas.v2.scenario_study import StudySpec
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.scenario_studies import StudyError, stable_hash, validate_spec


PACKAGE_FORMAT = "ontology-study-package/v1"


def _dependency(asset: LogicAsset) -> dict[str, Any]:
    return {
        "asset_key": asset.asset_key,
        "version": asset.version,
        "interface_key": asset.interface_key,
        "interface_version": asset.interface_version,
        "input_schema_digest": stable_hash(asset.input_schema or {}),
        "output_schema_digest": stable_hash(asset.output_schema or {}),
        "executor_type": asset.executor_type,
    }


def export_package(db, study: StudyDefinition, version: StudyVersion) -> dict[str, Any]:
    spec = StudySpec.model_validate(version.spec_json)
    dependencies = []
    for node in spec.nodes:
        if node.kind not in {"function", "time_series"}:
            continue
        asset = db.query(LogicAsset).filter_by(
            ontology_id=study.ontology_id,
            asset_key=node.implementation.key,
            version=node.implementation.version,
            status="published",
        ).first()
        if asset is None:
            raise StudyError("package_dependency_missing", "A pinned Function dependency is unavailable", 409,
                             f"nodes.{node.key}.implementation")
        dependencies.append(_dependency(asset))
    payload = {
        "format": PACKAGE_FORMAT,
        "name": study.name,
        "description": study.description,
        "source": {"study_id": study.id, "version": version.version, "spec_hash": version.spec_hash},
        "spec": spec.model_dump(mode="json"),
        "dependencies": sorted(dependencies, key=lambda item: (item["asset_key"], item["version"])),
    }
    payload["package_digest"] = stable_hash(payload)
    return payload


def _validate_envelope(package: dict[str, Any]) -> None:
    if not isinstance(package, dict) or package.get("format") != PACKAGE_FORMAT:
        raise StudyError("invalid_package", "Unsupported Study package format", path="package.format")
    supplied = package.get("package_digest")
    unsigned = {key: value for key, value in package.items() if key != "package_digest"}
    if not isinstance(supplied, str) or supplied != stable_hash(unsigned):
        raise StudyError("package_digest_mismatch", "Study package content does not match its digest", path="package.package_digest")
    if not isinstance(package.get("dependencies"), list):
        raise StudyError("invalid_package", "Study package dependencies must be a list", path="package.dependencies")


def rebind_spec(package: dict[str, Any], bindings: dict[str, Any]) -> StudySpec:
    _validate_envelope(package)
    try:
        spec = deepcopy(package["spec"])
        type_map = bindings.get("types") or {}
        property_map = bindings.get("properties") or {}
    except (KeyError, AttributeError):
        raise StudyError("invalid_package_binding", "Package bindings must be objects", path="bindings") from None
    if not isinstance(type_map, dict) or not isinstance(property_map, dict):
        raise StudyError("invalid_package_binding", "Type and property bindings must be objects", path="bindings")
    query_types: dict[str, tuple[str, str]] = {}
    for index, node in enumerate(spec.get("nodes") or []):
        if node.get("kind") != "object_query":
            continue
        source_type = node.get("config", {}).get("type")
        target_type = type_map.get(source_type)
        if not isinstance(target_type, str) or not target_type:
            raise StudyError("type_binding_required", f"Object type {source_type!r} needs an explicit target binding",
                             path=f"bindings.types.{source_type}")
        fields = property_map.get(source_type)
        if not isinstance(fields, dict):
            raise StudyError("property_binding_required", f"Object type {source_type!r} needs property bindings",
                             path=f"bindings.properties.{source_type}")
        selected = []
        for field in node["config"].get("select") or []:
            target = fields.get(field)
            if not isinstance(target, str) or not target:
                raise StudyError("property_binding_required", f"Property {source_type}.{field} needs a target binding",
                                 path=f"bindings.properties.{source_type}.{field}")
            selected.append(target)
        node["config"]["type"] = target_type
        node["config"]["select"] = selected
        query_types[node["key"]] = (source_type, target_type)
    # A mapping directly fed by a query refers to that query's selected property
    # names. Rewrite only this well-defined boundary; other paths are unchanged.
    for index, node in enumerate(spec.get("nodes") or []):
        if node.get("kind") != "row_mapping":
            continue
        source_ref = (node.get("inputs", {}).get("rows") or {}).get("from")
        query_key = source_ref.split(".", 1)[0] if isinstance(source_ref, str) else None
        if query_key not in query_types:
            continue
        source_type, _ = query_types[query_key]
        fields = property_map[source_type]
        for field in node.get("config", {}).get("fields") or []:
            path = field.get("source")
            if isinstance(path, list) and len(path) >= 2 and path[0] == "properties" and path[1] in fields:
                field["source"] = ["properties", fields[path[1]], *path[2:]]
    result = StudySpec.model_validate(spec)
    validate_spec(result)
    return result


def inspect_package(db, ontology_id: str, package: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    spec = rebind_spec(package, bindings)
    metadata = load_sql_metadata(db, ontology_id)
    issues = []
    for node in spec.nodes:
        if node.kind != "object_query":
            continue
        try:
            type_meta = metadata.resolve_type("object", node.config["type"], f"nodes.{node.key}.config.type")
            for field in node.config["select"]:
                metadata.resolve_property(type_meta, field, f"nodes.{node.key}.config.select")
        except Exception as exc:
            issue = getattr(exc, "issue", None)
            issues.append({"code": getattr(exc, "code", "metadata_incompatible"),
                           "path": getattr(issue, "path", f"nodes.{node.key}.config"),
                           "message": getattr(issue, "message", str(exc))})
    installed = {(row.asset_key, row.version): row for row in db.query(LogicAsset).filter_by(
        ontology_id=ontology_id, status="published").all()}
    for dependency in package["dependencies"]:
        row = installed.get((dependency.get("asset_key"), dependency.get("version")))
        if row is None:
            issues.append({"code": "dependency_missing", "path": f"dependencies.{dependency.get('asset_key')}",
                           "message": "Pinned Function release is not installed in the target ontology"})
            continue
        if stable_hash(row.input_schema or {}) != dependency.get("input_schema_digest") or stable_hash(row.output_schema or {}) != dependency.get("output_schema_digest"):
            issues.append({"code": "dependency_contract_mismatch", "path": f"dependencies.{row.asset_key}",
                           "message": "Installed Function release has a different contract"})
    return {"valid": not issues, "issues": issues, "rebound_spec": spec.model_dump(mode="json"),
            "package_digest": package["package_digest"]}


def install_package(db, ontology_id: str, owner_id: str, package: dict[str, Any], bindings: dict[str, Any],
                    *, name: str | None = None) -> StudyDefinition:
    report = inspect_package(db, ontology_id, package, bindings)
    if report["issues"]:
        first = report["issues"][0]
        raise StudyError("package_incompatible", first["message"], 409, first["path"])
    row = StudyDefinition(
        ontology_id=ontology_id,
        owner_id=owner_id,
        name=(name or package["name"]).strip(),
        description=package.get("description") or "",
        draft_spec=report["rebound_spec"],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
