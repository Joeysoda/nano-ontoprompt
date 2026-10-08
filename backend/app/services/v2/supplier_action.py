"""Server-owned typed edits for the single supported supplier replacement Action."""
from __future__ import annotations

from app.schemas.v2.scenario import Edit, Target, LinkTarget, RevisionRequest
from app.services.v2.object_query.normalize import stable_hash
from app.services.v2.supplier_resilience import WOOD, validate_profile


def compile_supplier_action(ontology_id, scenario, snapshot, profile, request_id):
    validate_profile(profile)
    if profile["supplier"] == "wood supplier":
        raise ValueError("candidate supplier must replace the fixture supplier")
    objects = snapshot.objects

    def find(kind, **fields):
        matches = [identity for identity, props in objects.items()
                   if identity[0] == kind and all(props.get(key) == value for key, value in fields.items())]
        if len(matches) != 1:
            raise ValueError(f"expected one {kind} with {fields}, found {len(matches)}")
        return Target(ontology_id=ontology_id, concrete_type=kind, object_id=matches[0][1])

    supplier_id = f"synthetic:{stable_hash({'scenario': scenario.id, 'supplier': profile['supplier']})[:24]}"
    supplier = Target(ontology_id=ontology_id, concrete_type="Supplier", object_id=supplier_id)
    edits = []

    def add(**fields):
        edits.append(Edit(sequence=len(edits), source_action="replace_wood_supplier_v1", **fields))

    add(op="invoke_action", action_key="replace_wood_supplier_v1", parameters=profile)
    add(op="create_object", target=supplier, object_type="Supplier",
        properties={"name": profile["supplier"], "source_kind": "synthetic_demo_extension", "active": True})
    for item_name in WOOD:
        item = find("Item", name=item_name)
        original = find("SupplyOption", item_id=item_name, supplier_id="wood supplier")
        add(op="set_property", target=original, property="active", value=False)
        option = Target(ontology_id=ontology_id, concrete_type="SupplyOption",
                        object_id=f"synthetic:{stable_hash({'scenario': scenario.id, 'item': item_name})[:24]}")
        source_props = objects[(original.concrete_type, original.object_id)]
        location_name = source_props.get("location_id")
        props = {"name": f"{profile['supplier']} / {item_name}", "supplier_id": profile["supplier"],
                 "item_id": item_name, "location_id": location_name, "active": True,
                 "leadtime": float(profile["lead_days"][item_name]) * 86400,
                 "sizeminimum": profile["minimum_order"][item_name],
                 "sizemultiple": profile["order_multiple"][item_name],
                 "capacity": profile["capacity"][item_name],
                 "synthetic_cost_multiplier": profile["cost_multiplier"],
                 "source_kind": "synthetic_demo_extension"}
        add(op="create_object", target=option, object_type="SupplyOption", properties=props)
        for relation, target in (("HAS_SUPPLIER", supplier), ("HAS_ITEM", item)):
            add(op="add_link", link=LinkTarget(ontology_id=ontology_id, relation_type=relation, source=option, target=target))
        if location_name:
            location = find("Location", name=location_name)
            add(op="add_link", link=LinkTarget(ontology_id=ontology_id, relation_type="HAS_LOCATION", source=option, target=location))
    return RevisionRequest(base_revision=scenario.head_revision, expected_etag=scenario.etag,
                           client_request_id=request_id, edits=edits)
