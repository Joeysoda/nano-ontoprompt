"""Solver Function -> function-backed Action -> live commit -> revert, on real FalkorDB."""
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.models.v2.action import OntologyActionRun, OntologyActionType
from app.models.v2.logic_asset import LogicAsset
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.solver_functions import CONTRACT

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_solver_functions import frepple_problem  # noqa: E402


@pytest.fixture
def plant(db, admin_user):
    graph = FalkorDBService()
    if not graph.available:
        pytest.fail("Real FalkorDB is required for solver Action acceptance")
    ontology = OntologyProject(id=f"solver-{uuid4().hex[:12]}", name="frePPLe operators", domain="manufacturing", created_by=admin_user.id)
    db.add(ontology); db.flush()
    types = {"Demand": [("item", "string"), ("quantity", "double"), ("hours", "double"), ("required_skill", "string"), ("assigned_operator", "string")],
             "Operator": [("skills", "array"), ("capacity_hours", "double")]}
    entities = {}
    for name, props in types.items():
        entity = Entity(ontology_id=ontology.id, name_cn=name, name_en=name, canonical_id=f"ontology:{name}", type="EntityType",
                        properties={"property_definitions": [{"id": p, "type": t} for p, t in props]})
        db.add(entity); db.flush()
        entities[name] = entity
    snapshot = frepple_problem(capacity_hours=120.0)
    nodes = []
    for (kind, object_id), props in snapshot.objects.items():
        stored = {k: v for k, v in props.items() if v is not None}
        nodes.append({"id": object_id, "entity_type": kind, "properties": stored})
        db.add(EntityInstance(id=object_id, entity_id=entities[kind].id, ontology_id=ontology.id, row_identity=object_id, row_data=dict(stored)))
    db.add(LogicAsset(ontology_id=ontology.id, asset_key=CONTRACT["asset_key"], name="Operator assignment", kind="edit",
                      implementation=CONTRACT["implementation"], version=CONTRACT["version"], interface_key="operator_assignment",
                      input_schema=CONTRACT["input_schema"], output_schema=CONTRACT["output_schema"], config=CONTRACT["config"],
                      status="published", side_effect=False))
    action = OntologyActionType(ontology_id=ontology.id, name="Assign operators", action_category="update", status="published", enabled=True,
                                parameters=[], effects=[], backed_by_function=f"{CONTRACT['asset_key']}@{CONTRACT['version']}")
    db.add(action); db.commit()
    graph.upsert_instances(ontology.id, nodes)
    try:
        yield ontology, action, snapshot
    finally:
        graph.delete_graph(ontology.id)


def handle(snapshot, kind, exclude=()):
    return {"kind": "set_handle", "completeness": "complete", "consistency": "snapshot", "data_view_id": "live-pin",
            "objects": [{"object_type": t, "object_id": i} for t, i in snapshot.objects if t == kind and i not in exclude]}


def test_solver_action_previews_commits_and_reverts(client, auth_headers, db, plant):
    ontology, action, snapshot = plant
    url = f"/api/v2/ontologies/{ontology.id}/actions/{action.id}"
    body = {"parameters": {"demands": handle(snapshot, "Demand"), "operators": handle(snapshot, "Operator"), "time_limit_seconds": 5},
            "context": {"ontology_id": ontology.id, "consistency": "live"}}
    preview = client.post(f"{url}/preview", headers=auth_headers, json=body)
    assert preview.status_code == 200, preview.text
    edits = preview.json()["edits"]
    solver = edits[0]["parameters"]["function"]["solver"]
    assert solver["status"] == "optimal" and solver["objective"] == 80.0 and preview.json()["edit_count"] == 16
    planned = {e["target"]["object_id"]: e["value"] for e in edits[1:]}
    assert db.get(EntityInstance, "Demand 14").row_data.get("assigned_operator") is None  # preview never writes

    run = client.post(f"{url}/run", headers=auth_headers, json=body)
    assert run.status_code == 200, run.text
    db.expire_all()
    assert {i: db.get(EntityInstance, i).row_data["assigned_operator"] for i in planned} == planned
    graph = FalkorDBService()._graph(ontology.id)
    stored = graph.ro_query("MATCH (n:Instance {_ontology_id: $o, _type: 'Demand'}) RETURN n._instance_id, n.assigned_operator",
                            params={"o": ontology.id}).result_set
    assert dict(stored) == planned
    assert all(planned[i] == "Antonio" for (t, i), p in snapshot.objects.items() if t == "Demand" and p["required_skill"])

    revert = client.post(f"/api/v2/ontologies/{ontology.id}/action-runs/{run.json()['run_id']}:revert", headers=auth_headers)
    assert revert.status_code == 200, revert.text
    db.expire_all()
    assert all(db.get(EntityInstance, i).row_data.get("assigned_operator") is None for i in planned)


def test_infeasible_plan_fails_with_stable_code_and_no_write(client, auth_headers, db, plant):
    ontology, action, snapshot = plant
    body = {"parameters": {"demands": handle(snapshot, "Demand"), "operators": handle(snapshot, "Operator", exclude=("Antonio",))},
            "context": {"ontology_id": ontology.id, "consistency": "live"}}
    r = client.post(f"/api/v2/ontologies/{ontology.id}/actions/{action.id}/run", headers=auth_headers, json=body)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "solver_infeasible"
    db.expire_all()
    assert all(row.row_data.get("assigned_operator") is None for row in db.query(EntityInstance).filter_by(ontology_id=ontology.id).all())
    failed = db.query(OntologyActionRun).filter_by(ontology_id=ontology.id).one()
    assert failed.status == "failed" and "infeasible" in failed.error
