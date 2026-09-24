from types import SimpleNamespace

import pytest

from app.services.v2.scenario_actions import compile_actions, validate_definition
from app.services.v2.scenarios import ScenarioError


def action_type(parameters, rules):
    return SimpleNamespace(id="change_ticket_priority", version=2, enabled=True,
                           status="published", parameters=parameters, effects=rules)


def test_compiled_action_is_server_owned_and_ordered():
    parameters = [{"name": "ticket", "type": "object", "object_type": "Ticket"},
                  {"name": "priority", "type": "string"}]
    rules = [{"op": "set_property", "target_parameter": "ticket", "property": "priority",
              "value_parameter": "priority"}]
    validate_definition(parameters, rules)
    snapshot = SimpleNamespace(objects={("Ticket", "T-1"): {"priority": "P2"}}, edges=[])
    edits = compile_actions("ontology", "scenario", [{"action_type_id": "change_ticket_priority",
        "parameters": {"ticket": {"object_type": "Ticket", "object_id": "T-1"}, "priority": "P1"}}],
        {"change_ticket_priority": action_type(parameters, rules)}, snapshot)
    assert [edit.op for edit in edits] == ["invoke_action", "set_property"]
    assert edits[1].target.ontology_id == "ontology"
    assert edits[1].expected_old_value == "P2"
    assert edits[1].value == "P1"
    assert edits[0].parameters["action_type_version"] == 2


def test_action_rejects_forged_parameter_and_missing_object():
    parameters = [{"name": "target", "type": "object"}, {"name": "value", "type": "number"}]
    rules = [{"op": "set_property", "target_parameter": "target", "property": "score",
              "value_parameter": "value"}]
    kind = action_type(parameters, rules)
    snapshot = SimpleNamespace(objects={("Ticket", "T-1"): {"score": 1}}, edges=[])
    with pytest.raises(ScenarioError, match="Unknown Action parameter"):
        compile_actions("ontology", "scenario", [{"action_type_id": kind.id,
            "parameters": {"target": {"object_type": "Ticket", "object_id": "T-1"},
                           "value": 2, "scenario_id": "forged"}}], {kind.id: kind}, snapshot)
    with pytest.raises(ScenarioError, match="does not identify an object"):
        compile_actions("ontology", "scenario", [{"action_type_id": kind.id,
            "parameters": {"target": {"object_type": "Ticket", "object_id": "missing"},
                           "value": 2}}], {kind.id: kind}, snapshot)


def test_multiple_actions_use_prior_edits_as_expected_values():
    parameters = [{"name": "target", "type": "object"}, {"name": "value", "type": "string"}]
    kind = action_type(parameters, [{"op": "set_property", "target_parameter": "target",
                                     "property": "state", "value_parameter": "value"}])
    target = {"object_type": "Ticket", "object_id": "T-1"}
    snapshot = SimpleNamespace(objects={("Ticket", "T-1"): {"state": "open"}}, edges=[])
    edits = compile_actions("ontology", "scenario", [
        {"action_type_id": kind.id, "parameters": {"target": target, "value": "review"}},
        {"action_type_id": kind.id, "parameters": {"target": target, "value": "closed"}}],
        {kind.id: kind}, snapshot)
    assert [edit.expected_old_value for edit in edits if edit.op == "set_property"] == ["open", "review"]
