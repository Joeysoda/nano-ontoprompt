"""Compile user-authored, declarative Action types into isolated Scenario edits.

The public form accepts values and object references only. Targets, edit order,
and the hidden Scenario reference are resolved on the server.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
import re

from app.schemas.v2.scenario import Edit, LinkTarget, Target
from app.services.v2.scenarios import ScenarioError

PARAMETER_TYPES = {"string", "number", "boolean", "object"}
RULE_OPS = {"set_property", "unset_property", "create_object", "delete_object", "add_link", "remove_link"}
SAFE_NAME = re.compile(r"^[^\W\d_][\w]{0,199}$", re.UNICODE)
SAFE_RELATION = re.compile(r"^[A-Z][A-Z0-9_]{0,199}$")


def _public_name(value):
    return isinstance(value, str) and bool(SAFE_NAME.fullmatch(value)) and not value.startswith("_")


def validate_definition(parameters, rules):
    if not isinstance(parameters, list) or len(parameters) > 30 or not isinstance(rules, list) or not 1 <= len(rules) <= 30:
        raise ScenarioError("invalid_action_type", "Define 1–30 rules and at most 30 parameters")
    names = set()
    parameter_types = {}
    for parameter in parameters:
        if not isinstance(parameter, dict) or not isinstance(parameter.get("name"), str):
            raise ScenarioError("invalid_parameter", "Each parameter needs a name")
        name = parameter["name"]
        if not name.isidentifier() or name in names or parameter.get("type") not in PARAMETER_TYPES:
            raise ScenarioError("invalid_parameter", "Parameter names must be unique identifiers with a supported type")
        names.add(name)
        parameter_types[name] = parameter["type"]
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("op") not in RULE_OPS:
            raise ScenarioError("invalid_rule", "Unsupported Action rule")
        op = rule["op"]
        if op in {"set_property", "unset_property", "delete_object"} and rule.get("target_parameter") not in names:
            raise ScenarioError("invalid_rule", "Object rule needs a target parameter")
        if op in {"set_property", "unset_property", "delete_object"} and parameter_types.get(rule.get("target_parameter")) != "object":
            raise ScenarioError("invalid_rule", "Target parameter must be an object reference")
        if op in {"set_property", "unset_property"} and not _public_name(rule.get("property")):
            raise ScenarioError("invalid_rule", "Property rule needs a property name")
        if op == "set_property" and ("value_parameter" in rule) == ("static_value" in rule):
            raise ScenarioError("invalid_rule", "Set property needs exactly one value source")
        if "value_parameter" in rule and rule["value_parameter"] not in names:
            raise ScenarioError("invalid_rule", "Unknown value parameter")
        if rule.get("value_parameter") and parameter_types.get(rule["value_parameter"]) == "object":
            raise ScenarioError("invalid_rule", "Object references cannot be stored as scalar properties")
        if "static_value" in rule and isinstance(rule["static_value"], (dict, list)):
            raise ScenarioError("invalid_rule", "Static property values must be scalar")
        if op == "create_object" and (not rule.get("object_type") or not isinstance(rule.get("properties", {}), dict)):
            raise ScenarioError("invalid_rule", "Create object needs an object type and property mappings")
        if op == "create_object":
            if not _public_name(rule["object_type"]):
                raise ScenarioError("invalid_rule", "Invalid object type")
            for prop, mapping in rule.get("properties", {}).items():
                if not _public_name(prop) or not isinstance(mapping, dict) or ("parameter" in mapping) == ("static" in mapping):
                    raise ScenarioError("invalid_rule", "Invalid created object property mapping")
                if "parameter" in mapping and parameter_types.get(mapping["parameter"]) not in {"string", "number", "boolean"}:
                    raise ScenarioError("invalid_rule", "Property mapping requires a scalar parameter")
        if op in {"add_link", "remove_link"} and (rule.get("source_parameter") not in names or
              rule.get("target_parameter") not in names or not rule.get("relation_type")):
            raise ScenarioError("invalid_rule", "Link rule needs source, target and relation type")
        if op in {"add_link", "remove_link"} and (parameter_types[rule["source_parameter"]] != "object" or
               parameter_types[rule["target_parameter"]] != "object" or not SAFE_RELATION.fullmatch(rule["relation_type"])):
            raise ScenarioError("invalid_rule", "Link rule requires object references and a valid relation type")
    return parameters, rules


def bind_parameters(definitions, submitted, objects):
    if not isinstance(submitted, dict) or set(submitted) - {item["name"] for item in definitions}:
        raise ScenarioError("invalid_parameter", "Unknown Action parameter")
    values = {}
    for item in definitions:
        name = item["name"]
        value = submitted.get(name, item.get("default"))
        if value is None:
            if item.get("required", True):
                raise ScenarioError("missing_parameter", f"{name} is required")
            values[name] = None
            continue
        kind = item["type"]
        if kind == "string" and not isinstance(value, str):
            raise ScenarioError("invalid_parameter", f"{name} must be text")
        if kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ScenarioError("invalid_parameter", f"{name} must be numeric")
        if kind == "number" and not isfinite(value):
            raise ScenarioError("invalid_parameter", f"{name} must be finite")
        if kind == "boolean" and not isinstance(value, bool):
            raise ScenarioError("invalid_parameter", f"{name} must be true or false")
        if kind == "object":
            if not isinstance(value, dict) or set(value) != {"object_type", "object_id"}:
                raise ScenarioError("invalid_parameter", f"{name} must be an object reference")
            if (value["object_type"], value["object_id"]) not in objects:
                raise ScenarioError("object_not_found", f"{name} does not identify an object in the base view", 404)
            allowed_type = item.get("object_type")
            if allowed_type and value["object_type"] != allowed_type:
                raise ScenarioError("invalid_parameter", f"{name} must reference {allowed_type}")
        choices = item.get("choices")
        if choices and value not in choices:
            raise ScenarioError("invalid_parameter", f"{name} is outside its allowed choices")
        values[name] = value
    return values


def compile_actions(ontology_id, scenario_id, actions, action_types, snapshot):
    """Return a complete, ordered ChangeSet; each invocation is auditable."""
    if not isinstance(actions, list) or len(actions) > 30:
        raise ScenarioError("invalid_actions", "A Scenario supports at most 30 Actions")
    objects = {key: deepcopy(value) for key, value in snapshot.objects.items()}
    links = set(snapshot.edges)
    edits = []

    def add(**kwargs):
        edits.append(Edit(sequence=len(edits), **kwargs))

    def target(value):
        return Target(ontology_id=ontology_id, concrete_type=value["object_type"], object_id=value["object_id"])

    for action_index, invocation in enumerate(actions):
        if not isinstance(invocation, dict) or set(invocation) != {"action_type_id", "parameters"}:
            raise ScenarioError("invalid_actions", "Each Action needs a type and parameters")
        action_type = action_types.get(invocation["action_type_id"])
        if not action_type or not action_type.enabled or action_type.status != "published":
            raise ScenarioError("action_type_unavailable", "Select a published Action type", 422)
        validate_definition(action_type.parameters, action_type.effects)
        values = bind_parameters(action_type.parameters, invocation["parameters"], objects)
        source = f"{action_type.id}@{action_type.version}"
        add(op="invoke_action", source_action=source, action_key=action_type.id,
            parameters={"action_type_version": action_type.version, "values": values})
        for rule_index, rule in enumerate(action_type.effects):
            op = rule["op"]
            if op in {"set_property", "unset_property", "delete_object"}:
                ref = values[rule["target_parameter"]]
                key = (ref["object_type"], ref["object_id"])
                if key not in objects:
                    raise ScenarioError("object_not_found", "Action target is unavailable", 404)
                if op == "delete_object":
                    add(op=op, target=target(ref), source_action=source)
                    objects.pop(key)
                    links = {link for link in links if key not in (link[0], link[2])}
                else:
                    prop = rule["property"]
                    before = objects[key].get(prop)
                    if op == "set_property":
                        value = values[rule["value_parameter"]] if "value_parameter" in rule else rule["static_value"]
                        if isinstance(value, (dict, list)):
                            raise ScenarioError("invalid_value", "Object and list values cannot be stored as scalar properties")
                        add(op=op, target=target(ref), property=prop, value=value,
                            expected_old_value=before, source_action=source)
                        objects[key][prop] = value
                    else:
                        add(op=op, target=target(ref), property=prop,
                            expected_old_value=before, source_action=source)
                        objects[key].pop(prop, None)
            elif op == "create_object":
                object_type = rule["object_type"]
                object_id = f"scenario:{scenario_id}:{action_index}:{rule_index}"
                props = {}
                for prop, mapping in rule.get("properties", {}).items():
                    if not isinstance(mapping, dict) or ("parameter" in mapping) == ("static" in mapping):
                        raise ScenarioError("invalid_rule", "Property mapping needs one value source")
                    props[prop] = values[mapping["parameter"]] if "parameter" in mapping else mapping["static"]
                    if isinstance(props[prop], (dict, list)):
                        raise ScenarioError("invalid_value", "Created object properties must be scalar")
                props.setdefault("name", object_id)
                ref = {"object_type": object_type, "object_id": object_id}
                if (object_type, object_id) in objects:
                    raise ScenarioError("duplicate_object", "Action creates the same object twice")
                add(op=op, target=target(ref), object_type=object_type,
                    properties=props, source_action=source)
                objects[(object_type, object_id)] = props
            else:
                source_ref = values[rule["source_parameter"]]
                target_ref = values[rule["target_parameter"]]
                edge = ((source_ref["object_type"], source_ref["object_id"]), rule["relation_type"],
                        (target_ref["object_type"], target_ref["object_id"]))
                if op == "add_link":
                    links.add(edge)
                else:
                    links.discard(edge)
                add(op=op, link=LinkTarget(ontology_id=ontology_id, relation_type=edge[1],
                                         source=target(source_ref), target=target(target_ref)), source_action=source)
    return edits
