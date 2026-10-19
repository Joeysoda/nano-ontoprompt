"""A new user builds the Heineken case from an empty ontology, through the API only.

No repository code is specific to this case: the data is imported through
mappings, the decision logic is a user Function (examples/heineken, sent as
text and executed by the isolated runner), and the write-back is two
user-configured scenario Actions.  Needs real FalkorDB and FUNCTION_RUNNER_URL.

Hand-derived answers (one truck pickup per 7 days, weekly buckets):
* Baseline FIFO pickup: C1 (AMS) day 0, C2 (RTM) day 7 -> RTM short 300 in week 1.
* Pickup RTM first: 200 short, demurrage +1,000 (C1 waits 8 days at 200).
* C4 -> RTM: arrives week 2, too late: still 300.
* C5 -> RTM: route not allowed; would leave AMS 400 short (harmed).
* Combined (priority + C4 -> RTM): 0 short, +1,000.
* C3 is at sea and C6 is past its booking cut-off: never offered as swaps.
"""
import json
import os
import uuid
from pathlib import Path
from time import monotonic

import pytest

from app.models.entity_instance import EntityInstance
from app.models.user import User
from app.models.v2.query_view import QueryDataView
from app.models.v2.scenario import ScenarioRun, ScenarioRunStage
from app.models.v2.scenario_study import RunPlan
from app.services.auth_service import hash_password
from app.services.v2 import decision_packages
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.logic_assets import EXECUTORS
from app.tasks.v2.scenario import _execute_study_run

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "heineken"
pytestmark = pytest.mark.skipif(not os.environ.get("FUNCTION_RUNNER_URL"), reason="needs the isolated function runner")

WAREHOUSE_FIELDS = ["code", "on_hand", "capacity", "demand_w1", "demand_w2", "demand_w3"]
CONTAINER_FIELDS = ["code", "status", "destination", "quantity", "port_day", "demurrage_per_day", "eta_day", "cutoff_day",
                    "allowed_destinations", "pickup_rank"]


def ok(response, status=(200, 201)):
    assert response.status_code in ((status,) if isinstance(status, int) else status), response.text
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) and "data" in body else body


@pytest.fixture
def built(client, auth_headers, db, admin_user):
    graph = FalkorDBService()
    if not graph.available:
        pytest.fail("Real FalkorDB is required")
    data = json.loads((EXAMPLE / "data.json").read_text(encoding="utf-8"))
    ontology = ok(client.post("/api/v1/ontologies", headers=auth_headers, json={"name": "Heineken 自建", "domain": "供应链"}))
    api = f"/api/v2/ontologies/{ontology['id']}"

    # 1. Data: the same mapping import a user runs from a file or connection.
    for entity_class, rows in (("Warehouse", data["warehouses"]), ("Container", data["containers"])):
        mapping = ok(client.post(f"{api}/mappings", headers=auth_headers, json={
            "curated_dataset_id": f"heineken-{entity_class.lower()}", "entity_class": entity_class,
            "field_mapping": {}, "primary_key_column": "code"}))
        written = ok(client.post(f"{api}/mappings/{mapping['mapping_id']}/apply", headers=auth_headers, json=rows))
        assert written["instances_written"] == len(rows)

    # A field the planner will set (not in the source data) is added in the type editor.
    entities = ok(client.get(f"/api/v1/ontologies/{ontology['id']}/entities", headers=auth_headers))
    container_type = next(row for row in entities if row["name_en"] == "Container")
    ok(client.post(f"/api/v2/ontologies/{ontology['id']}/semantic-changes", headers=auth_headers, json={
        "target_kind": "attribute", "operation": "add",
        "payload": {"entity_id": container_type["id"], "property": {"id": "pickup_rank", "type": "integer"}}}))

    # 2. Logic: a user-authored Function, sent as text.
    function = ok(client.post(f"{api}/logic-assets/user-functions", headers=auth_headers, json={
        "asset_key": "heineken_container_decision", "name": "集装箱与目的地决策", "version": "1.0.0",
        "source": (EXAMPLE / "decision_function.py").read_text(encoding="utf-8"),
        "input_schema": {"type": "object", "required": ["warehouses", "containers", "scenario_day", "pickup_interval_days"],
                         "properties": {"warehouses": {"type": "array"}, "containers": {"type": "array"},
                                        "scenario_day": {"type": "number"}, "pickup_interval_days": {"type": "number"}}},
        "output_schema": {"type": "object", "required": ["decision"], "properties": {"decision": {"type": "object"}}}}), 201)
    assert function["implementation"] == "user_python" and "heineken_container_decision" not in EXECUTORS

    # 3. Write-back: two scenario Actions configured by the user.
    actions = {}
    for name, value_param, value_type, prop, effect in (
            ("改目的地", "destination", "string", "destination", {"kind": "carrier_rebooking", "description": "通知承运商改订舱目的地"}),
            ("设置提箱顺序", "rank", "number", "pickup_rank", None)):
        created = ok(client.post(f"{api}/scenario-action-types", headers=auth_headers, json={
            "name": name, "parameters": [{"name": "container", "type": "object", "object_type": "Container"},
                                         {"name": value_param, "type": value_type}],
            "rules": [{"op": "set_property", "target_parameter": "container", "property": prop, "value_parameter": value_param}],
            **({"external_effect": effect} if effect else {})}), 201)
        assert ok(client.post(f"{api}/scenario-action-types/{created['id']}:publish", headers=auth_headers))["status"] == "published"
        actions[name] = created

    # 4. Study: two object reads feeding the Function.
    spec = {"spec_version": "scenario-study/v1", "context_policy": {"scenario_mode": "pinned_on_run", "time_required": False},
            "parameters": [{"key": "scenario_day", "type": "decimal", "default": 0}, {"key": "pickup_interval_days", "type": "decimal", "default": 7}],
            "nodes": [{"key": "read_warehouses", "kind": "object_query", "outputs": {"objects": "object_set"},
                       "config": {"type": "Warehouse", "select": WAREHOUSE_FIELDS}},
                      {"key": "read_containers", "kind": "object_query", "outputs": {"objects": "object_set"},
                       "config": {"type": "Container", "select": CONTAINER_FIELDS}},
                      {"key": "decide", "kind": "function", "implementation": {"key": "heineken_container_decision", "version": "1.0.0"},
                       "inputs": {"warehouses": {"from": "read_warehouses.objects"}, "containers": {"from": "read_containers.objects"},
                                  "scenario_day": {"parameter": "scenario_day"}, "pickup_interval_days": {"parameter": "pickup_interval_days"}},
                       "outputs": {"decision": "decision"}}],
            "edges": [{"source": "read_warehouses.objects", "target": "decide.warehouses"},
                      {"source": "read_containers.objects", "target": "decide.containers"}],
            "metrics": [], "constraints": [], "budgets": {"max_nodes": 10, "max_objects": 10000, "max_wall_seconds": 300, "max_artifact_bytes": 10485760}}
    study = ok(client.post(f"{api}/scenario-study-definitions", headers=auth_headers, json={"name": "Heineken 三周供需", "draft_spec": spec}))
    ok(client.post(f"{api}/scenario-study-definitions/{study['id']}:publish", headers=auth_headers, json={"expected_etag": study["etag"]}), 201)
    reviewer = User(id=str(uuid.uuid4()), username="reviewer", email="reviewer@test.com", password_hash=hash_password("review123"), role="admin")
    db.add(reviewer)
    db.commit()
    token = ok(client.post("/api/v1/auth/login", json={"username": "reviewer", "password": "review123"}))["access_token"]
    connector = decision_packages.FakeProcurementConnector()
    original, decision_packages.procurement_connector = decision_packages.procurement_connector, connector
    try:
        yield {"api": api, "ontology_id": ontology["id"], "study": study, "connector": connector,
               "reviewer": {"Authorization": f"Bearer {token}"}}
    finally:
        decision_packages.procurement_connector = original
        for view in db.query(QueryDataView).filter_by(ontology_id=ontology["id"]).all():
            graph.delete_graph(view.graph_key)
        graph.delete_graph(ontology["id"])


def run_on_live(client, headers, db, admin_user, built, name):
    api, study = built["api"], built["study"]
    view = ok(client.post(f"{api}/object-query/data-views", headers=headers,
                          json={"source_ontology_id": built["ontology_id"], "source_manifest_digest": f"heineken-{name}"}), 201)
    scenario = ok(client.post(f"{api}/scenarios", headers=headers, json={"name": name, "base_view_id": view["view_id"]}), 201)
    case = ok(client.post(f"{api}/scenario-study-definitions/{study['id']}/cases", headers=headers, json={
        "study_version": 1, "scenario_id": scenario["id"], "name": name, "case_kind": "baseline" if name == "baseline" else "candidate"}), 201)
    plan = db.get(RunPlan, ok(client.post(f"{api}/scenario-study-definitions/{study['id']}/cases/{case['id']}:compile", headers=headers), 201)["id"])
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
    _execute_study_run(db, run, monotonic())  # what the Celery worker does
    db.refresh(run)
    assert run.status == "completed", run.error_json
    return run, run.result_manifest["nodes"]["decide"]["outputs"]["decision"]


def by_label(decision):
    return {row["label"]: row for row in decision["subjects"]}


def live_container(db, code):
    db.expire_all()
    rows = [row for row in db.query(EntityInstance).all() if (row.row_data or {}).get("object_type") == "Container" and row.row_data.get("code") == code]
    assert len(rows) == 1
    return rows[0].row_data


def test_new_user_builds_runs_applies_and_reverts_heineken(client, auth_headers, db, admin_user, built):
    run, decision = run_on_live(client, auth_headers, db, admin_user, built, "baseline")
    assert decision["metric"]["baseline"] == 300
    assert {k: v["metric_value"] for k, v in by_label(decision).items()} == {"AMS": 0, "RTM": 300, "UTR": 0}
    candidates = {row["id"]: row for row in decision["candidates"]}
    wh = {label: row["id"] for label, row in by_label(decision).items()}
    assert set(candidates) == {"combined", "prioritize_pickup", "swap:C4:RTM", "swap:C5:RTM"}  # C3 at sea, C6 past cut-off
    expected = {"combined": (0, 1000, [wh["RTM"]], [], True), "prioritize_pickup": (200, 1000, [], [], True),
                "swap:C4:RTM": (300, 0, [], [], True), "swap:C5:RTM": (700, 0, [], [wh["AMS"]], False)}
    for key, (after, cost, protected, harmed, applicable) in expected.items():
        row = candidates[key]
        assert (row["metric_after"], row["cost"], row["protected"], row["harmed"], row["applicable"]) == (after, cost, protected, harmed, applicable), key
    assert candidates["swap:C5:RTM"]["hard_constraints"][0]["code"] == "route_not_allowed"

    runs = f"{built['api']}/scenario-study-runs/{run.id}/decision-packages"
    blocked = client.post(runs, headers=auth_headers, json={"candidate_id": "swap:C5:RTM", "rationale": "x"})
    assert blocked.status_code == 422 and blocked.json()["detail"]["code"] == "hard_constraint_failed"
    package = ok(client.post(runs, headers=auth_headers, json={"candidate_id": "combined", "rationale": "RTM 第一周缺货最急，接受 1,000 滞港费。"}))
    edits = [(e["target"]["concrete_type"], e["property"], e["value"]) for a in package["actions"] for e in a["expected_edits"] if e["op"] == "set_property"]
    assert sorted(edits, key=str) == sorted([("Container", "pickup_rank", 1), ("Container", "pickup_rank", 2), ("Container", "destination", "RTM")], key=str)
    assert [a["external_effect_class"] for a in package["actions"]] == ["none", "none", "irreversible"]

    url = f"{built['api']}/decision-packages/{package['id']}"
    package = ok(client.post(f"{url}/transition", headers=auth_headers, json={"event": "submit", "expected_etag": package["etag"], "request_id": "s"}))
    package = ok(client.post(f"{url}/transition", headers=built["reviewer"], json={"event": "approve", "expected_etag": package["etag"], "request_id": "a"}))
    applied = ok(client.post(f"{url}:apply", headers=built["reviewer"], json={"expected_etag": package["etag"], "request_id": "apply"}))
    assert applied["package"]["status"] == "applied"
    assert applied["delivery"] == {"external_effects": 1, "delivered": 1, "failed": [], "status": "complete"}
    assert live_container(db, "C4")["destination"] == "RTM"
    assert (live_container(db, "C2")["pickup_rank"], live_container(db, "C1")["pickup_rank"]) == (1, 2)

    _, after = run_on_live(client, auth_headers, db, admin_user, built, "after-apply")
    assert after["metric"]["baseline"] == candidates["combined"]["metric_after"] == 0  # prediction holds on merged live data

    reverted = ok(client.post(f"{url}:revert", headers=built["reviewer"], json={"expected_etag": applied["package"]["etag"], "request_id": "revert"}))
    assert reverted["package"]["status"] == "reverted" and reverted["irreversible_effects"][0]["delivered"] is True
    assert live_container(db, "C4")["destination"] == "UTR"
    _, restored = run_on_live(client, auth_headers, db, admin_user, built, "after-revert")
    assert restored["metric"]["baseline"] == 300
