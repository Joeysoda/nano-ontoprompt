"""Portable Study package contracts without application/database fixtures."""
from copy import deepcopy
import pytest

from app.services.v2.scenario_studies import StudyError, stable_hash, validate_spec
from app.services.v2.study_packages import PACKAGE_FORMAT, rebind_spec


def package():
    payload = {
        "format": PACKAGE_FORMAT,
        "name": "Cycle capacity",
        "description": "Portable domain model",
        "source": {"study_id": "source", "version": 1, "spec_hash": "sha256:source"},
        "dependencies": [{"asset_key": "capacity_replan_v1", "version": "1.0.0"}],
        "spec": {
            "nodes": [
                {"key": "read", "kind": "object_query", "outputs": {"objects": "object_set"},
                 "config": {"type": "Operation", "select": ["runtime_minutes", "work_center"]}},
                {"key": "adapt", "kind": "row_mapping", "inputs": {"rows": {"from": "read.objects"}},
                 "outputs": {"rows": "rows", "lineage": "lineage"},
                 "config": {"fields": [
                     {"target": "duration", "source": ["properties", "runtime_minutes"], "data_type": "number",
                      "required": True, "nullable": False, "source_unit": "min", "target_unit": "s"},
                     {"target": "resource", "source": ["properties", "work_center"], "data_type": "string",
                      "required": True, "nullable": False},
                 ]}},
                {"key": "plan", "kind": "function", "implementation": {"key": "capacity_replan_v1", "version": "1.0.0"},
                 "inputs": {"tasks": {"from": "adapt.rows"}, "capacity": 1000}, "outputs": {"feasible": "feasible"}},
            ],
            "edges": [
                {"source": "read.objects", "target": "adapt.rows"},
                {"source": "adapt.rows", "target": "plan.tasks"},
            ],
            "metrics": [{"key": "feasible", "type": "boolean", "source": "plan.feasible"}],
        },
    }
    payload["package_digest"] = stable_hash(payload)
    return payload


def bindings():
    return {"types": {"Operation": "Job"}, "properties": {"Operation": {
        "runtime_minutes": "elapsed_seconds", "work_center": "machine_id",
    }}}


def test_rebinding_rewrites_query_and_direct_mapping_paths_without_mutating_package():
    source = package()
    before = deepcopy(source)
    spec = rebind_spec(source, bindings())
    assert source == before
    query, mapping = spec.nodes[:2]
    assert query.config == {"type": "Job", "select": ["elapsed_seconds", "machine_id"]}
    assert [field["source"] for field in mapping.config["fields"]] == [
        ["properties", "elapsed_seconds"], ["properties", "machine_id"],
    ]
    assert validate_spec(spec) == ["read", "adapt", "plan"]


def test_package_requires_complete_explicit_bindings_and_valid_digest():
    with pytest.raises(StudyError) as error:
        rebind_spec(package(), {"types": {}, "properties": {}})
    assert error.value.code == "type_binding_required"
    incomplete = bindings()
    incomplete["properties"]["Operation"].pop("work_center")
    with pytest.raises(StudyError) as error:
        rebind_spec(package(), incomplete)
    assert error.value.code == "property_binding_required"
    tampered = package()
    tampered["name"] = "Changed after signing"
    with pytest.raises(StudyError) as error:
        rebind_spec(tampered, bindings())
    assert error.value.code == "package_digest_mismatch"


def test_rebinding_does_not_rewrite_unrelated_nested_paths():
    value = package()
    value["spec"]["nodes"][2]["inputs"]["manual"] = {"path": ["properties", "runtime_minutes"]}
    value["package_digest"] = stable_hash({key: item for key, item in value.items() if key != "package_digest"})
    spec = rebind_spec(value, bindings())
    assert spec.nodes[2].inputs["manual"] == {"path": ["properties", "runtime_minutes"]}
