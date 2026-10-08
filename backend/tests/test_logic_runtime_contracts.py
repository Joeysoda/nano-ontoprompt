"""Runtime contract tests for stored heterogeneous logic."""
import pytest

from app.services.v2.logic_assets import ASSET_DEFINITIONS, EXECUTORS, execute
from app.services.v2.logic_contracts import ContractValidationError, bind_object_fields


def definition(key):
    return next(item for item in ASSET_DEFINITIONS if item["asset_key"] == key)


def test_full_json_schema_rejects_missing_and_wrong_input():
    with pytest.raises(ContractValidationError) as missing:
        execute(definition("cycle_time_v1"), {"setup_seconds": 1, "unit_seconds": 2})
    assert missing.value.stage == "input"
    assert any("quantity" in error["message"] for error in missing.value.errors)
    with pytest.raises(ContractValidationError):
        execute(definition("cycle_time_v1"), {"setup_seconds": 1, "unit_seconds": 2, "quantity": 1.5})


def test_contract_error_does_not_echo_rejected_input_value():
    from app.services.v2.logic_contracts import validate_payload
    secret = "sensitive-customer-value"
    with pytest.raises(ContractValidationError) as caught:
        validate_payload({"type": "object", "properties": {"quantity": {"type": "integer"}}},
                         {"quantity": secret}, "input")
    assert secret not in str(caught.value.errors)

def test_output_is_validated_before_becoming_a_result(monkeypatch):
    monkeypatch.setitem(EXECUTORS, "cycle_time_v1", lambda _inputs: {"total_seconds": "not-a-number"})
    with pytest.raises(ContractValidationError) as failure:
        execute(definition("cycle_time_v1"), {"setup_seconds": 1, "unit_seconds": 2, "quantity": 1})
    assert failure.value.stage == "output"


def test_binding_spec_maps_nested_ontology_fields_without_inventing_values():
    asset = definition("observation_summary_v1")
    inputs, trace = bind_object_fields(asset, {"observation": {"values": [1, 2, 3]}, "ignored": 9})
    assert inputs == {"values": [1, 2, 3]}
    assert trace[0]["stage"] == "bind_object"
    assert "observation" not in str(trace)


def test_unit_envelope_is_normalized_before_execution():
    result = execute(definition("cycle_time_v1"), {
        "setup_seconds": {"value": 2, "unit": "min"},
        "unit_seconds": {"value": 1, "unit": "s"},
        "quantity": 2,
    })
    assert result["output"]["total_seconds"] == 122
    assert any(item["stage"] == "normalize_unit" for item in result["trace"])
