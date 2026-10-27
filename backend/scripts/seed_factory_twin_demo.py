"""Install the Factory Digital Twin showcase from data and public contracts.

The platform runtime is not extended by this script. It creates ordinary
Ontology types/objects, two user Functions, two Scenario Action types, one
Scenario, and a Workshop definition through the same reusable contracts used
by any other domain.

Run inside the backend container:
  python scripts/seed_factory_twin_demo.py --api http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models.entity import Entity  # noqa: E402
from app.models.entity_instance import EntityInstance  # noqa: E402
from app.models.ontology import OntologyProject  # noqa: E402
from app.models.v2.workshop_app import WorkshopApp  # noqa: E402
from app.services.v2.graph.falkordb_service import FalkorDBService  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "factory_twin"
NAME = "工厂数字孪生控制塔"


def unwrap(response: httpx.Response, expected: int = 200):
    if response.status_code != expected:
        raise RuntimeError(f"{response.request.method} {response.request.url}: {response.status_code} {response.text[:1200]}")
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


def login(client: httpx.Client, username: str, password: str) -> dict:
    body = unwrap(client.post("/api/v1/auth/login", json={"username": username, "password": password}))
    token = body.get("access_token") or (body.get("data") or {}).get("access_token")
    if not token:
        raise RuntimeError("Login response did not contain an access token")
    return {"Authorization": f"Bearer {token}"}


def properties(definitions: list[tuple[str, str, str]]) -> dict:
    return {"property_definitions": [{"id": key, "type": kind, "name": label} for key, kind, label in definitions]}


TYPE_DEFINITIONS = {
    "FactoryResource": properties([
        ("resource_id", "string", "资源 ID"), ("name", "string", "资源名称"), ("capability", "string", "能力")]),
    "FactoryAsset": properties([
        ("asset_id", "string", "资产 ID"), ("name", "string", "资产名称"), ("resource_id", "string", "所属资源"),
        ("risk_level", "string", "风险等级"), ("health_score", "double", "健康评分"), ("latest_cycle", "integer", "最新周期"),
        ("sensor_4", "double", "传感器 4"), ("sensor_11", "double", "传感器 11"),
        ("maintenance_status", "string", "维护状态"), ("source_unit", "integer", "源设备编号")]),
    "ScheduleItem": properties([
        ("schedule_id", "string", "任务 ID"), ("name", "string", "任务名称"), ("resource", "json", "资源链接"),
        ("start", "timestamp", "开始时间"), ("end", "timestamp", "结束时间"), ("layer", "string", "排程层"),
        ("predecessors", "json", "前置任务"), ("compatible_resources", "json", "兼容资源"),
        ("material_ready", "boolean", "物料就绪"), ("quality_hold", "boolean", "质量冻结"),
        ("order_id", "string", "工单"), ("status", "string", "状态"), ("priority", "integer", "优先级"),
        ("object_type", "string", "对象类型")]),
}


def install_objects(ontology_id: str, fixture: dict) -> int:
    graph = FalkorDBService()
    if not graph.available:
        raise RuntimeError("FalkorDB is unavailable")
    rows_by_type = {
        "FactoryResource": [{**row, "resource_id": row["id"], "name": row["label"]} for row in fixture["resources"]],
        "FactoryAsset": [{**row, "asset_id": row["id"]} for row in fixture["assets"]],
        "ScheduleItem": [{**row, "schedule_id": row["id"], "object_type": "ScheduleItem"} for row in fixture["schedule_items"]],
    }
    nodes = []
    with SessionLocal() as db:
        entities = {}
        for api_name, prop_defs in TYPE_DEFINITIONS.items():
            entity = Entity(ontology_id=ontology_id, name_cn={"FactoryResource": "工厂资源", "FactoryAsset": "设备资产", "ScheduleItem": "排程项"}[api_name],
                            name_en=api_name, canonical_id=f"factory-twin:{api_name}", type="EntityType", properties=prop_defs)
            db.add(entity); db.flush(); entities[api_name] = entity
        for api_name, rows in rows_by_type.items():
            for row in rows:
                source_id = row.pop("id")
                object_id = f"{ontology_id[:8]}:{api_name}:{source_id}"
                clean = {key: value for key, value in row.items() if key in {item["id"] for item in TYPE_DEFINITIONS[api_name]["property_definitions"]}}
                db.add(EntityInstance(id=object_id, entity_id=entities[api_name].id, ontology_id=ontology_id,
                                      row_identity=source_id, row_data=clean))
                nodes.append({"id": object_id, "entity_type": api_name, "properties": clean})
        db.commit()
    graph.upsert_instances(ontology_id, nodes)
    return len(nodes)


def user_function(client, root: str, headers: dict, *, key: str, name: str, source_file: str, contract: str):
    output_schema = {"type": "object", "required": ["schema"], "properties": {
        "schema": {"const": contract}, "row_group": {"type": ["object", "null"]},
        "source_puck_ids": {"type": "array"}, "violations": {"type": "array"}, "error": {"type": "string"}}}
    return unwrap(client.post(f"{root}/logic-assets/user-functions", headers=headers, json={
        "asset_key": key, "name": name, "description": "Generic contract-driven scheduling capability",
        "version": "1.0.0", "source": (EXAMPLE / source_file).read_text(encoding="utf-8"),
        "input_schema": {"type": "object", "required": ["schema", "schedule_objects", "domain_start", "domain_end"],
                         "properties": {"schema": {"type": "string"}, "schedule_objects": {"type": "array"},
                                        "domain_start": {"type": "string"}, "domain_end": {"type": "string"},
                                        "trigger": {"type": "object"}}},
        "output_schema": output_schema, "limits": {"timeout_seconds": 10, "memory_mb": 256}}), 201)


def action_type(client, root: str, headers: dict, body: dict) -> dict:
    created = unwrap(client.post(f"{root}/scenario-action-types", headers=headers, json=body), 201)
    return unwrap(client.post(f"{root}/scenario-action-types/{created['id']}:publish", headers=headers))


def app_definition(fixture: dict, scenario_id: str, modify_action: str, create_action: str,
                   search_asset: str, validation_asset: str) -> dict:
    resources = [{"id": row["id"], "label": row["label"]} for row in fixture["resources"]]
    sources = [
        {"id": "schedule", "kind": "object_set", "label": "排程对象", "object_type": "ScheduleItem", "limit": 2000},
        {"id": "assets", "kind": "object_set", "label": "设备资产", "object_type": "FactoryAsset", "limit": 1000},
        {"id": "schedule_status", "kind": "aggregate", "label": "任务状态", "object_type": "ScheduleItem", "group_by": ["status"],
         "metrics": [{"key": "tasks", "op": "count"}]},
        {"id": "asset_risk", "kind": "aggregate", "label": "资产风险", "object_type": "FactoryAsset", "group_by": ["risk_level"],
         "metrics": [{"key": "assets", "op": "count"}, {"key": "avg_health", "op": "avg", "property": "health_score"}]},
    ]
    gantt_props = {
        "id_field": "schedule_id", "label_field": "name", "resource_field": "resource", "start_field": "start", "end_field": "end",
        "dependency_field": "predecessors", "layer_field": "layer", "object_type_field": "object_type", "object_id_field": "id",
        "resources": resources, "resource_cardinality": "many", "resource_drop_behavior": "replace", "snap_minutes": 15,
        "allocation_behavior": "allocate", "enforce_rules": True, "operating_start_minute": 360, "operating_end_minute": 1080,
        "layers": [{"id": "constraint", "label": "可用产能", "style": "background", "color": "#94a3b8"},
                   {"id": "production", "label": "生产工单", "style": "standard", "color": "#2563eb"},
                   {"id": "maintenance", "label": "预测性维护", "style": "standard", "color": "#7c3aed"}],
        "scenario_variable": "scenario", "action_type_id": modify_action, "target_parameter": "task",
        "start_parameter": "start", "end_parameter": "end", "resource_parameter": "resource",
        "create_action_type_id": create_action, "create_id_parameter": "schedule_id", "create_label_parameter": "name",
        "create_start_parameter": "start", "create_end_parameter": "end", "create_resource_parameter": "resource",
        "create_default_label": "Operator-created task", "create_layer": "production",
        "recommendation_asset_id": search_asset, "validation_asset_id": validation_asset, "enforce_business_rules": True,
    }
    table_columns = [{"label": "任务", "field": "name", "format": "text"}, {"label": "工单", "field": "order_id", "format": "text"},
                     {"label": "状态", "field": "status", "format": "text", "badge": True}, {"label": "开始", "field": "start", "format": "date"},
                     {"label": "资源", "field": "resource", "format": "text"}]
    return {"schema": "workshop-app/v1", "data_sources": sources,
            "variables": [{"id": "scenario", "label": "Scenario", "default": scenario_id},
                          {"id": "risk", "label": "风险等级"}],
            "settings": {"time_range": "all", "refresh_minutes": 0}, "pages": [
        {"id": "command", "title": "工厂控制塔", "template": "free", "widgets": [
            {"id": "kpis", "type": "stat_row", "title": "运营态势", "source": "schedule", "path": "objects", "props": {"items": [
                {"label": "排程对象", "agg": "count", "format": "number"},
                {"label": "已发布工单", "agg": "count", "where_field": "status", "where_equals": "Released", "format": "number"},
                {"label": "未排任务", "agg": "count", "where_field": "status", "where_equals": "Unscheduled", "format": "number"}]}},
            {"id": "status", "type": "donut_chart", "title": "任务状态", "source": "schedule_status", "path": "groups",
             "props": {"category_field": "status", "value_field": "tasks", "center_label": "任务"}},
            {"id": "risk", "type": "donut_chart", "title": "设备健康风险", "source": "asset_risk", "path": "groups",
             "props": {"category_field": "risk_level", "value_field": "assets", "center_label": "设备", "variable": "risk"}},
            {"id": "health", "type": "rank_bars", "title": "设备健康评分", "source": "assets", "path": "objects",
             "props": {"label_field": "name", "value_field": "health_score", "max_rows": 8, "format": "number", "color": "#0f766e", "filter_field": "risk_level", "filter_variable": "risk"}},
            {"id": "work", "type": "table", "title": "当前工单与维护任务", "source": "schedule", "path": "objects", "props": {"columns": table_columns, "sort_field": "priority", "max_rows": 20}}],
         "layout": [{"i": "kpis", "x": 0, "y": 0, "w": 24, "h": 3}, {"i": "status", "x": 0, "y": 3, "w": 7, "h": 7},
                    {"i": "risk", "x": 7, "y": 3, "w": 7, "h": 7}, {"i": "health", "x": 14, "y": 3, "w": 10, "h": 7},
                    {"i": "work", "x": 0, "y": 10, "w": 24, "h": 11}]},
        {"id": "schedule", "title": "生产与维护协同排程", "template": "free", "widgets": [
            {"id": "gantt", "type": "schedule_gantt", "title": "Factory Twin · Dynamic Schedule", "source": "schedule", "path": "objects", "props": gantt_props},
            {"id": "task_table", "type": "table", "title": "排程对象明细", "source": "schedule", "path": "objects", "props": {"columns": table_columns, "max_rows": 30}}],
         "layout": [{"i": "gantt", "x": 0, "y": 0, "w": 24, "h": 18}, {"i": "task_table", "x": 0, "y": 18, "w": 24, "h": 10}]},
        {"id": "assets", "title": "设备数字孪生", "template": "free", "widgets": [
            {"id": "asset_table", "type": "table", "title": "NASA C-MAPSS 设备健康快照", "source": "assets", "path": "objects", "props": {"columns": [
                {"label": "设备", "field": "name", "format": "text"}, {"label": "资源", "field": "resource_id", "format": "text"},
                {"label": "风险", "field": "risk_level", "format": "text", "badge": True}, {"label": "健康评分", "field": "health_score", "format": "number", "tone_rule": "lt", "tone_value": 65},
                {"label": "维护状态", "field": "maintenance_status", "format": "text"}, {"label": "Sensor 4", "field": "sensor_4", "format": "number"},
                {"label": "Sensor 11", "field": "sensor_11", "format": "number"}], "sort_field": "health_score", "max_rows": 20}},
            {"id": "asset_health", "type": "rank_bars", "title": "健康评分排序", "source": "assets", "path": "objects",
             "props": {"label_field": "name", "value_field": "health_score", "max_rows": 10, "format": "number", "color": "#7c3aed"}}],
         "layout": [{"i": "asset_table", "x": 0, "y": 0, "w": 16, "h": 13}, {"i": "asset_health", "x": 16, "y": 0, "w": 8, "h": 9}]}
    ]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=os.environ.get("FACTORY_TWIN_PASSWORD", ""))
    parser.add_argument("--force-new", action="store_true")
    args = parser.parse_args()
    if not args.password:
        raise SystemExit("Set FACTORY_TWIN_PASSWORD or pass --password")
    fixture = json.loads((EXAMPLE / "data.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url=args.api, timeout=120) as client:
        headers = login(client, args.username, args.password)
        with SessionLocal() as db:
            existing = db.query(OntologyProject).filter_by(name=NAME).first()
            if existing and not args.force_new:
                app = db.query(WorkshopApp).filter_by(ontology_id=existing.id).order_by(WorkshopApp.updated_at.desc()).first()
                if app:
                    result = {"ontology_id": existing.id, "app_id": app.id,
                              "url": f"/apps/workshop/{app.id}?ontology={existing.id}", "reused": True}
                    print(json.dumps(result, ensure_ascii=False)); return
        name = NAME if not args.force_new else f"{NAME} · {datetime.now():%m%d-%H%M%S}"
        ontology = unwrap(client.post("/api/v1/ontologies", headers=headers, json={
            "name": name, "domain": "制造", "description": "Config-driven production scheduling and asset-health digital twin showcase"}), 201)
        ontology_id = ontology["id"]
        object_count = install_objects(ontology_id, fixture)
        root = f"/api/v2/ontologies/{ontology_id}"
        search = user_function(client, root, headers, key="generic_schedule_gap_search", name="通用排程空档推荐",
                               source_file="schedule_search.py", contract="schedule-search/v1")
        validation = user_function(client, root, headers, key="generic_schedule_validation", name="通用排程约束校验",
                                   source_file="schedule_validation.py", contract="schedule-validation/v1")
        modify = action_type(client, root, headers, {"name": "修改排程项", "description": "Update the bound time and resource fields",
            "parameters": [{"name": "task", "type": "object", "object_type": "ScheduleItem"}, {"name": "start", "type": "string"},
                           {"name": "end", "type": "string"}, {"name": "resource", "type": "string"}],
            "rules": [{"op": "set_property", "target_parameter": "task", "property": "start", "value_parameter": "start"},
                      {"op": "set_property", "target_parameter": "task", "property": "end", "value_parameter": "end"},
                      {"op": "set_property", "target_parameter": "task", "property": "resource", "value_parameter": "resource"}]})
        create = action_type(client, root, headers, {"name": "创建排程项", "description": "Create a scenario-local schedule object",
            "parameters": [{"name": "schedule_id", "type": "string"}, {"name": "name", "type": "string"},
                           {"name": "start", "type": "string"}, {"name": "end", "type": "string"}, {"name": "resource", "type": "string"}],
            "rules": [{"op": "create_object", "object_type": "ScheduleItem", "properties": {
                "schedule_id": {"parameter": "schedule_id"}, "name": {"parameter": "name"}, "start": {"parameter": "start"},
                "end": {"parameter": "end"}, "resource": {"parameter": "resource"}, "layer": {"static": "production"},
                "material_ready": {"static": True}, "status": {"static": "Proposed"}, "priority": {"static": 3}}}]})
        scenario = unwrap(client.post(f"{root}/scenarios:from-live", headers=headers, json={
            "name": "Factory Twin · Shift Recovery", "description": "Isolated operator proposals before controlled merge", "mode": "tracking"}), 201)
        definition = app_definition(fixture, scenario["id"], modify["id"], create["id"], search["id"], validation["id"])
        app = unwrap(client.post(f"{root}/workshop-apps", headers=headers, json={"name": "Factory Twin Operations",
            "description": "Asset health, production/maintenance schedule, constraints, recommendations and governed scenario actions",
            "definition": definition}), 201)
        result = {"ontology_id": ontology_id, "app_id": app["id"], "scenario_id": scenario["id"],
                  "search_asset_id": search["id"], "validation_asset_id": validation["id"], "objects": object_count,
                  "url": f"/apps/workshop/{app['id']}?ontology={ontology_id}", "reused": False}
        (Path(__file__).resolve().parents[1] / ".factory-twin-demo.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
