"""``decision/v1``: the domain-neutral result a decision Function returns.

Any user Function can emit it; the Decision Workspace renders it and a
Decision Package compiles its candidates through the ontology's own,
user-configured scenario Action types.  The platform checks structure and
invariants it can know without the domain (unique ids, referenced subjects,
"inapplicable exactly when a hard constraint fails"); the metric arithmetic
is the Function's responsibility and is shown with its evidence.
"""
from __future__ import annotations

from app.services.v2.logic_contracts import validate_payload

SCHEMA_ID = "decision/v1"
_ID = {"type": "string", "minLength": 1, "maxLength": 300}
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema", "title", "subject_type", "metric", "subjects", "candidates"],
    "properties": {
        "schema": {"const": SCHEMA_ID},
        "title": {"type": "string", "maxLength": 200},
        "subject_type": {"type": "string", "maxLength": 120},
        "subject_label": {"type": "string", "maxLength": 60},
        "metric": {"type": "object", "required": ["key", "label", "direction", "baseline"], "properties": {
            "key": {"type": "string"}, "label": {"type": "string"}, "unit": {"type": "string"},
            "direction": {"enum": ["minimize", "maximize"]}, "baseline": {"type": "number"}}},
        "kpis": {"type": "array", "maxItems": 8, "items": {"type": "object", "required": ["label", "value"]}},
        "subjects": {"type": "array", "maxItems": 5000, "items": {"type": "object", "required": ["id", "label", "status"], "properties": {
            "id": _ID, "label": {"type": "string"}, "status": {"type": "string"}, "at_risk": {"type": "boolean"},
            "metric_value": {"type": "number"}, "reasons": {"type": "array", "items": {"type": "string"}},
            "path": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "object", "required": ["label", "value"]}}}}},
        "candidates": {"type": "array", "maxItems": 50, "items": {"type": "object",
            "required": ["id", "label", "applicable", "hard_constraints", "metric_after", "protected", "harmed", "actions"], "properties": {
                "id": _ID, "label": {"type": "string"}, "applicable": {"type": "boolean"},
                "hard_constraints": {"type": "array", "items": {"type": "object", "required": ["code"]}},
                "metric_after": {"type": "number"}, "cost": {"type": "number"},
                "protected": {"type": "array", "items": _ID}, "harmed": {"type": "array", "items": _ID},
                "actions": {"type": "array", "maxItems": 30, "items": {"type": "object", "required": ["action_type", "parameters"],
                    "properties": {"action_type": {"type": "string"}, "parameters": {"type": "object"}}}}}}},
        "impact_path": {"type": "object"},
    },
}


class DecisionOutputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def is_decision(value) -> bool:
    return isinstance(value, dict) and value.get("schema") == SCHEMA_ID


def validate_decision(value: dict) -> dict:
    validate_payload(SCHEMA, value, "decision")
    subjects = [row["id"] for row in value["subjects"]]
    if len(set(subjects)) != len(subjects):
        raise DecisionOutputError("duplicate_subject", "Subject ids must be unique")
    candidates = [row["id"] for row in value["candidates"]]
    if len(set(candidates)) != len(candidates):
        raise DecisionOutputError("duplicate_candidate", "Candidate ids must be unique")
    known = set(subjects)
    for row in value["candidates"]:
        if (set(row["protected"]) | set(row["harmed"])) - known:
            raise DecisionOutputError("unknown_subject", f"Candidate {row['id']} references subjects not in the result")
        if set(row["protected"]) & set(row["harmed"]):
            raise DecisionOutputError("contradictory_candidate", f"Candidate {row['id']} both protects and harms a subject")
        if row["applicable"] == bool(row["hard_constraints"]):
            raise DecisionOutputError("inconsistent_constraints", f"Candidate {row['id']} must be inapplicable exactly when a hard constraint fails")
    return value


def find_decision_candidate(run, candidate_id: str):
    """Return (node_key, port, decision, candidate) for a decision/v1 candidate in a run."""
    for node_key, node in sorted((run.result_manifest or {}).get("nodes", {}).items()):
        for port, value in sorted((node.get("outputs") or {}).items()):
            if is_decision(value):
                for row in value["candidates"]:
                    if row["id"] == candidate_id:
                        return node_key, port, value, row
    return None


def compile_decision_candidate(db, ontology_id: str, scenario_id: str, snapshot, candidate: dict, actor) -> list[dict]:
    """Compile a candidate through the ontology's published scenario Action types.

    The candidate names Action types by name; their rules, not domain code,
    decide the edits.  Actions that declare an external effect are delivered by
    the Decision Package outbox, so they are compiled with delivery enabled.
    """
    from app.models.v2.action import OntologyActionType
    from app.services.v2.function_binding import function_resolver
    from app.services.v2.scenario_actions import compile_actions
    from app.services.v2.scenarios import ScenarioError

    if not candidate["applicable"]:
        codes = ", ".join(row["code"] for row in candidate["hard_constraints"])
        raise DecisionOutputError("hard_constraint_failed", f"Candidate violates hard constraints: {codes}")
    if not candidate["actions"]:
        raise DecisionOutputError("no_actions", "Candidate has no actions to apply")
    types, invocations = {}, []
    for action in candidate["actions"]:
        rows = db.query(OntologyActionType).filter_by(ontology_id=ontology_id, name=action["action_type"], action_category="scenario",
                                                      status="published", enabled=True).all()
        if len(rows) != 1:
            raise DecisionOutputError("action_type_unavailable", f"Publish exactly one scenario Action named {action['action_type']}")
        types[rows[0].id] = rows[0]
        invocations.append({"action_type_id": rows[0].id, "parameters": action["parameters"]})
    try:
        edits = compile_actions(ontology_id, scenario_id, invocations, types, snapshot, actor=actor,
                                function_resolver=function_resolver(db, ontology_id), deliver_side_effects=True)
    except ScenarioError as exc:
        raise DecisionOutputError(exc.code, exc.message) from exc
    groups: list[list[dict]] = []
    for edit in (row.model_dump(mode="json") for row in edits):
        if edit["op"] == "invoke_action":
            groups.append([])
        groups[-1].append(edit)
    compiled = []
    for action, invocation, group in zip(candidate["actions"], invocations, groups):
        action_type = types[invocation["action_type_id"]]
        external = (action_type.side_effects or {}).get("external")
        invocation = {**action, "action_type_version": action_type.version}
        if external and external.get("connection_id"):
            invocation["_external_delivery"] = {"connection_id": external["connection_id"], "resource": external["resource"],
                                                "kind": external["kind"]}
        compiled.append({"action_type_id": action_type.name, "invocation": invocation,
                         "expected_edits": group, "external_effect_class": "irreversible" if external else "none"})
    return compiled
