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


def test_solver_action_runs_inside_scenario_duplicates_and_previews_merge(client, auth_headers, db, plant):
    ontology, _, snapshot = plant
    api = f"/api/v2/ontologies/{ontology.id}"
    view = client.post(f"{api}/object-query/data-views", headers=auth_headers,
                       json={"source_ontology_id": ontology.id, "source_manifest_digest": "frepple-demo"})
    assert view.status_code == 201, view.text
    base_view = view.json()["view_id"]
    kind = client.post(f"{api}/scenario-action-types", headers=auth_headers,
                       json={"name": "Assign operators (scenario)", "backed_by_function": f"{CONTRACT['asset_key']}@{CONTRACT['version']}"}).json()
    assert client.post(f"{api}/scenario-action-types/{kind['id']}:publish", headers=auth_headers).json()["status"] == "published"
    mixed = client.post(f"{api}/scenario-action-types", headers=auth_headers, json={
        "name": "mixed", "backed_by_function": "operator_assignment@1.0.0",
        "rules": [{"op": "set_property", "target_parameter": "t", "property": "x", "static_value": 1}]})
    assert mixed.status_code == 422
    scenario = client.post(f"{api}/scenarios", headers=auth_headers, json={"name": "operator plan", "base_view_id": base_view}).json()
    parameters = {"demands": {**handle(snapshot, "Demand"), "data_view_id": base_view},
                  "operators": {**handle(snapshot, "Operator"), "data_view_id": base_view}}
    command = {"expected_etag": scenario["etag"], "client_request_id": "plan-1", "actions": [{"action_type_id": kind["id"], "parameters": parameters}]}
    preview = client.post(f"{api}/scenarios/{scenario['id']}/actions:preview", headers=auth_headers, json=command)
    assert preview.status_code == 200, preview.text
    assert preview.json()["edit_count"] == 16 and preview.json()["edits"][0]["parameters"]["function"]["solver"]["objective"] == 80.0
    submitted = client.put(f"{api}/scenarios/{scenario['id']}/actions", headers=auth_headers, json=command)
    assert submitted.status_code == 200, submitted.text

    def assignments(view_id):
        body = client.post(f"{api}/object-query/load", headers=auth_headers, json={
            "expression": {"kind": "base", "type_ref": {"api_name": "Demand"}},
            "context": {"ontology_id": ontology.id, "consistency": "snapshot", "data_view_id": view_id}, "read": {"page_size": 50}}).json()
        return {o["object_id"]: o["properties"].get("assigned_operator") for o in body["objects"]}
    scenario_plan = assignments(submitted.json()["result_view_id"])
    assert len(scenario_plan) == 16 and all(scenario_plan.values())
    assert set(assignments(base_view).values()) == {None}  # the base view and live data stay untouched
    db.expire_all()
    assert all(row.row_data.get("assigned_operator") is None for row in db.query(EntityInstance).filter_by(ontology_id=ontology.id).all())

    infeasible = {**command, "client_request_id": "plan-2", "expected_etag": client.get(f"{api}/scenarios/{scenario['id']}", headers=auth_headers).json()["etag"],
                  "actions": [{"action_type_id": kind["id"], "parameters": {**parameters, "operators": {
                      **handle(snapshot, "Operator", exclude=("Antonio",)), "data_view_id": base_view}}}]}
    r = client.put(f"{api}/scenarios/{scenario['id']}/actions", headers=auth_headers, json=infeasible)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "solver_infeasible"
    assert client.get(f"{api}/scenarios/{scenario['id']}", headers=auth_headers).json()["head_revision"] == 1

    copy = client.post(f"{api}/scenarios/{scenario['id']}:duplicate", headers=auth_headers, json={"name": "copy", "base_view_id": base_view})
    assert copy.status_code == 201, copy.text
    revision = client.get(f"{api}/scenarios/{copy.json()['id']}/revisions/1", headers=auth_headers)
    assert revision.status_code == 200, revision.text
    assert assignments(revision.json()["result_view_id"]) == scenario_plan  # same release + same base -> same plan
    merge = client.post(f"{api}/scenarios/{scenario['id']}:merge-preview", headers=auth_headers).json()
    assert merge["can_merge"] is True and merge["applied_edits"] == 16
