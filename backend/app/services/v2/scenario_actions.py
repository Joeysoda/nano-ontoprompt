"""Compile Action types into one target-neutral, ordered edit batch.

Both live execution and Scenario revisions consume the ``Edit`` values emitted
here.  The compiler accepts the newer declarative ``op`` rules and the older
runtime ``action`` rules, but third-party/UI syntax and unknown effects never
cross this boundary.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
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


def _authorize(action_type, actor):
    rules = getattr(action_type, "permission_rules", None) or []
    if not rules:
        return
    if actor is None:
        raise ScenarioError("forbidden", "Action permission context is required", 403, "permission_rules")
    supported = {"role", "principal_id"}
    for rule in rules:
        if not isinstance(rule, dict) or not set(rule) or set(rule) - supported:
            raise ScenarioError("invalid_permission_rule", "Unsupported Action permission rule", 422, "permission_rules")
    if not any(
        ("role" not in rule or rule["role"] == getattr(actor, "role", None)) and
        ("principal_id" not in rule or rule["principal_id"] == getattr(actor, "id", None))
        for rule in rules
    ):
        raise ScenarioError("forbidden", "Action permission denied", 403, "permission_rules")


def _object_by_id(objects, object_id, expected_type=None):
    matches = [(key, value) for key, value in objects.items()
               if key[1] == object_id and (not expected_type or key[0] == expected_type)]
    if len(matches) != 1:
        raise ScenarioError("object_not_found" if not matches else "ambiguous_object",
                            "Action target does not identify exactly one object", 404 if not matches else 422,
                            "target_object_id")
    return matches[0]


def _validate_criteria(action_type, submitted, target_object_id, objects):
    for index, criterion in enumerate(
        getattr(action_type, "submission_criteria", None) or []
    ):
        if not isinstance(criterion, dict):
            raise ScenarioError("invalid_submission_criterion", "Submission criterion must be an object",
                                path=f"submission_criteria[{index}]")
        ctype = str(criterion.get("type") or "").lower()
        # Generated discovery metadata is descriptive until it is migrated to
        # an executable criterion. It must not be mistaken for a passed check.
        if not ctype and criterion.get("logic_type"):
            continue
        if ctype in {"required_target", "entity_exists", "field_equals"}:
            object_id = target_object_id or submitted.get("target_id")
            if not object_id:
                raise ScenarioError("submission_criteria_failed", "target_object_id is required", 422,
                                    f"submission_criteria[{index}]")
            key, props = _object_by_id(
                objects,
                str(object_id),
                getattr(action_type, "target_entity_type", None),
            )
            if ctype == "field_equals":
                field, expected = criterion.get("field"), criterion.get("value")
                if not field or props.get(field) != expected:
                    raise ScenarioError("submission_criteria_failed",
                                        f"{key[0]}.{field} does not match the required value", 422,
                                        f"submission_criteria[{index}]")
        elif ctype == "required_param":
            name = criterion.get("name")
            if not name or submitted.get(str(name)) in (None, ""):
                raise ScenarioError("submission_criteria_failed", f"{name or 'parameter'} is required", 422,
                                    f"submission_criteria[{index}]")
        elif ctype:
            raise ScenarioError("invalid_submission_criterion", f"Unsupported submission criterion: {ctype}",
                                path=f"submission_criteria[{index}]")


def _bind_legacy_parameters(definitions, submitted):
    if not isinstance(submitted, dict):
        raise ScenarioError("invalid_parameter", "Action parameters must be an object")
    declared = {item.get("name") for item in definitions if isinstance(item, dict) and item.get("name")}
    unknown = set(submitted) - declared
    # target_id/source_id/target_id are historical routing fields and may be
    # supplied even when an old definition omitted them.
    unknown -= {"target_id", "source_id"}
    if unknown:
        raise ScenarioError("invalid_parameter", "Unknown Action parameter")
    values = {}
    for item in definitions:
        if not isinstance(item, dict) or not item.get("name"):
            raise ScenarioError("invalid_parameter", "Each parameter needs a name")
        name = str(item["name"])
        value = submitted.get(name, item.get("default"))
        if value is None and item.get("required", True):
            raise ScenarioError("missing_parameter", f"{name} is required")
        kind = str(item.get("type") or "string")
        if value is not None and kind in {"number", "integer", "decimal"} and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ScenarioError("invalid_parameter", f"{name} must be numeric")
        if value is not None and kind == "boolean" and not isinstance(value, bool):
            raise ScenarioError("invalid_parameter", f"{name} must be true or false")
        if value is not None and kind in {"object", "struct"} and name == "data" and not isinstance(value, dict):
            raise ScenarioError("invalid_parameter", f"{name} must be an object")
        values[name] = value
    for routing_name in ("target_id", "source_id"):
        if routing_name in submitted:
            values[routing_name] = submitted[routing_name]
    return values


def compile_actions(ontology_id, scenario_id, actions, action_types, snapshot, *, actor=None, id_factory=None, function_resolver=None):
    """Return one complete, ordered edit batch for live or Scenario targets.

    ``scenario_id`` is only used for deterministic Scenario-created IDs.  A
    live caller can provide ``id_factory`` for its own IDs; the resulting
    ``Edit`` objects are otherwise identical.
    """
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
        if not isinstance(invocation, dict) or not {"action_type_id", "parameters"}.issubset(invocation) or set(invocation) - {"action_type_id", "parameters", "target_object_id"}:
            raise ScenarioError("invalid_actions", "Each Action needs a type and parameters")
        action_type = action_types.get(invocation["action_type_id"])
        if not action_type or not action_type.enabled or action_type.status != "published":
            raise ScenarioError("action_type_unavailable", "Select a published Action type", 422)
        _authorize(action_type, actor)
        if getattr(action_type, "side_effects", None):
            raise ScenarioError("unsupported_side_effect", "External side effects require a configured delivery adapter", 422, "side_effects")
        function_manifest = None
        current_id_factory = id_factory
        if getattr(action_type, "backed_by_function", None):
            if action_type.effects:
                raise ScenarioError('mutually_exclusive_rules', 'Function and ontology rules cannot be mixed')
            if function_resolver is None:
                raise ScenarioError("function_binding_unavailable", "Function binding must resolve before compiling ontology edits", 422, "backed_by_function")
            _validate_criteria(action_type, invocation['parameters'], invocation.get('target_object_id'), objects)
            definitions, submitted, rules, generated_ids, function_manifest = function_resolver(action_type, invocation, SimpleNamespace(objects=objects, edges=links))
            action_type = SimpleNamespace(id=action_type.id, version=action_type.version, parameters=definitions, effects=rules, submission_criteria=[], target_entity_type=getattr(action_type, 'target_entity_type', None))
            invocation = dict(invocation, parameters=submitted)
            current_id_factory = lambda action_index, rule_index: generated_ids[rule_index]
        values = {}
        canonical_rules = all(isinstance(rule, dict) and "op" in rule for rule in (action_type.effects or []))
        legacy_rules = all(isinstance(rule, dict) and "action" in rule for rule in (action_type.effects or []))
        if action_type.effects and not (canonical_rules or legacy_rules):
            raise ScenarioError("invalid_rule", "Action effects must use one supported rule format")
        if canonical_rules:
            validate_definition(action_type.parameters, action_type.effects)
            values = bind_parameters(action_type.parameters, invocation["parameters"], objects)
        elif legacy_rules:
            legacy_submitted = dict(invocation["parameters"])
            if invocation.get("target_object_id") and "target_id" not in legacy_submitted:
                legacy_submitted["target_id"] = invocation["target_object_id"]
            values = _bind_legacy_parameters(action_type.parameters or [], legacy_submitted)
        _validate_criteria(action_type, invocation["parameters"], invocation.get("target_object_id"), objects)
        source = f"{action_type.id}@{action_type.version}"
        add(op="invoke_action", source_action=source, action_key=action_type.id,
            parameters={"action_type_version": action_type.version, "values": values, **({'function': function_manifest} if function_manifest else {})})
        for rule_index, rule in enumerate(action_type.effects):
            op = rule.get("op") if canonical_rules else rule.get("action")
            if not canonical_rules:
                target_id = invocation.get("target_object_id") or values.get("target_id") or values.get("source_id")
                if op in {"set_property", "create_object", "create_node", "update_object", "merge_relationship", "delete_relationship"} and op not in {"create_object", "create_node"} and not target_id and op == "set_property":
                    raise ScenarioError("invalid_parameter", "Legacy property Action requires target_object_id")
                if op == "set_property":
                    key, props = _object_by_id(objects, str(target_id), action_type.target_entity_type)
                    prop = rule.get("property")
                    if not _public_name(prop):
                        raise ScenarioError("invalid_rule", "Property rule needs a public property name")
                    value = values.get(str(prop), invocation["parameters"].get(str(prop)))
                    if isinstance(value, (dict, list)):
                        raise ScenarioError("invalid_value", "Object and list values cannot be stored as scalar")
                    ref = {"object_type": key[0], "object_id": key[1]}
                    add(op="set_property", target=target(ref), property=prop, value=value,
                        expected_old_value=props.get(prop), source_action=source)
                    props[prop] = value
                    continue
                if op in {"create_object", "create_node"}:
                    data = values.get("data") or invocation["parameters"].get("data") or {}
                    if not isinstance(data, dict):
                        raise ScenarioError("invalid_parameter", "create_object data must be an object")
                    object_type = rule.get("entity_type") or rule.get("object_type") or action_type.target_entity_type or "Object"
                    object_id = current_id_factory(action_index, rule_index) if current_id_factory else f"scenario:{scenario_id}:{action_index}:{rule_index}"
                    props = {key: value for key, value in data.items() if _public_name(str(key))}
                    props.setdefault("name", object_id)
                    ref = {"object_type": object_type, "object_id": object_id}
                    if (object_type, object_id) in objects:
                        raise ScenarioError("duplicate_object", "Action creates the same object twice")
                    add(op="create_object", target=target(ref), object_type=object_type, properties=props, source_action=source)
                    objects[(object_type, object_id)] = props
                    continue
                if op == "update_object":
                    target_id = invocation.get("target_object_id") or values.get("target_id")
                    key, props = _object_by_id(objects, str(target_id), action_type.target_entity_type)
                    data = values.get("data") or invocation["parameters"].get("data") or {}
                    if not isinstance(data, dict):
                        raise ScenarioError("invalid_parameter", "update_object data must be an object")
                    ref = {"object_type": key[0], "object_id": key[1]}
                    for prop, value in data.items():
                        if not _public_name(str(prop)) or isinstance(value, (dict, list)):
                            raise ScenarioError("invalid_rule", "update_object only accepts scalar public properties")
                        add(op="set_property", target=target(ref), property=str(prop), value=value,
                            expected_old_value=props.get(prop), source_action=source)
                        props[str(prop)] = value
                    continue
                if op in {"merge_relationship", "delete_relationship"}:
                    source_id = values.get("source_id") or invocation["parameters"].get("source_id")
                    target_id = values.get("target_id") or invocation["parameters"].get("target_id")
                    relation_type = rule.get("relation_type")
                    source_key, _ = _object_by_id(objects, str(source_id))
                    target_key, _ = _object_by_id(objects, str(target_id))
                    edge = (source_key, relation_type, target_key)
                    if op == "merge_relationship":
                        links.add(edge)
                    else:
                        links.discard(edge)
                    add(op="add_link" if op == "merge_relationship" else "remove_link",
                        link=LinkTarget(ontology_id=ontology_id, relation_type=relation_type,
                                        source=target({"object_type": source_key[0], "object_id": source_key[1]}),
                                        target=target({"object_type": target_key[0], "object_id": target_key[1]})),
                        source_action=source)
                    continue
                if op in {"review", "repair", "writeback"}:
                    raise ScenarioError("unsupported_effect", f"Effect '{op}' has no ontology edit contract")
                raise ScenarioError("unsupported_effect", f"Unknown Action effect '{op}'")
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
                object_id = current_id_factory(action_index, rule_index) if current_id_factory else f"scenario:{scenario_id}:{action_index}:{rule_index}"
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
