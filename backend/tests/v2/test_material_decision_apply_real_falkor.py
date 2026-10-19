"""Decision Package Apply/Revert on real FalkorDB.

Ontology objects -> pinned data view -> object-backed Study run -> package ->
review -> Scenario merge -> outbox receipts -> re-run on live data -> revert.
The re-run after Apply must reproduce the candidate's predicted outcome.
"""
import uuid
from time import monotonic

import pytest

from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.v2.logic_asset import LogicAsset
from app.models.v2.query_view import QueryDataView
from app.models.v2.scenario import ScenarioRun, ScenarioRunStage
from app.models.v2.scenario_study import RunPlan
from app.services.auth_service import hash_password
from app.services.v2 import decision_packages
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.l3harris_fixture import l3harris_material_shortage_fixture
from app.services.v2.logic_assets import ASSET_DEFINITIONS
from app.services.v2.material_shortage_objects import seed_material_shortage_ontology
from app.services.v2.scenario_study_templates import templates
from app.tasks.v2.scenario import _execute_study_run


@pytest.fixture
def plant(client, auth_headers, db, admin_user):
    graph = FalkorDBService()
    if not graph.available:
        pytest.fail("Real FalkorDB is required for Decision Package apply acceptance")
    ontology = OntologyProject(id=f"l3-{uuid.uuid4().hex[:12]}", name="L3 shortage", domain="manufacturing", created_by=admin_user.id)
    db.add(ontology)
    definition = next(row for row in ASSET_DEFINITIONS if row["asset_key"] == "material_shortage_decision_v1")
    db.add(LogicAsset(ontology_id=ontology.id, asset_key=definition["asset_key"], name=definition["name"], kind=definition["kind"],
                      implementation=definition["implementation"], version="1.0.0", input_schema=definition["input_schema"],
                      output_schema=definition["output_schema"], bindings=definition["bindings"], status="published"))
    db.commit()
    # The test ontology is alone in its database, so ids stay readable.
    seed_material_shortage_ontology(db, graph, ontology.id, l3harris_material_shortage_fixture(), id_prefix="")
    api = f"/api/v2/ontologies/{ontology.id}"
    spec = next(row for row in templates() if row["template_id"] == "material_shortage_decision_objects")["spec"]
    study = client.post(f"{api}/scenario-study-definitions", headers=auth_headers, json={"name": "L3 objects", "draft_spec": spec}).json()
    published = client.post(f"{api}/scenario-study-definitions/{study['id']}:publish", headers=auth_headers, json={"expected_etag": study["etag"]})
    assert published.status_code == 201, published.text
    reviewer = User(id=str(uuid.uuid4()), username="reviewer", email="reviewer@test.com", password_hash=hash_password("review123"), role="admin")
    db.add(reviewer)
    db.commit()
    token = client.post("/api/v1/auth/login", json={"username": "reviewer", "password": "review123"}).json()["data"]["access_token"]
    connector = decision_packages.FakeProcurementConnector()
    original = decision_packages.procurement_connector
    decision_packages.procurement_connector = connector
    try:
        yield {"ontology": ontology, "api": api, "study": study, "reviewer": {"Authorization": f"Bearer {token}"}, "connector": connector}
    finally:
        decision_packages.procurement_connector = original
        for view in db.query(QueryDataView).filter_by(ontology_id=ontology.id).all():
            graph.delete_graph(view.graph_key)
        graph.delete_graph(ontology.id)


def run_on_live(client, headers, db, admin_user, plant, name):
    """Pin a fresh view of live data, open a Scenario on it and run the Study."""
    api, study = plant["api"], plant["study"]
    view = client.post(f"{api}/object-query/data-views", headers=headers,
                       json={"source_ontology_id": plant["ontology"].id, "source_manifest_digest": f"l3-{name}"})
    assert view.status_code == 201, view.text
    scenario = client.post(f"{api}/scenarios", headers=headers, json={"name": name, "base_view_id": view.json()["view_id"]}).json()
    case = client.post(f"{api}/scenario-study-definitions/{study['id']}/cases", headers=headers,
                       json={"study_version": 1, "scenario_id": scenario["id"], "name": name,
                             "case_kind": "baseline" if name == "baseline" else "candidate"})
    assert case.status_code == 201, case.text
    case = case.json()
    compiled = client.post(f"{api}/scenario-study-definitions/{study['id']}/cases/{case['id']}:compile", headers=headers)
    assert compiled.status_code == 201, compiled.text
    plan = db.get(RunPlan, compiled.json()["id"])
    run = ScenarioRun(scenario_id=scenario["id"], revision=0, case_key=name, logic_asset_key=f"study:{study['id']}",
                      logic_version="scenario-study/v1", parameters={"run_plan_id": plan.id}, status="queued", progress=0,
                      current_stage="queued", input_digest=plan.plan_hash, study_id=study["id"], case_id=case["id"],
                      run_plan_id=plan.id, created_by=admin_user.id)
    db.add(run)
    db.flush()
    for index, node in enumerate(plan.plan_json["nodes"]):
        db.add(ScenarioRunStage(run_id=run.id, sequence=index + 1, stage_key=f"node_{index:03d}",
                                summary_json={"node_key": node["key"], "kind": node["kind"]}))
    db.commit()
    db.refresh(run)
    _execute_study_run(db, run, monotonic())
    db.refresh(run)
    assert run.status == "completed", run.error_json
    return scenario, run, run.result_manifest["nodes"]["decision"]["outputs"]


def create_package(client, headers, plant, run, candidate_id):
    return client.post(f"{plant['api']}/scenario-study-runs/{run.id}/decision-packages", headers=headers,
                       json={"candidate_id": candidate_id, "rationale": "Protect committed revenue."})


def approve(client, author, plant, package):
    packages = f"{plant['api']}/decision-packages/{package['id']}"
    submitted = client.post(f"{packages}/transition", headers=author,
                            json={"event": "submit", "expected_etag": package["etag"], "request_id": uuid.uuid4().hex}).json()
    approved = client.post(f"{packages}/transition", headers=plant["reviewer"],
                           json={"event": "approve", "expected_etag": submitted["etag"], "request_id": uuid.uuid4().hex})
    assert approved.status_code == 200, approved.text
    return approved.json()


def live(db, object_id):
    db.expire_all()
    row = db.get(EntityInstance, object_id)
    return dict(row.row_data) if row else None


def test_applied_package_matches_prediction_and_reverts(client, auth_headers, db, admin_user, plant):
    _, run, outputs = run_on_live(client, auth_headers, db, admin_user, plant, "baseline")
    assert outputs["revenue_at_risk"] == 550000
    by_id = {row["id"]: row for row in outputs["candidate_actions"]}

    blocked = create_package(client, auth_headers, plant, run, "qualify_alternate:ALT-UNQUALIFIED")
    assert blocked.status_code == 422 and blocked.json()["detail"]["code"] == "hard_constraint_failed"

    created = create_package(client, auth_headers, plant, run, "combined:expedite+alternate+replan")
    assert created.status_code == 200, created.text
    package = created.json()
    edits = [edit for action in package["actions"] for edit in action["expected_edits"]]
    assert {(edit["op"], (edit.get("target") or {}).get("object_id"), edit.get("property")) for edit in edits} >= {
        ("set_property", "PO-LATE", "available_at"), ("set_property", "ALT-QUALIFIED", "quantity"),
        ("set_property", "ORD-1004", "resource")}
    assert [a["external_effect_class"] for a in package["actions"]].count("irreversible") == 2

    package = approve(client, auth_headers, plant, package)
    apply_url = f"{plant['api']}/decision-packages/{package['id']}:apply"
    applied = client.post(apply_url, headers=plant["reviewer"], json={"expected_etag": package["etag"], "request_id": "apply-1"})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["package"]["status"] == "applied" and body["package"]["applied_decision_id"]
    assert body["delivery"] == {"external_effects": 2, "delivered": 2, "failed": [], "status": "complete"}
    assert all(a["receipt"]["connector"] == "fake-erp" for a in body["package"]["actions"] if a["external_effect_class"] == "irreversible")
    replay = client.post(apply_url, headers=plant["reviewer"], json={"expected_etag": package["etag"], "request_id": "apply-1"})
    assert replay.status_code == 200 and len(plant["connector"].sent) == 2

    assert live(db, "PO-LATE")["available_at"] == 4 and live(db, "PO-LATE")["expedited"] is True
    assert live(db, "ORD-1004")["resource"] == "assembly-1"
    assert live(db, "ALT-QUALIFIED")["quantity"] == 0
    created_po = next(edit["target"]["object_id"] for edit in edits if edit["op"] == "create_object")
    assert live(db, created_po)["quantity"] == 4

    # The decision's prediction must be what actually happens on the merged live data.
    _, _, after = run_on_live(client, auth_headers, db, admin_user, plant, "after-apply")
    assert after["revenue_at_risk"] == by_id["combined:expedite+alternate+replan"]["revenue_at_risk"] == 0
    assert after["feasible"] is True

    reverted = client.post(f"{plant['api']}/decision-packages/{package['id']}:revert", headers=plant["reviewer"],
                           json={"expected_etag": body["package"]["etag"], "request_id": "revert-1"})
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["package"]["status"] == "reverted"
    assert [row["delivered"] for row in reverted.json()["irreversible_effects"]] == [True, True]
    assert live(db, "PO-LATE")["available_at"] == 9 and "expedited" not in live(db, "PO-LATE")
    assert live(db, "ORD-1004")["resource"] == "assembly-2" and live(db, created_po) is None
    _, _, restored = run_on_live(client, auth_headers, db, admin_user, plant, "after-revert")
    assert restored["revenue_at_risk"] == 550000


def test_reallocation_is_applied_as_persisted_allocation_rank(client, auth_headers, db, admin_user, plant):
    _, run, outputs = run_on_live(client, auth_headers, db, admin_user, plant, "baseline")
    predicted = next(row for row in outputs["candidate_actions"] if row["id"] == "reallocate:CHIP-X")
    package = approve(client, auth_headers, plant, create_package(client, auth_headers, plant, run, "reallocate:CHIP-X").json())
    applied = client.post(f"{plant['api']}/decision-packages/{package['id']}:apply", headers=auth_headers,
                          json={"expected_etag": package["etag"], "request_id": "apply-rank"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["delivery"]["external_effects"] == 0
    assert live(db, "RSV-1002-CHIP")["status"] == "released"
    _, _, after = run_on_live(client, auth_headers, db, admin_user, plant, "after-rank")
    assert after["revenue_at_risk"] == predicted["revenue_at_risk"] == 420000
    impacts = {row["demand_id"]: row["status"] for row in after["demand_impacts"]}
    assert impacts["ORD-1003"] != "on_time" and impacts["ORD-1010"] == "on_time"


def test_live_drift_after_review_fails_apply_without_writing(client, auth_headers, db, admin_user, plant):
    _, run, _ = run_on_live(client, auth_headers, db, admin_user, plant, "baseline")
    package = approve(client, auth_headers, plant, create_package(client, auth_headers, plant, run, "expedite:PO-LATE").json())
    # Someone changes the commitment in live data after the package was reviewed.
    row = db.get(EntityInstance, "PO-LATE")
    row.row_data = {**row.row_data, "available_at": 7}
    db.commit()
    FalkorDBService()._graph(plant["ontology"].id).query(
        "MATCH (n:Instance {_instance_id: 'PO-LATE', _ontology_id: $o}) SET n.available_at = 7", params={"o": plant["ontology"].id})
    failed = client.post(f"{plant['api']}/decision-packages/{package['id']}:apply", headers=auth_headers,
                         json={"expected_etag": package["etag"], "request_id": "apply-drift"})
    assert failed.status_code == 409 and failed.json()["detail"]["code"] == "merge_conflict"
    assert live(db, "PO-LATE")["available_at"] == 7 and "expedited" not in live(db, "PO-LATE")
    listed = client.get(f"{plant['api']}/decision-packages", headers=auth_headers).json()["packages"][0]
    assert listed["status"] == "apply_failed" and listed["events"][-1]["event_type"] == "apply_failed"
    assert plant["connector"].sent == {}


def test_partial_external_delivery_is_reported_and_retried(client, auth_headers, db, admin_user, plant):
    _, run, _ = run_on_live(client, auth_headers, db, admin_user, plant, "baseline")
    package = approve(client, auth_headers, plant, create_package(client, auth_headers, plant, run, "combined:expedite+alternate+replan").json())
    from app.models.v2.decision import DecisionOutbox

    class FlakyOnce(decision_packages.FakeProcurementConnector):
        def send(self, key, payload):
            if payload["action_type"] == "create_purchase_order" and not self.fail_keys:
                self.fail_keys.add(key)
            return super().send(key, payload)

    flaky = FlakyOnce()
    decision_packages.procurement_connector = flaky
    applied = client.post(f"{plant['api']}/decision-packages/{package['id']}:apply", headers=auth_headers,
                          json={"expected_etag": package["etag"], "request_id": "apply-flaky"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["package"]["status"] == "applied"
    assert applied.json()["delivery"]["status"] == "external_effect_partial" and applied.json()["delivery"]["delivered"] == 1
    flaky.fail_keys.clear()
    flaky.fail_keys.add("never")
    retried = client.post(f"{plant['api']}/decision-packages/{package['id']}:deliver-outbox", headers=auth_headers).json()
    assert retried["delivery"]["status"] == "complete"
    db.expire_all()
    assert sorted(row.attempts for row in db.query(DecisionOutbox).all()) == [1, 2]
