"""Seed the Heineken-style container demo through the HTTP API only, as a new user would.

Imports the synthetic data via mappings, adds a planner field in the type
editor, submits the user Function (examples/heineken, sent as text, run by the
isolated function runner), configures two scenario Actions, publishes a Study
and runs it.  No app is created: apps are built in the app builder.

    python scripts/seed_heineken_demo.py --api http://backend:8000
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "heineken"
WAREHOUSE_FIELDS = ["code", "on_hand", "capacity", "demand_w1", "demand_w2", "demand_w3"]
CONTAINER_FIELDS = ["code", "status", "destination", "quantity", "port_day", "demurrage_per_day", "eta_day", "cutoff_day", "allowed_destinations", "pickup_rank"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://backend:8000")
    args = parser.parse_args()
    data = json.loads((EXAMPLE / "data.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url=args.api, timeout=180) as client:
        def ok(response):
            response.raise_for_status()
            body = response.json()
            return body.get("data", body) if isinstance(body, dict) and "data" in body else body

        body = {"name": "Heineken 集装箱演示", "domain": "供应链", "description": "仓库三周供需、集装箱提箱与改目的地；示例数据为合成数据"}
        created = client.post("/api/v1/ontologies", json=body)
        if created.status_code == 409:
            created = client.post("/api/v1/ontologies", json={**body, "name": f"{body['name']} · {datetime.now():%m%d-%H%M%S}"})
        ontology = ok(created)
        oid = ontology["id"]
        api = f"/api/v2/ontologies/{oid}"
        # Data enters like a user's file: upload -> pipeline -> curated dataset -> mapping -> objects.
        for entity_class, rows in (("Warehouse", data["warehouses"]), ("Container", data["containers"])):
            payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows).encode("utf-8")
            dataset = ok(client.post("/api/v2/datasets/upload", files={"file": (f"{entity_class.lower()}s.jsonl", payload, "application/x-ndjson")}))
            pipeline = ok(client.post("/api/v2/pipelines", json={"name": f"{entity_class} {oid[:8]}", "source_dataset_id": dataset["id"], "route": "A"}))
            result = ok(client.post(f"/api/v2/pipelines/{pipeline['id']}/run-sync"))
            if result["status"] != "success":
                raise SystemExit(f"pipeline failed for {entity_class}: {result}")
            curated = next(row for row in ok(client.get("/api/v2/curated")) if row.get("pipeline_id") == pipeline["id"])
            mapping = ok(client.post(f"{api}/mappings", json={"curated_dataset_id": curated["id"], "entity_class": entity_class,
                                                             "field_mapping": {}, "primary_key_column": "code"}))
            ok(client.post(f"{api}/mappings/{mapping['mapping_id']}/apply-from-dataset"))
        container_type = next(row for row in ok(client.get(f"/api/v1/ontologies/{oid}/entities")) if row["name_en"] == "Container")
        ok(client.post(f"{api}/semantic-changes", json={"target_kind": "attribute", "operation": "add",
                                                         "payload": {"entity_id": container_type["id"], "property": {"id": "pickup_rank", "type": "integer"}}}))
        ok(client.post(f"{api}/logic-assets/user-functions", json={
            "asset_key": "heineken_container_decision", "name": "集装箱与目的地决策", "version": "1.0.0",
            "source": (EXAMPLE / "decision_function.py").read_text(encoding="utf-8"),
            "input_schema": {"type": "object", "required": ["warehouses", "containers", "scenario_day", "pickup_interval_days"],
                             "properties": {"warehouses": {"type": "array"}, "containers": {"type": "array"},
                                            "scenario_day": {"type": "number"}, "pickup_interval_days": {"type": "number"}}},
            "output_schema": {"type": "object", "required": ["decision"], "properties": {"decision": {"type": "object"}}}}))
        for name, param, kind, prop, effect in (("改目的地", "destination", "string", "destination", {"kind": "carrier_rebooking", "description": "通知承运商改订舱目的地"}),
                                                ("设置提箱顺序", "rank", "number", "pickup_rank", None)):
            action = ok(client.post(f"{api}/scenario-action-types", json={
                "name": name, "parameters": [{"name": "container", "type": "object", "object_type": "Container"}, {"name": param, "type": kind}],
                "rules": [{"op": "set_property", "target_parameter": "container", "property": prop, "value_parameter": param}],
                **({"external_effect": effect} if effect else {})}))
            ok(client.post(f"{api}/scenario-action-types/{action['id']}:publish"))
        spec = {"spec_version": "scenario-study/v1", "context_policy": {"scenario_mode": "pinned_on_run", "time_required": False},
                "parameters": [{"key": "scenario_day", "type": "decimal", "default": 0}, {"key": "pickup_interval_days", "type": "decimal", "default": 7}],
                "nodes": [{"key": "read_warehouses", "kind": "object_query", "outputs": {"objects": "object_set"}, "config": {"type": "Warehouse", "select": WAREHOUSE_FIELDS}},
                          {"key": "read_containers", "kind": "object_query", "outputs": {"objects": "object_set"}, "config": {"type": "Container", "select": CONTAINER_FIELDS}},
                          {"key": "decide", "kind": "function", "implementation": {"key": "heineken_container_decision", "version": "1.0.0"},
                           "inputs": {"warehouses": {"from": "read_warehouses.objects"}, "containers": {"from": "read_containers.objects"},
                                      "scenario_day": {"parameter": "scenario_day"}, "pickup_interval_days": {"parameter": "pickup_interval_days"}},
                           "outputs": {"decision": "decision"}}],
                "edges": [{"source": "read_warehouses.objects", "target": "decide.warehouses"}, {"source": "read_containers.objects", "target": "decide.containers"}],
                "metrics": [], "constraints": [], "budgets": {"max_nodes": 10, "max_objects": 10000, "max_wall_seconds": 300, "max_artifact_bytes": 10485760}}
        study = ok(client.post(f"{api}/scenario-study-definitions", json={"name": "Heineken 三周供需", "draft_spec": spec}))
        version = ok(client.post(f"{api}/scenario-study-definitions/{study['id']}:publish", json={"expected_etag": study["etag"]}))
        view = ok(client.post(f"{api}/object-query/data-views", json={"source_ontology_id": oid, "source_manifest_digest": "heineken-demo"}))
        scenario = ok(client.post(f"{api}/scenarios", json={"name": "Heineken 基线", "base_view_id": view["view_id"]}))
        case = ok(client.post(f"{api}/scenario-study-definitions/{study['id']}/cases", json={"study_version": version["version"], "scenario_id": scenario["id"], "name": "基线", "case_kind": "baseline"}))
        ok(client.post(f"{api}/scenario-study-definitions/{study['id']}/cases/{case['id']}:compile"))
        run = ok(client.post(f"{api}/scenario-study-definitions/{study['id']}/cases/{case['id']}/runs", json={"expected_etag": case["etag"], "client_request_id": "seed"}))
        for _ in range(60):
            state = ok(client.get(f"{api}/scenario-study-runs/{run['id']}"))
            if state["status"] in ("completed", "failed"):
                break
            time.sleep(2)
        print(f"ontology={oid} study={study['id']} run={run['id']} status={state['status']} error={state.get('error')}")


if __name__ == "__main__":
    main()
