"""Seed two Workshop dashboards that exercise the object-set / alert sources and the map, donut, ranking and alert widgets.

* 门店城市分布 (real data): DataCo orders (CC BY 4.0) grouped by the city where the
  purchase was made.  DataCo's Latitude/Longitude are the store location; each
  city takes the median of its stores' coordinates.  Customer identity columns
  are never read.  Late-delivery level: 高 >= 60 %, 中 50-60 %, 低 < 50 % of the
  city's order lines flagged Late_delivery_risk.
* 设备告警总览: the Azure PdM Machinery ontology from seed_machinery_demo.py
  (100 machines, rule alerts), which must be seeded first.

Bulk objects have no API, so StoreCity objects go through the same SQL + graph
path as the other demo seeders; ontologies and apps go through the HTTP API.
Prints the expected figures, computed straight from the CSV / database, so the
rendered pages can be checked against them.

    python scripts/seed_dashboard_demo.py --dataco /dataco/DataCoSupplyChainDataset.csv --api http://backend:8000
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
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
CITY_PROPERTIES = {"city": "string", "state": "string", "country": "string", "latitude": "double", "longitude": "double",
                   "orders": "double", "sales": "double", "late_rate": "double", "late_level": "string"}


def late_level(rate: float) -> str:
    return "高" if rate >= 0.6 else "中" if rate >= 0.5 else "低"


def store_cities(csv_path: Path) -> list[dict]:
    frame = pd.read_csv(csv_path, encoding="latin-1", usecols=["Customer City", "Customer State", "Customer Country", "Latitude", "Longitude",
                                                              "Order Id", "Sales", "Late_delivery_risk"])
    grouped = frame.groupby(["Customer City", "Customer State", "Customer Country"]).agg(
        latitude=("Latitude", "median"), longitude=("Longitude", "median"), orders=("Order Id", "nunique"),
        sales=("Sales", "sum"), late_rate=("Late_delivery_risk", "mean")).reset_index()
    cities = []
    for row in grouped.itertuples(index=False):
        city, state, country = row[0], row[1], row[2]
        cities.append({"id": f"{city}|{state}", "entity_type": "StoreCity", "properties": {
            "city": city, "state": state, "country": "USA" if country == "EE. UU." else country,
            "latitude": round(float(row.latitude), 5), "longitude": round(float(row.longitude), 5), "orders": float(row.orders),
            "sales": round(float(row.sales), 2), "late_rate": round(float(row.late_rate), 4), "late_level": late_level(float(row.late_rate))}})
    return cities


def ok(response):
    response.raise_for_status()
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


def seed_cities(client: httpx.Client, csv_path: Path) -> tuple[str, list[dict]]:
    cities = store_cities(csv_path)
    body = {"name": CITY_NAME, "domain": "供应链", "description": "DataCo 订单按下单城市汇总：门店位置、订单数、销售额、迟交率（CC BY 4.0）"}
    created = client.post("/api/v1/ontologies", json=body)
    if created.status_code == 409:
        created = client.post("/api/v1/ontologies", json={**body, "name": f"{CITY_NAME} · {datetime.now():%m%d-%H%M%S}"})
    ontology = ok(created)
    with SessionLocal() as db:
        entity = Entity(ontology_id=ontology["id"], name_cn="门店城市", name_en="StoreCity", canonical_id="ontology:StoreCity", type="EntityType",
                        properties={"property_definitions": [{"id": key, "type": kind} for key, kind in CITY_PROPERTIES.items()]})
        db.add(entity); db.flush()
        for node in cities:
            db.add(EntityInstance(id=node["id"], entity_id=entity.id, ontology_id=ontology["id"], row_identity=node["id"], row_data=dict(node["properties"])))
        db.commit()
    FalkorDBService().upsert_instances(ontology["id"], cities)
    return ontology["id"], cities


def city_app() -> dict:
    return {"schema": "workshop-app/v1",
            "data_sources": [{"id": "cities", "kind": "object_set", "label": "门店城市", "object_type": "StoreCity"}],
            "variables": [{"id": "level", "label": "迟交等级"}, {"id": "city", "label": "城市"}],
            "pages": [{"id": "home", "title": "门店与迟交分布", "widgets": [
                {"id": "map", "type": "geo_map", "title": "门店位置（大小=订单数，颜色=迟交等级）", "source": "cities", "path": "objects",
                 "props": {"lat_field": "latitude", "lng_field": "longitude", "label_field": "city", "size_field": "orders", "color_field": "late_level",
                           "key_field": "id", "variable": "city", "filter_field": "late_level", "filter_variable": "level"}},
                {"id": "levels", "type": "donut_chart", "title": "城市迟交等级分布", "source": "cities", "path": "objects",
                 "props": {"category_field": "late_level", "center_label": "城市", "variable": "level"}},
                {"id": "topSales", "type": "rank_bars", "title": "销售额前 10 城市", "source": "cities", "path": "objects",
                 "props": {"label_field": "city", "value_field": "sales", "max_rows": 10, "format": "money", "filter_field": "late_level", "filter_variable": "level"}},
                {"id": "byState", "type": "rank_bars", "title": "订单数前 8 州", "source": "cities", "path": "objects",
                 "props": {"label_field": "state", "value_field": "orders", "max_rows": 8, "filter_field": "late_level", "filter_variable": "level"}}],
                "layout": [{"i": "map", "x": 0, "y": 0, "w": 15, "h": 14}, {"i": "levels", "x": 15, "y": 0, "w": 9, "h": 7},
                           {"i": "byState", "x": 15, "y": 7, "w": 9, "h": 7}, {"i": "topSales", "x": 0, "y": 14, "w": 15, "h": 8}]}]}


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
        ontology_id, cities = seed_cities(client, args.dataco)
        app = ok(client.post(f"/api/v2/ontologies/{ontology_id}/workshop-apps", json={"name": "门店与迟交分布", "definition": city_app()}))
        props = [row["properties"] for row in cities]
        top = sorted(props, key=lambda row: -row["sales"])[:10]
        states = Counter()
        for row in props:
            states[row["state"]] += row["orders"]
        print(json.dumps({"app": f"/apps/workshop/{app['id']}?ontology={ontology_id}", "cities": len(props),
                          "orders": sum(row["orders"] for row in props), "levels": dict(Counter(row["late_level"] for row in props)),
                          "top_sales": [(row["city"], round(row["sales"])) for row in top], "top_states_by_orders": states.most_common(8)},
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
