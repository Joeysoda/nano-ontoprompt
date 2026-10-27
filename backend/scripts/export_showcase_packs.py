"""Export the two OntoAct demo cases as self-contained data packs.

    python -I scripts/export_showcase_packs.py --out <ontoact>/demo_cases

Each pack is {ontology.json, objects.jsonl, links.jsonl, study.json, app.json,
MANIFEST.json}.  Objects carry local ids; the loader namespaces them per
ontology.  After writing, the MRP pack is read back and planned again, and the
headline numbers must match the reference run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.v2.material_shortage_objects import CODED_TYPES, OBJECT_TYPES, PLANNING_INPUT_TYPES, fixture_objects  # noqa: E402
from app.services.v2.mrp_planning import mrpspeed_planning  # noqa: E402
from app.services.v2.mrpspeed_fixture import HORIZON_WEEKS, mrpspeed_fixture  # noqa: E402

PLANNING_FUNCTION = "mrp_planning_v1"
PLANNING_OUTPUTS = ("scenario_time", "quarter", "totals", "portfolio", "policies", "demand_events", "demand_by_month",
                    "coverage", "candidate_actions", "projected_revenue", "revenue_at_risk", "completeness")
# Business-key references, materialized as links.  (source type, property, target type, link name)
# Link names are unique: a query resolves a link by name and direction.
MRP_LINKS = [
    ("CustomerOrder", "item", "ItemMaster", "ORDERS"),
    ("CustomerOrder", "product_line", "ProductLine", "FOR_PRODUCT_LINE"),
    ("BOMLine", "parent_item", "ItemMaster", "PARENT_ITEM"),
    ("BOMLine", "component_item", "ItemMaster", "COMPONENT_ITEM"),
    ("InventoryLot", "item", "ItemMaster", "STOCK_OF"),
    ("SupplyCommitment", "item", "ItemMaster", "SUPPLIES"),
    ("SupplyOption", "item", "ItemMaster", "CAN_SUPPLY"),
    ("Reservation", "demand_id", "CustomerOrder", "RESERVED_FOR"),
    ("ItemMaster", "product_line", "ProductLine", "PART_OF_LINE"),
    ("ProductLine", "policy", "PlanningPolicy", "USES_POLICY"),
]
# Reference run of the original system (2026-10-23, docs/plans/ONTOACT_SHOWCASE_PLAN.md section 2).
EXPECTED_TOTALS = {"demand_events": 135, "revenue_target": 1857023.73, "projected_revenue": 1186706.62,
                   "revenue_gap": -670317.11, "otif": 0.4889, "planned_orders": 15}
EXPECTED_CANDIDATES = {  # id: (incremental_cost, quarter_revenue_delta, protected orders, harmed orders)
    "combined:BIKE": (22715.55, 198286.78, 10, 0),
    "expedite:BIKE:Aluminum Frame": (16934.40, 0.0, 0, 0),
    "expedite:BIKE:Rear Derailleur": (3491.10, 55196.32, None, 0),
}


APP_COPY = {
    "MRPSpeed 规划": "Quarterly Planning",
    "产品线": "Product line",
    "情景": "Planning policy",
    "动作": "Actions",
    "MRPSpeed Cockpit": "Planning Cockpit",
    "MRPSpeed Suggested Actions": "Suggested Actions",
    "季度收入 +{quarter_revenue_delta|money}": "Quarterly revenue +{quarter_revenue_delta|money}",
    "有 {count} 个需求事件延期需要关注。": "{count} demand events are late and need attention.",
    "Requirements — Surplus/Deficit of Inventory for Demand": "Requirements: inventory surplus / deficit against demand",
    "季度收入 {quarter_revenue_delta|money} · 成本 {incremental_cost|money}": "Quarterly revenue {quarter_revenue_delta|money} · Cost {incremental_cost|money}",
    "季度预计收入": "Projected quarterly revenue",
    "风险需求事件": "Demand events at risk",
    "增量成本": "Incremental cost",
    "Execute：重跑 MRPSpeed 并更新预测": "Re-run plan and update forecast",
    # English labels that read like internal names.
    "Scenario": "Planning policy",
    "Root Item": "Product line",
    "Demand Event Views": "View",
    "Demand Event Id": "Demand event",
    "Total Demand Events": "Demand events",
    "Demand Events At Risk": "Demand events at risk",
    "Total Planned Orders": "Planned orders",
    # Re-run steps show a before/after value, so they are named after the value.
    "Generating New Orders": "Planned orders",
    "Updating Financial Forecast": "Projected revenue",
    # Chart colors follow the single blue palette.
    "#b91c1c": "#93c5fd",
}


# Action names must match study.json action_types.
FUNCTION_COPY = {
    '"设置提箱顺序"': '"Set pickup order"',
    '"改目的地"': '"Change destination"',
    '"按缺货紧迫度调整提箱顺序"': '"Reorder pickups by shortage urgency"',
    "f\"{container['code']} 改送 {target}\"": "f\"Send {container['code']} to {target}\"",
    '"调整提箱顺序 + " + "、".join(f"{k} 改送 {v}" for k, v in moves.items())':
        '"Reorder pickups + " + ", ".join(f"send {k} to {v}" for k, v in moves.items())',
    "f\"第 {w['week']} 周缺 {w['unmet']:g} 箱\"": "f\"Week {w['week']}: short {w['unmet']:g} cases\"",
    "f\"第 {w['week']} 周到货\"": "f\"Week {w['week']} arrivals\"",
    '"未来三周仓库供需与集装箱调整"': '"Warehouse supply and container moves, next three weeks"',
    '"仓库"': '"Warehouse"',
    '"三周缺货箱数"': '"Cases short over three weeks"',
    '"箱"': '"cases"',
    '"缺货仓库"': '"Warehouses short"',
    '"当前滞港费"': '"Current demurrage"',
    '"可改目的地集装箱"': '"Containers that can be re-routed"',
}


CONTAINER_APP = {
    "schema": "workshop-app/v1",
    "name": "Container Scheduling",
    "data_sources": [{"id": "plan", "kind": "study_run", "label": "Three-week plan", "study_id": "__STUDY_ID__", "node": "decide"}],
    "variables": [{"id": "action", "label": "Action"}],
    "pages": [
        {"id": "overview", "title": "Overview", "widgets": [
            {"id": "kpis", "type": "kpi_strip", "title": "", "source": "plan", "path": "", "props": {"items": [
                {"label": "Cases short over three weeks", "path": "decision.metric.baseline", "format": "number"},
                {"label": "Warehouses short", "path": "decision.kpis.0.value", "format": "number"},
                {"label": "Current demurrage", "path": "decision.kpis.1.value", "format": "money"},
                {"label": "Containers that can be re-routed", "path": "decision.kpis.2.value", "format": "number"}]}},
            {"id": "warehouses", "type": "table", "title": "Warehouses", "source": "plan", "path": "decision.subjects", "props": {"columns": [
                {"label": "Warehouse", "field": "label"}, {"label": "Status", "field": "status", "badge": True},
                {"label": "Cases short", "field": "metric_value", "format": "number", "tone_rule": "gt", "tone_value": 0},
                {"label": "Why", "field": "reasons"}]}},
            {"id": "suggested", "type": "action_list", "title": "Suggested Actions", "source": "plan", "path": "decision.candidates",
             "props": {"label_field": "label", "subtitle": "Cases short after: {metric_after|number} · Cost {cost|money}", "variable": "action",
                       "only_applicable": True, "navigate": "actions"}}],
         "layout": [{"i": "kpis", "x": 0, "y": 0, "w": 24, "h": 3}, {"i": "warehouses", "x": 0, "y": 3, "w": 15, "h": 9}, {"i": "suggested", "x": 15, "y": 3, "w": 9, "h": 9}]},
        {"id": "actions", "title": "Suggested Actions", "widgets": [
            {"id": "actionList", "type": "action_list", "title": "Suggestions", "source": "plan", "path": "decision.candidates",
             "props": {"label_field": "label", "subtitle": "Cases short after: {metric_after|number} · Cost {cost|money}", "variable": "action"}},
            {"id": "detail", "type": "action_detail", "title": "Action", "source": "plan", "path": "decision.candidates", "props": {"variable": "action", "explain": False, "metrics": [
                {"label": "Cases short over three weeks", "before_path": "decision.metric.baseline", "after_field": "metric_after", "format": "number"},
                {"label": "Cost", "after_field": "cost", "format": "money"}]}},
            {"id": "rerun", "type": "rerun_button", "title": "", "source": "plan", "path": "", "props": {"label": "Re-run plan", "steps": [
                {"label": "Cases short", "path": "decision.metric.baseline", "format": "number"}]}}],
         "layout": [{"i": "actionList", "x": 0, "y": 0, "w": 8, "h": 19}, {"i": "detail", "x": 8, "y": 0, "w": 16, "h": 17}, {"i": "rerun", "x": 8, "y": 17, "w": 16, "h": 2}]},
    ],
    "settings": {"time_range": "all", "refresh_minutes": 0},
}


def _clean_policy(name: str) -> str:
    return name.removeprefix("MRPSpeed ").strip()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8", newline="\n")


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _manifest(folder: Path, meta: dict) -> None:
    files = {}
    for path in sorted(folder.iterdir()):
        if path.name != "MANIFEST.json":
            files[path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    _write_json(folder / "MANIFEST.json", {**meta, "pack_version": 1, "exported": date.today().isoformat(), "files": files})


def _links(nodes: list[dict], specs) -> list[dict]:
    by_key = {}
    for node in nodes:
        key = node["properties"].get("code", node["id"])
        by_key[(node["entity_type"], key)] = node["id"]
        by_key[(node["entity_type"], node["id"])] = node["id"]
    links, missing = [], 0
    for source_type, prop, target_type, name in specs:
        for node in nodes:
            if node["entity_type"] != source_type or node["properties"].get(prop) in (None, ""):
                continue
            target = by_key.get((target_type, node["properties"][prop]))
            if target is None:
                missing += 1
                continue
            links.append({"type": name, "source": node["id"], "target": target})
    if missing:
        raise SystemExit(f"{missing} references do not resolve to an object")
    return links


def export_mrp(out: Path) -> None:
    fixture = mrpspeed_fixture()
    for row in fixture["policy_definitions"]:
        row["id"] = _clean_policy(row["id"])
    for row in fixture["product_lines"]:
        row["policy"] = _clean_policy(row["policy"])
    nodes = fixture_objects(fixture, id_prefix="", input_types=PLANNING_INPUT_TYPES)
    links = _links(nodes, MRP_LINKS)
    folder = out / "dataco_mrp"
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(folder / "ontology.json", {
        "name": "DataCo Quarterly Planning",
        "domain": "Manufacturing",
        "description": "Quarterly revenue, demand risk, material coverage and recovery actions for five product lines.",
        "object_types": [{"api_name": name, "key_property": "code" if name in CODED_TYPES else None,
                          "properties": [{"id": key, "type": kind} for key, kind in props.items()]}
                         for name, props in OBJECT_TYPES.items()],
        "link_types": [{"api_name": name, "source": source, "target": target, "via": prop}
                       for source, prop, target, name in MRP_LINKS],
    })
    _write_jsonl(folder / "objects.jsonl", [{"id": n["id"], "type": n["entity_type"], "properties": n["properties"]} for n in nodes])
    _write_jsonl(folder / "links.jsonl", links)
    _write_json(folder / "study.json", {
        "name": "Quarterly portfolio plan",
        "description": "Plans every product line from Ontology objects and evaluates expedite, rework, deferral and policy actions.",
        "builtin_functions": [PLANNING_FUNCTION],
        "spec": {
            "spec_version": "scenario-study/v1",
            "context_policy": {"scenario_mode": "pinned_on_run", "time_required": True},
            "parameters": [{"key": "scenario_time", "label": "Planning date", "type": "datetime", "default": fixture["scenario_time"]},
                           {"key": "horizon_weeks", "label": "Horizon (weeks)", "type": "integer", "default": HORIZON_WEEKS},
                           {"key": "quarter_start", "label": "Quarter start (days from planning date)", "type": "integer", "default": fixture["quarter_start"]},
                           {"key": "quarter_end", "label": "Quarter end (days from planning date)", "type": "integer", "default": fixture["quarter_end"]}],
            "nodes": [{"key": f"read_{key}", "kind": "object_query", "outputs": {"objects": "object_set"},
                       "config": {"type": kind, "select": sorted(OBJECT_TYPES[kind])}} for key, kind in PLANNING_INPUT_TYPES.items()] + [
                {"key": "plan", "kind": "function", "implementation": {"key": PLANNING_FUNCTION, "version": "1.0.0"},
                 "inputs": {**{key: {"parameter": key} for key in ("scenario_time", "horizon_weeks", "quarter_start", "quarter_end")},
                            **{key: {"from": f"read_{key}.objects"} for key in PLANNING_INPUT_TYPES}},
                 "outputs": {key: key for key in PLANNING_OUTPUTS}}],
            "edges": [{"source": f"read_{key}.objects", "target": f"plan.{key}"} for key in PLANNING_INPUT_TYPES],
            "metrics": [{"key": "projected_revenue", "label": "Projected quarterly revenue", "type": "decimal", "direction": "maximize",
                         "unit": "currency", "source": "plan.projected_revenue"}],
            "constraints": [],
            "budgets": {"max_nodes": 50, "max_objects": 100000, "max_wall_seconds": 900, "max_artifact_bytes": 52428800},
        },
    })
    def translate(value):
        if isinstance(value, dict):
            return {key: translate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [translate(item) for item in value]
        return APP_COPY.get(value, value) if isinstance(value, str) else value

    app = json.dumps(translate(json.loads((BACKEND / "examples" / "mrpspeed_app.json").read_text(encoding="utf-8"))),
                     ensure_ascii=False, indent=1)
    leftovers = sorted({ch for ch in app if ord(ch) > 127} - {"·", "—"})
    if "MRPSpeed" in app or leftovers:
        raise SystemExit(f"app.json still has untranslated copy: {leftovers}")
    (folder / "app.json").write_text(app + "\n", encoding="utf-8", newline="\n")
    _manifest(folder, {
        "case": "dataco_mrp",
        "objects": len(nodes), "links": len(links),
        "sources": {"demand": fixture["provenance"]["real"],
                    "supply": fixture["provenance"]["notional"]},
        "license": "Demand rows: CC BY 4.0 (DataCo Smart Supply Chain). Rule-generated supply: same terms as this repository.",
    })
    verify_mrp(folder)


def verify_mrp(folder: Path) -> None:
    """Rebuild planning inputs from the pack alone and compare with the reference run."""
    objects = [json.loads(line) for line in (folder / "objects.jsonl").read_text(encoding="utf-8").splitlines()]
    study = json.loads((folder / "study.json").read_text(encoding="utf-8"))
    inputs = {p["key"]: p["default"] for p in study["spec"]["parameters"]}
    for key, kind in PLANNING_INPUT_TYPES.items():
        inputs[key] = [{"id": o["id"], **o["properties"]} for o in objects if o["type"] == kind]
    result = mrpspeed_planning(inputs)
    problems = [f"totals.{k}: {result['totals'].get(k)} != {v}" for k, v in EXPECTED_TOTALS.items()
                if abs(result["totals"].get(k, float("nan")) - v) > 0.005]
    candidates = {c["id"]: c for c in result["candidate_actions"]}
    for cid, (cost, delta, protected, harmed) in EXPECTED_CANDIDATES.items():
        got = candidates.get(cid)
        if got is None:
            problems.append(f"{cid}: missing")
            continue
        actual = (got["incremental_cost"], got["quarter_revenue_delta"], len(got["protected_orders"]), len(got["harmed_orders"]))
        for name, want, have in zip(("cost", "revenue_delta", "protected", "harmed"), (cost, delta, protected, harmed), actual):
            if want is not None and abs(have - want) > 0.005:
                problems.append(f"{cid}.{name}: {have} != {want}")
        print(f"{cid}: cost={actual[0]} revenue_delta={actual[1]} protected={actual[2]} harmed={actual[3]}")
    print(f"totals: {json.dumps({k: result['totals'][k] for k in EXPECTED_TOTALS})}")
    if problems:
        raise SystemExit("pack does not reproduce the reference run:\n  " + "\n  ".join(problems))
    print("dataco_mrp pack reproduces the reference run")


def export_container(out: Path) -> None:
    source = BACKEND / "examples" / "heineken"
    data = json.loads((source / "data.json").read_text(encoding="utf-8"))
    folder = out / "container_scheduling"
    folder.mkdir(parents=True, exist_ok=True)
    warehouse_props = {"code": "string", "on_hand": "double", "capacity": "double", "demand_w1": "double", "demand_w2": "double", "demand_w3": "double"}
    container_props = {"code": "string", "status": "string", "destination": "string", "quantity": "double", "port_day": "double",
                       "demurrage_per_day": "double", "eta_day": "double", "cutoff_day": "double", "allowed_destinations": "array",
                       "pickup_rank": "integer"}
    nodes = [{"id": r["code"], "entity_type": "Warehouse", "properties": r} for r in data["warehouses"]]
    nodes += [{"id": r["code"], "entity_type": "Container", "properties": r} for r in data["containers"]]
    links = _links(nodes, [("Container", "destination", "Warehouse", "BOUND_FOR")])
    _write_json(folder / "ontology.json", {
        "name": "Container Scheduling",
        "domain": "Logistics",
        "description": "Three-week warehouse supply and demand, container pickup order and destination changes.",
        "object_types": [{"api_name": "Warehouse", "key_property": "code", "properties": [{"id": k, "type": t} for k, t in warehouse_props.items()]},
                         {"api_name": "Container", "key_property": "code", "properties": [{"id": k, "type": t} for k, t in container_props.items()]}],
        "link_types": [{"api_name": "BOUND_FOR", "source": "Container", "target": "Warehouse", "via": "destination"}],
    })
    _write_jsonl(folder / "objects.jsonl", [{"id": n["id"], "type": n["entity_type"], "properties": n["properties"]} for n in nodes])
    _write_jsonl(folder / "links.jsonl", links)
    function_source = (source / "decision_function.py").read_text(encoding="utf-8").replace(
        '"""Heineken-style container and destination decision, written as a user Function.',
        '"""Container pickup and destination decision, written as a user Function.')
    for old, new in FUNCTION_COPY.items():
        function_source = function_source.replace(old, new)
    if any(ord(ch) > 127 for ch in function_source.replace("·", "")):
        raise SystemExit("decision_function.py still has untranslated copy")
    (folder / "decision_function.py").write_text(function_source, encoding="utf-8", newline="\n")
    warehouse_fields = list(warehouse_props)
    container_fields = list(container_props)
    _write_json(folder / "study.json", {
        "name": "Three-week supply and demand",
        "description": "Plans container pickups and destination changes against warehouse demand.",
        "user_functions": [{
            "asset_key": "container_decision", "name": "Container and destination decision", "version": "1.0.0",
            "source_file": "decision_function.py",
            "input_schema": {"type": "object", "required": ["warehouses", "containers", "scenario_day", "pickup_interval_days"],
                             "properties": {"warehouses": {"type": "array"}, "containers": {"type": "array"},
                                            "scenario_day": {"type": "number"}, "pickup_interval_days": {"type": "number"}}},
            "output_schema": {"type": "object", "required": ["decision"], "properties": {"decision": {"type": "object"}}}}],
        "action_types": [
            {"name": "Change destination",
             "parameters": [{"name": "container", "type": "object", "object_type": "Container"}, {"name": "destination", "type": "string"}],
             "rules": [{"op": "set_property", "target_parameter": "container", "property": "destination", "value_parameter": "destination"}],
             "external_effect": {"kind": "carrier_rebooking", "description": "Asks the carrier to rebook the container to the new destination"}},
            {"name": "Set pickup order",
             "parameters": [{"name": "container", "type": "object", "object_type": "Container"}, {"name": "rank", "type": "number"}],
             "rules": [{"op": "set_property", "target_parameter": "container", "property": "pickup_rank", "value_parameter": "rank"}]}],
        "spec": {
            "spec_version": "scenario-study/v1", "context_policy": {"scenario_mode": "pinned_on_run", "time_required": False},
            "parameters": [{"key": "scenario_day", "label": "Scenario day", "type": "decimal", "default": 0},
                           {"key": "pickup_interval_days", "label": "Days between pickups", "type": "decimal", "default": 7}],
            "nodes": [{"key": "read_warehouses", "kind": "object_query", "outputs": {"objects": "object_set"}, "config": {"type": "Warehouse", "select": warehouse_fields}},
                      {"key": "read_containers", "kind": "object_query", "outputs": {"objects": "object_set"}, "config": {"type": "Container", "select": container_fields}},
                      {"key": "decide", "kind": "function", "implementation": {"key": "container_decision", "version": "1.0.0"},
                       "inputs": {"warehouses": {"from": "read_warehouses.objects"}, "containers": {"from": "read_containers.objects"},
                                  "scenario_day": {"parameter": "scenario_day"}, "pickup_interval_days": {"parameter": "pickup_interval_days"}},
                       "outputs": {"decision": "decision"}}],
            "edges": [{"source": "read_warehouses.objects", "target": "decide.warehouses"},
                      {"source": "read_containers.objects", "target": "decide.containers"}],
            "metrics": [], "constraints": [],
            "budgets": {"max_nodes": 10, "max_objects": 10000, "max_wall_seconds": 300, "max_artifact_bytes": 10485760}},
    })
    _write_json(folder / "app.json", CONTAINER_APP)
    _manifest(folder, {"case": "container_scheduling", "objects": len(nodes), "links": len(links),
                       "sources": {"all": "Synthetic data written for this demo; days are offsets from scenario day 0."},
                       "license": "Same terms as this repository."})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    export_mrp(args.out)
    export_container(args.out)


if __name__ == "__main__":
    main()
