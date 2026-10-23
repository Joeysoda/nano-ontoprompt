"""Seed two Workshop dashboards that exercise the Ontology sources and the map, donut, ranking and alert widgets.

* 门店与迟交分布 (real data): DataCo orders (CC BY 4.0) as Order objects
  (order time, sales, late flag, purchase city) plus StoreCity dimension objects
  (city, state, store coordinates: the median of DataCo's store Latitude/Longitude
  per purchase city).  Customer identity columns are never read.  The page
  aggregates orders per city on the server for the selected time range, so
  "last 7 days" recomputes every figure; nothing is pre-aggregated per city.
  DataCo's order timestamps carry no time zone and are read as UTC.  An order is
  late when any of its lines has Late_delivery_risk = 1.  Late level of a city:
  高 >= 60 %, 中 50-60 %, 低 < 50 % of its orders late.
* 设备告警总览: the Azure PdM Machinery ontology from seed_machinery_demo.py
  (100 machines, rule alerts), which must be seeded first.

Bulk objects have no API, so they go through the same SQL + graph path as the
other demo seeders; ontologies and apps go through the HTTP API.

    python scripts/seed_dashboard_demo.py --dataco /dataco/DataCoSupplyChainDataset.csv --api http://backend:8000
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models.entity import Entity  # noqa: E402
from app.models.entity_instance import EntityInstance  # noqa: E402
from app.models.ontology import OntologyProject  # noqa: E402
from app.models.v2.rule_runtime import RuleAlert, RuleResource  # noqa: E402
from app.services.v2.graph.falkordb_service import FalkorDBService  # noqa: E402

CITY_NAME = "门店城市分布（DataCo 真实数据）"
MACHINERY_NAME = "设备运维演示（Azure PdM）"
TYPES = {
    "StoreCity": ("门店城市", {"city": "string", "state": "string", "country": "string", "latitude": "double", "longitude": "double"}),
    "Order": ("订单", {"order_time": "timestamp", "city_id": "string", "state": "string", "sales": "double", "late": "double"}),
}
LATE_BANDS = [{"field": "late_rate", "into": "late_level", "cuts": [[0.6, "高"], [0.5, "中"]], "otherwise": "低"}]


def dataco_objects(csv_path: Path, prefix: str) -> tuple[list[dict], list[dict]]:
    """Object ids are unique across ontologies, so they carry the ontology prefix (the UI hides it)."""
    frame = pd.read_csv(csv_path, encoding="latin-1", usecols=["Customer City", "Customer State", "Customer Country", "Latitude", "Longitude",
                                                              "Order Id", "order date (DateOrders)", "Sales", "Late_delivery_risk"])
    frame["city_id"] = prefix + frame["Customer City"] + "|" + frame["Customer State"]
    cities = frame.groupby(["city_id", "Customer City", "Customer State", "Customer Country"]).agg(
        latitude=("Latitude", "median"), longitude=("Longitude", "median")).reset_index()
    orders = frame.groupby("Order Id").agg(order_time=("order date (DateOrders)", "first"), city_id=("city_id", "first"), state=("Customer State", "first"),
                                           sales=("Sales", "sum"), late=("Late_delivery_risk", "max")).reset_index()
    city_nodes = [{"id": row.city_id, "entity_type": "StoreCity", "properties": {
        "city": row[1], "state": row[2], "country": "USA" if row[3] == "EE. UU." else row[3],
        "latitude": round(float(row.latitude), 5), "longitude": round(float(row.longitude), 5)}} for row in cities.itertuples(index=False)]
    order_nodes = [{"id": f"{prefix}order-{int(row[0])}", "entity_type": "Order", "properties": {
        "order_time": datetime.strptime(row.order_time, "%m/%d/%Y %H:%M").replace(tzinfo=timezone.utc).isoformat(),
        "city_id": row.city_id, "state": row.state, "sales": round(float(row.sales), 2), "late": float(row.late)}} for row in orders.itertuples(index=False)]
    return city_nodes, order_nodes


def ok(response):
    response.raise_for_status()
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


def seed_dataco(client: httpx.Client, csv_path: Path) -> tuple[str, list[dict], list[dict]]:
    body = {"name": CITY_NAME, "domain": "供应链", "description": "DataCo 真实订单（CC BY 4.0）与门店城市；指标按所选时间范围从订单实时汇总"}
    created = client.post("/api/v1/ontologies", json=body)
    if created.status_code == 409:
        created = client.post("/api/v1/ontologies", json={**body, "name": f"{CITY_NAME} · {datetime.now():%m%d-%H%M%S}"})
    ontology = ok(created)
    cities, orders = dataco_objects(csv_path, f"{ontology['id'][:8]}:")
    with SessionLocal() as db:
        for api_name, (name_cn, props) in TYPES.items():
            entity = Entity(ontology_id=ontology["id"], name_cn=name_cn, name_en=api_name, canonical_id=f"ontology:{api_name}", type="EntityType",
                            properties={"property_definitions": [{"id": key, "type": kind} for key, kind in props.items()]})
            db.add(entity); db.flush()
            db.bulk_save_objects([EntityInstance(id=node["id"], entity_id=entity.id, ontology_id=ontology["id"], row_identity=node["id"],
                                                 row_data=dict(node["properties"])) for node in (cities if api_name == "StoreCity" else orders)])
        db.commit()
    graph = FalkorDBService()
    graph.upsert_instances(ontology["id"], cities)
    for start in range(0, len(orders), 5000):
        graph.upsert_instances(ontology["id"], orders[start:start + 5000])
    return ontology["id"], cities, orders


def city_app() -> dict:
    per_city = {"id": "byCity", "kind": "aggregate", "label": "订单（按城市汇总）", "object_type": "Order", "group_by": ["city_id"], "time_field": "order_time",
                "metrics": [{"key": "orders", "op": "count"}, {"key": "sales", "op": "sum", "property": "sales"}, {"key": "late_rate", "op": "avg", "property": "late"}],
                "join": {"object_type": "StoreCity", "on": "city_id", "select": ["city", "state", "latitude", "longitude"]}, "bands": LATE_BANDS}
    return {"schema": "workshop-app/v1", "data_sources": [per_city],
            "variables": [{"id": "level", "label": "迟交等级"}, {"id": "city", "label": "城市"}],
            "settings": {"time_range": "all", "refresh_minutes": 0},
            "pages": [{"id": "home", "title": "门店与迟交分布", "template": "scene_kpi_insight", "widgets": [
                {"id": "map", "type": "geo_map", "title": "门店位置（大小=订单数，颜色=迟交等级）", "source": "byCity", "path": "groups", "region": "scene",
                 "props": {"lat_field": "latitude", "lng_field": "longitude", "label_field": "city", "size_field": "orders", "color_field": "late_level",
                           "key_field": "city_id", "variable": "city", "filter_field": "late_level", "filter_variable": "level"}},
                {"id": "levels", "type": "donut_chart", "title": "城市迟交等级分布", "source": "byCity", "path": "groups", "region": "kpi",
                 "props": {"category_field": "late_level", "center_label": "城市", "variable": "level"}},
                {"id": "byState", "type": "rank_bars", "title": "订单数前 8 州", "source": "byCity", "path": "groups", "region": "insight",
                 "props": {"label_field": "state", "value_field": "orders", "max_rows": 8, "filter_field": "late_level", "filter_variable": "level"}},
                {"id": "topSales", "type": "rank_bars", "title": "销售额前 10 城市", "source": "byCity", "path": "groups", "region": "insight",
                 "props": {"label_field": "city", "value_field": "sales", "max_rows": 10, "format": "money", "filter_field": "late_level", "filter_variable": "level"}}],
                "layout": [{"i": "map", "x": 0, "y": 0, "w": 15, "h": 14}, {"i": "levels", "x": 0, "y": 0, "w": 9, "h": 7},
                           {"i": "byState", "x": 0, "y": 0, "w": 9, "h": 8}, {"i": "topSales", "x": 9, "y": 0, "w": 15, "h": 8}]}]}


def machinery_app() -> dict:
    return {"schema": "workshop-app/v1",
            "data_sources": [{"id": "alerts", "kind": "alerts", "label": "未处理告警", "status": "open"},
                             {"id": "machines", "kind": "object_set", "label": "设备", "object_type": "Machine", "select": ["model", "age"]}],
            "variables": [{"id": "severity", "label": "严重度"}, {"id": "machine", "label": "设备"}],
            "pages": [{"id": "home", "title": "设备告警总览", "widgets": [
                {"id": "bySeverity", "type": "donut_chart", "title": "告警严重度", "source": "alerts", "path": "alerts",
                 "props": {"category_field": "severity", "center_label": "条告警", "variable": "severity"}},
                {"id": "byRule", "type": "rank_bars", "title": "按规则的告警数", "source": "alerts", "path": "alerts",
                 "props": {"label_field": "rule_name", "max_rows": 10, "filter_field": "severity", "filter_variable": "severity"}},
                {"id": "list", "type": "alert_list", "title": "告警（按严重度）", "source": "alerts", "path": "alerts",
                 "props": {"title": "{rule_name}：{subject_id}", "subtitle": "部件 {properties.component}", "severity_field": "severity", "time_field": "window_end",
                           "key_field": "subject_id", "variable": "machine", "max_rows": 100, "filter_field": "severity", "filter_variable": "severity"}},
                {"id": "byModel", "type": "rank_bars", "title": "设备型号分布", "source": "machines", "path": "objects",
                 "props": {"label_field": "model", "max_rows": 10}}],
                "layout": [{"i": "bySeverity", "x": 0, "y": 0, "w": 8, "h": 7}, {"i": "byRule", "x": 8, "y": 0, "w": 8, "h": 7},
                           {"i": "byModel", "x": 16, "y": 0, "w": 8, "h": 7}, {"i": "list", "x": 0, "y": 7, "w": 24, "h": 12}]}]}


def machinery_expected(ontology_id: str) -> dict:
    with SessionLocal() as db:
        alerts = db.query(RuleAlert).filter_by(ontology_id=ontology_id, status="open").all()
        names = dict(db.query(RuleResource.id, RuleResource.display_name).filter_by(ontology_id=ontology_id).all())
        machines = db.query(EntityInstance).join(Entity, EntityInstance.entity_id == Entity.id).filter(
            EntityInstance.ontology_id == ontology_id, Entity.name_en == "Machine").all()
    return {"open_alerts": len(alerts), "by_severity": dict(Counter(row.severity for row in alerts)),
            "by_rule": dict(Counter(names.get(row.rule_id, "") for row in alerts)), "machines": len(machines),
            "by_model": dict(Counter((row.row_data or {}).get("model") for row in machines))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataco", type=Path, required=True)
    parser.add_argument("--api", default="http://backend:8000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.api, timeout=300) as client:
        ontology_id, cities, orders = seed_dataco(client, args.dataco)
        app = ok(client.post(f"/api/v2/ontologies/{ontology_id}/workshop-apps", json={"name": "门店与迟交分布", "definition": city_app()}))
        print(json.dumps({"app": f"/apps/workshop/{app['id']}?ontology={ontology_id}", "cities": len(cities), "orders": len(orders),
                          "first_order": min(row["properties"]["order_time"] for row in orders), "last_order": max(row["properties"]["order_time"] for row in orders)},
                         ensure_ascii=False, indent=1))
        with SessionLocal() as db:
            machinery = db.query(OntologyProject).filter_by(name=MACHINERY_NAME).first()
        if machinery is None:
            print("Machinery ontology not found; run seed_machinery_demo.py first")
            return
        app = ok(client.post(f"/api/v2/ontologies/{machinery.id}/workshop-apps", json={"name": "设备告警总览", "definition": machinery_app()}))
        print(json.dumps({"app": f"/apps/workshop/{app['id']}?ontology={machinery.id}", **machinery_expected(machinery.id)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
