"""Compile user-authored process graphs into ordinary governed Action types."""
from datetime import datetime, timezone

from app.models.v2.action import OntologyActionType
from app.schemas.v2.process_model import ProcessDefinition
from app.services.v2.scenario_actions import validate_definition


class ProcessError(Exception):
    def __init__(self, code, message, status=422, path="definition"):
        self.code, self.message, self.status, self.path = code, message, status, path
        super().__init__(message)


def validate_metadata(metadata, entity_type: str, state_property: str):
    try:
        typ = metadata.resolve_type("object", entity_type, "entity_type_name")
        prop = metadata.resolve_property(typ, state_property, "state_property")
    except Exception as exc:
        raise ProcessError("metadata_incompatible", "Process object type or state property is not published",
                           path="state_property") from exc
    if prop.data_type not in {"string", "text"}:
        raise ProcessError("invalid_state_property", "Process state property must be text", path="state_property")


def action_contract(machine, transition) -> dict:
    parameters = [{"name": "target", "label": machine.entity_type_name, "type": "object",
                   "object_type": machine.entity_type_name, "required": True}]
    effects = [{"op": "set_property", "target_parameter": "target", "property": machine.state_property,
                "static_value": transition.to_state}]
    validate_definition(parameters, effects)
    return {
        "name": f"{machine.name} · {transition.label}",
        "description": f"{transition.from_state} → {transition.to_state}",
        "target_entity_type": machine.entity_type_name,
        "action_category": "state_transition",
        "parameters": parameters,
        "submission_criteria": [{"type": "field_equals", "field": machine.state_property,
                                  "value": transition.from_state}],
        "effects": effects,
        "permission_rules": transition.permission_rules,
    }


def publish(db, machine, actor_id: str):
    definition = ProcessDefinition.model_validate({"states": machine.states, "transitions": machine.transitions})
    current = {row.transition_key: row for row in db.query(OntologyActionType).filter_by(
        ontology_id=machine.ontology_id, state_machine_id=machine.id).all()}
    active = set()
    for transition in definition.transitions:
        active.add(transition.key)
        values = action_contract(machine, transition)
        row = current.get(transition.key)
        if row is None:
            row = OntologyActionType(ontology_id=machine.ontology_id, state_machine_id=machine.id,
                                     transition_key=transition.key, created_by=actor_id, version=1, **values)
            db.add(row)
        else:
            changed = any(getattr(row, key) != value for key, value in values.items())
            for key, value in values.items(): setattr(row, key, value)
            row.version += int(changed)
        row.status, row.enabled, row.updated_at = "published", True, datetime.now(timezone.utc)
    for key, row in current.items():
        if key not in active:
            row.enabled, row.status, row.updated_at = False, "disabled", datetime.now(timezone.utc)
    machine.version += 1
    machine.status, machine.etag, machine.updated_at = "published", machine.etag + 1, datetime.now(timezone.utc)
    db.commit()
    return db.query(OntologyActionType).filter_by(ontology_id=machine.ontology_id, state_machine_id=machine.id).all()
