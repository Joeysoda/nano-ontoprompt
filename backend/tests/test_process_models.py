from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.v2.process_model import ProcessDefinition
from app.services.v2.process_models import action_contract
from app.services.v2.scenario_actions import compile_actions
from app.services.v2.scenarios import ScenarioError


def definition():
    return ProcessDefinition.model_validate({
        "states": [{"key": "reported", "label": "已报告", "initial": True},
                   {"key": "assigned", "label": "已分派"},
                   {"key": "closed", "label": "已关闭", "terminal": True}],
        "transitions": [{"key": "assign", "label": "分派", "from": "reported", "to": "assigned",
                         "permission_rules": [{"role": "editor"}]},
                        {"key": "close", "label": "关闭", "from": "assigned", "to": "closed"}],
    })


def test_process_graph_rejects_unreachable_and_terminal_outgoing_states():
    with pytest.raises(ValidationError, match="Unreachable"):
        ProcessDefinition.model_validate({"states": [{"key": "a", "label": "A", "initial": True},
                                                       {"key": "b", "label": "B"}, {"key": "c", "label": "C"}],
                                          "transitions": [{"key": "go", "label": "go", "from": "a", "to": "b"}]})
    with pytest.raises(ValidationError, match="Terminal"):
        ProcessDefinition.model_validate({"states": [{"key": "a", "label": "A", "initial": True, "terminal": True},
                                                       {"key": "b", "label": "B"}],
                                          "transitions": [{"key": "go", "label": "go", "from": "a", "to": "b"}]})


def test_transition_compiles_to_guarded_standard_action():
    machine = SimpleNamespace(id="m1", name="维修工单", entity_type_name="WorkOrder", state_property="status")
    transition = definition().transitions[0]
    values = action_contract(machine, transition)
    assert values["submission_criteria"] == [{"type": "field_equals", "field": "status", "value": "reported"}]
    assert values["effects"][0]["static_value"] == "assigned"
    assert values["permission_rules"] == [{"role": "editor"}]

    action = SimpleNamespace(id="assign-action", version=1, enabled=True, status="published", **values)
    snapshot = SimpleNamespace(objects={("WorkOrder", "WO-1"): {"status": "reported"}}, edges=[])
    edits = compile_actions("o", "s", [{"action_type_id": action.id,
        "parameters": {"target": {"object_type": "WorkOrder", "object_id": "WO-1"}}, "target_object_id": "WO-1"}],
        {action.id: action}, snapshot, actor=SimpleNamespace(id="u1", role="editor"))
    assert [(row.op, row.property, row.value) for row in edits if row.op == "set_property"] == [("set_property", "status", "assigned")]

    stale = SimpleNamespace(objects={("WorkOrder", "WO-1"): {"status": "closed"}}, edges=[])
    with pytest.raises(ScenarioError) as error:
        compile_actions("o", "s", [{"action_type_id": action.id,
            "parameters": {"target": {"object_type": "WorkOrder", "object_id": "WO-1"}}, "target_object_id": "WO-1"}],
            {action.id: action}, stale, actor=SimpleNamespace(id="u1", role="editor"))
    assert error.value.code == "submission_criteria_failed"
