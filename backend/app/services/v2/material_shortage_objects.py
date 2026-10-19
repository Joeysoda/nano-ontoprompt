"""Ontology object layer for the material-shortage decision.

The Study reads these object types through Object Query; an approved Decision
Package writes back through typed Scenario edits.  Each candidate action is
compiled to edits of exactly the inputs the model reads, so re-running the
Study after a merge reproduces the candidate's predicted outcome.
"""
from __future__ import annotations

from app.schemas.v2.scenario import Edit, Target
from app.services.v2.object_query.normalize import stable_hash


OBJECT_TYPES = {
    "CustomerOrder": {"item": "string", "quantity": "double", "due_at": "double", "priority": "double", "unit_revenue": "double",
                      "processing_days": "double", "resource": "string", "alternate_resources": "array", "allocation_rank": "double"},
    "BOMLine": {"parent_item": "string", "component_item": "string", "quantity_per": "double"},
    "InventoryLot": {"item": "string", "location": "string", "quantity": "double"},
    "Reservation": {"item": "string", "demand_id": "string", "quantity": "double", "status": "string"},
    "SupplyCommitment": {"supplier": "string", "item": "string", "quantity": "double", "available_at": "double", "expires_at": "string",
                         "status": "string", "unit_cost": "double", "expedite_available_at": "double",
                         "expedite_unit_premium": "double", "expedited": "boolean", "source_option_id": "string"},
    "SupplyOption": {"supplier": "string", "item": "string", "quantity": "double", "lead_time": "double", "unit_cost": "double", "qualified": "boolean"},
    "FixedTask": {"resource": "string", "start": "double", "finish": "double"},
}
# Study input key -> object type, in template order.
INPUT_TYPES = {"demands": "CustomerOrder", "boms": "BOMLine", "inventory": "InventoryLot", "reservations": "Reservation",
               "supply_commitments": "SupplyCommitment", "supply_options": "SupplyOption", "fixed_tasks": "FixedTask"}
# Actions that notify a supplier cannot be undone by reverting Ontology edits.
EXTERNAL_ACTIONS = {"expedite_supply_commitment", "create_purchase_order"}


def fixture_objects(fixture: dict) -> list[dict]:
    """Turn a fixture into graph instances ({id, entity_type, properties})."""
    nodes = []
    for key, kind in INPUT_TYPES.items():
        for index, row in enumerate(fixture.get(key, [])):
            object_id = row.get("id") or (f"BOM-{row['parent_item']}-{row['component_item']}" if kind == "BOMLine" else f"{kind}-{index}")
            props = {name: value for name, value in row.items() if name != "id" and value is not None}
            unknown = set(props) - set(OBJECT_TYPES[kind])
            if unknown:
                raise ValueError(f"{kind} fixture has undeclared properties {sorted(unknown)}")
            nodes.append({"id": object_id, "entity_type": kind, "properties": props})
    return nodes


def seed_material_shortage_ontology(db, graph_service, ontology_id: str, fixture: dict) -> list[dict]:
    """Create object types, SQL instances and their graph projection."""
    from app.models.entity import Entity
    from app.models.entity_instance import EntityInstance

    entities = {}
    for name, props in OBJECT_TYPES.items():
        entity = db.query(Entity).filter_by(ontology_id=ontology_id, name_en=name).first()
        if not entity:
            entity = Entity(ontology_id=ontology_id, name_cn=name, name_en=name, canonical_id=f"ontology:{name}", type="EntityType",
                            properties={"property_definitions": [{"id": key, "type": kind} for key, kind in props.items()]})
            db.add(entity)
            db.flush()
        entities[name] = entity
    nodes = fixture_objects(fixture)
    for node in nodes:
        db.add(EntityInstance(id=node["id"], entity_id=entities[node["entity_type"]].id, ontology_id=ontology_id,
                              row_identity=node["id"], row_data=dict(node["properties"])))
    db.commit()
    graph_service.upsert_instances(ontology_id, nodes)
    return nodes


class CompileError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def compile_candidate(ontology_id: str, snapshot, candidate: dict, *, run_id: str) -> list[dict]:
    """Compile one evaluated candidate into ordered package actions with typed edits.

    Values come from the pinned snapshot, not from the client or the candidate
    payload, and every property edit carries the value it expects to replace.
    """
    if not candidate.get("applicable"):
        codes = ", ".join(row["code"] for row in candidate.get("hard_constraints", []))
        raise CompileError("hard_constraint_failed", f"Candidate violates hard constraints: {codes}")
    objects = snapshot.objects
    sequence = 0

    def props(kind, object_id):
        if (kind, object_id) not in objects:
            raise CompileError("object_not_found", f"{kind} {object_id} is not in the pinned data view")
        return objects[(kind, object_id)]

    def target(kind, object_id):
        return Target(ontology_id=ontology_id, concrete_type=kind, object_id=object_id)

    def edit(**fields):
        nonlocal sequence
        row = Edit(sequence=sequence, source_action=f"material_shortage:{candidate['id']}", **fields)
        sequence += 1
        return row.model_dump(mode="json")

    def set_prop(kind, object_id, name, value):
        return edit(op="set_property", target=target(kind, object_id), property=name, value=value,
                    expected_old_value=props(kind, object_id).get(name))

    compiled = []
    for action in candidate["actions"]:
        kind = action["action_type"]
        edits = []
        if kind == "expedite_supply_commitment":
            current = props("SupplyCommitment", action["commitment_id"])
            if current.get("expedite_available_at") is None:
                raise CompileError("not_expeditable", f"{action['commitment_id']} has no expedite option")
            edits = [set_prop("SupplyCommitment", action["commitment_id"], "available_at", current["expedite_available_at"]),
                     set_prop("SupplyCommitment", action["commitment_id"], "expedited", True)]
        elif kind == "create_purchase_order":
            option = props("SupplyOption", action["option_id"])
            quantity = float(action.get("quantity") or 0)
            if quantity <= 0 or quantity > float(option.get("quantity", 0)) + 1e-9:
                raise CompileError("invalid_quantity", f"Purchase quantity for {action['option_id']} is outside the option capacity")
            if option.get("qualified") is False:
                raise CompileError("hard_constraint_failed", f"{action['option_id']} supplier is not qualified")
            commitment_id = f"PO-ALT-{stable_hash({'run': run_id, 'option': action['option_id']})[:10]}"
            edits = [edit(op="create_object", target=target("SupplyCommitment", commitment_id), object_type="SupplyCommitment",
                          properties={"supplier": option["supplier"], "item": option["item"], "quantity": quantity,
                                      "available_at": option.get("lead_time", 0), "status": "active",
                                      "unit_cost": option.get("unit_cost", 0), "source_option_id": action["option_id"]}),
                     set_prop("SupplyOption", action["option_id"], "quantity", float(option["quantity"]) - quantity)]
        elif kind == "reassign_work_order":
            props("CustomerOrder", action["demand_id"])
            edits = [set_prop("CustomerOrder", action["demand_id"], "resource", action["to_resource"]),
                     set_prop("CustomerOrder", action["demand_id"], "alternate_resources", [action["from_resource"]])]
        elif kind == "reprioritize_allocation":
            edits = [set_prop("CustomerOrder", demand_id, "allocation_rank", float(rank))
                     for rank, demand_id in enumerate(action["ranked_demands"], start=1)]
            edits += [set_prop("Reservation", reservation_id, "status", "released") for reservation_id in action["released_reservations"]]
        elif kind == "reschedule_work_orders":
            edits = []  # policy marker; the concrete moves are separate reassign actions
        else:
            raise CompileError("unsupported_action", f"{kind} cannot be applied as Ontology edits")
        compiled.append({"action_type_id": kind, "invocation": action, "expected_edits": edits,
                         "external_effect_class": "irreversible" if kind in EXTERNAL_ACTIONS else "none"})
    return compiled
